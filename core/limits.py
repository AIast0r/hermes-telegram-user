from __future__ import annotations

import asyncio
import hashlib
import math
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

_STATE_LOCK = threading.Lock()
_SEMAPHORE_LOCK = threading.Lock()
_TOOL_SEMAPHORE: Optional[threading.BoundedSemaphore] = None
_TOOL_SEMAPHORE_SIZE: Optional[int] = None
_NEXT_TOOL_START = 0.0
_FLOOD_UNTIL = 0.0

_GATEWAY_LOCK_HANDLE = None
_GATEWAY_LOCK_PATH: Optional[Path] = None


class TelegramRateLimited(RuntimeError):
    def __init__(self, seconds: int):
        self.seconds = max(1, int(seconds))
        super().__init__(
            f"Telegram rate limit is active. Retry after {self.seconds} seconds; "
            "do not retry earlier."
        )


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(value, high))


def configured_flood_sleep_threshold() -> int:
    """Seconds Telethon may silently sleep for a small FloodWait."""
    return _env_int("HERMES_TG_USER_FLOOD_SLEEP_THRESHOLD", 30, 0, 300)


def configured_max_concurrent_tools() -> int:
    return _env_int("HERMES_TG_USER_MAX_CONCURRENT_TOOLS", 3, 1, 16)


def configured_tool_min_interval_seconds() -> float:
    ms = _env_int("HERMES_TG_USER_TOOL_MIN_INTERVAL_MS", 150, 0, 10000)
    return ms / 1000.0


def _get_tool_semaphore() -> threading.BoundedSemaphore:
    global _TOOL_SEMAPHORE, _TOOL_SEMAPHORE_SIZE
    with _SEMAPHORE_LOCK:
        if _TOOL_SEMAPHORE is None:
            _TOOL_SEMAPHORE_SIZE = configured_max_concurrent_tools()
            _TOOL_SEMAPHORE = threading.BoundedSemaphore(_TOOL_SEMAPHORE_SIZE)
        return _TOOL_SEMAPHORE


def note_flood_wait(seconds: int) -> int:
    """Block new Telegram tool calls until Telegram's wait period expires."""
    global _FLOOD_UNTIL
    wait = max(1, int(seconds))
    deadline = time.monotonic() + wait
    with _STATE_LOCK:
        _FLOOD_UNTIL = max(_FLOOD_UNTIL, deadline)
    return wait


def remaining_flood_wait() -> int:
    with _STATE_LOCK:
        remaining = _FLOOD_UNTIL - time.monotonic()
    return max(0, int(math.ceil(remaining)))


def _flood_seconds_from_exception(exc: BaseException) -> Optional[int]:
    name = type(exc).__name__.lower()
    if "floodwait" not in name and "floodpremiumwait" not in name:
        return None
    raw = getattr(exc, "seconds", None)
    if raw is None:
        raw = getattr(exc, "value", None)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 60


def telegram_error_message(exc: BaseException) -> str:
    """Turn FloodWait into actionable agent-facing backoff instructions."""
    seconds = _flood_seconds_from_exception(exc)
    if seconds is not None:
        note_flood_wait(seconds)
        return (
            f"Telegram rate limited this session for {seconds} seconds. "
            f"Retry only after {seconds} seconds; do not retry immediately."
        )
    text = str(exc).strip()
    if not text or text.startswith("(caused by"):
        # Some RPC failures arrive with no message at all — sometimes as nothing but
        # a ``(caused by <Request>)`` suffix. Passed through verbatim that reaches the
        # model as an empty error, and an empty error it cannot act on is one it
        # retries: the same call, over and over. Name the failure instead.
        cause = exc.__cause__ or exc.__context__
        detail = text.strip("() ") or (
            type(cause).__name__ if cause is not None else type(exc).__name__
        )
        return f"Telegram returned no error text for this call ({detail})"
    return text


@asynccontextmanager
async def tool_gate() -> AsyncIterator[None]:
    """Bound concurrent tool connections and pace their starts across worker loops."""
    blocked = remaining_flood_wait()
    if blocked:
        raise TelegramRateLimited(blocked)

    semaphore = _get_tool_semaphore()
    await asyncio.to_thread(semaphore.acquire)
    try:
        blocked = remaining_flood_wait()
        if blocked:
            raise TelegramRateLimited(blocked)

        interval = configured_tool_min_interval_seconds()
        global _NEXT_TOOL_START
        with _STATE_LOCK:
            now = time.monotonic()
            start_at = max(now, _NEXT_TOOL_START)
            _NEXT_TOOL_START = start_at + interval
        delay = start_at - now
        if delay > 0:
            await asyncio.sleep(delay)

        blocked = remaining_flood_wait()
        if blocked:
            raise TelegramRateLimited(blocked)
        yield
    finally:
        semaphore.release()


def _lock_path(session: str) -> Path:
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"hermes-telegram-user-{digest}.lock"


def acquire_gateway_session_lock(session: str) -> Path:
    """Ensure only one long-lived gateway listener uses this session per machine."""
    global _GATEWAY_LOCK_HANDLE, _GATEWAY_LOCK_PATH
    if _GATEWAY_LOCK_HANDLE is not None:
        return _GATEWAY_LOCK_PATH  # type: ignore[return-value]

    path = _lock_path(session)
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError(
            "Another hermes-telegram-user gateway instance is already using this Telegram session"
        ) from exc

    _GATEWAY_LOCK_HANDLE = handle
    _GATEWAY_LOCK_PATH = path
    return path


def release_gateway_session_lock() -> None:
    global _GATEWAY_LOCK_HANDLE, _GATEWAY_LOCK_PATH
    handle = _GATEWAY_LOCK_HANDLE
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        handle.close()
    finally:
        _GATEWAY_LOCK_HANDLE = None
        _GATEWAY_LOCK_PATH = None


def rate_limit_snapshot() -> dict[str, int | float]:
    """Small diagnostic surface used by tests/logging, not a model tool."""
    return {
        "remaining_flood_wait": remaining_flood_wait(),
        "max_concurrent_tools": configured_max_concurrent_tools(),
        "min_interval_ms": int(configured_tool_min_interval_seconds() * 1000),
        "flood_sleep_threshold": configured_flood_sleep_threshold(),
    }
