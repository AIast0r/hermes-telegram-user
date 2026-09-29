"""Digest watermarks: how far each chat has been digested, and where the gap is.

A digest that re-reads the whole chat every time is useless, and one that
trusts a single number is worse than useless: it silently drops messages.
So a mark here is *two* numbers, not one.

``contiguous``
    The highest message id such that everything at or below it has been
    digested. It is a floor, never a ceiling: a fetch that wants what is new
    asks for ids strictly above it, and never re-reads an id at or below it.

``pending_from_id`` / ``pending_top_id``
    The two ends of a *hole* — the range left over when a run stopped early
    (budget, a truncated page, a crash). ``pending_from_id`` is the lowest id
    that run reached and therefore where the next run carries on *from*;
    ``pending_top_id`` is the highest id ever seen while the hole has been
    open, and is the id that becomes ``contiguous`` once the hole closes. The
    mark does not move while a hole is open, because moving it across a gap
    makes every message inside the gap unreachable forever.

The four failure modes of the design this replaces
(``other/tgai/tgai/storage.py`` + ``commands/aggregate.py``) are each closed
here explicitly:

* **keyed by peer id, not by display name.** A name is mutable and not unique;
  re-keying a mark on rename either loses it or, worse, hands one chat's mark
  to another. Keys are the canonical numeric id produced by
  ``telethon.utils.get_peer_id`` — the same key space as ``aliases.json``'s
  ``peer_id`` and ``transcripts.sqlite3``'s ``chat_id``.
* **the mark advances only on proof of contiguity.** ``advance`` can never move
  ``contiguous`` backwards, and a report that does not reach down to the
  current mark leaves the mark alone instead of jumping it.
* **a truncated page records a hole instead of being skipped.** A run that
  stopped short leaves ``pending_from_id``/``pending_top_id`` behind, and
  :func:`resume_bounds` hands the next fetch the cursors that close it first.
* **atomic and locked.** The file is written tmp-file → ``0600`` → ``os.replace``,
  every mutation holds a module lock, and :func:`watermark_lock` serialises the
  read-fetch-write cycles of one chat.

None of this touches Telegram: a watermark is written locally and a read
pointer is never acknowledged, so a digest can be repeated at no cost.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from ..sanitize import sanitize_name
from .paths import private_file, state_dir

__all__ = [
    "advance",
    "forget_all",
    "forget_mark",
    "get_mark",
    "list_marks",
    "resume_bounds",
    "watermark_lock",
]

_FILENAME = "digest_watermarks.json"
_VERSION = 1
#: Telegram ids are int32 on the wire; anything larger is a caller bug, and a
#: mark that is too large would answer "nothing is new" about everything.
_MAX_MESSAGE_ID = 2**31 - 1
#: Long enough for the longest canonical id, short enough that a path-looking
#: or pasted string is rejected instead of becoming a key.
_MAX_PEER_ID_LEN = 40

_LOCK = threading.RLock()
_PEER_LOCKS_LOCK = threading.Lock()
_PEER_LOCKS: dict[str, threading.Lock] = {}
_cache: Optional[dict[str, dict[str, Any]]] = None


def _path() -> Path:
    return state_dir() / _FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _peer_key(peer_id: str | int) -> str:
    """Canonical numeric peer id, or ``ValueError``.

    Sanitising first and parsing second is what stops a display name, a t.me
    link or an entity repr from ever becoming a key: it would parse as nothing
    and be refused rather than stored under a key no other module uses.
    """
    raw = sanitize_name(str(peer_id), limit=_MAX_PEER_ID_LEN).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("peer_id must be a numeric Telegram id") from None
    if value == 0:
        raise ValueError("peer_id is required")
    return str(value)


def _message_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a message id")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a message id") from None
    if parsed < 0 or parsed > _MAX_MESSAGE_ID:
        raise ValueError(f"{field} is out of range")
    return parsed


def _coerce_stored_id(value: Any) -> Optional[int]:
    """Best-effort read of an id that came off disk; ``None`` if unusable."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0 or parsed > _MAX_MESSAGE_ID:
        return None
    return parsed


def _normalize_mark(raw: Any) -> Optional[dict[str, Any]]:
    """Repair one stored mark, or drop it.

    A row is only kept when it is internally consistent, and an inconsistent
    hole is dropped rather than guessed at: a dropped hole costs one re-read
    from the top, while a guessed one can jump the mark across messages that
    were never digested.
    """
    if not isinstance(raw, dict):
        return None
    contiguous = _coerce_stored_id(raw.get("contiguous"))
    if contiguous is None:
        return None
    pending_from = _coerce_stored_id(raw.get("pending_from_id"))
    pending_top = _coerce_stored_id(raw.get("pending_top_id"))
    # The hole is the open range (contiguous, pending_from): it exists only when
    # at least one id sits inside it, and only when both ends are known.
    if (
        pending_from is None
        or pending_top is None
        or pending_from < contiguous + 2
        or pending_top < pending_from
    ):
        pending_from = None
        pending_top = None
    updated_at = raw.get("updated_at")
    return {
        "contiguous": contiguous,
        "pending_from_id": pending_from,
        "pending_top_id": pending_top,
        "updated_at": sanitize_name(updated_at, limit=64) if isinstance(updated_at, str) else None,
    }


def _empty_mark() -> dict[str, Any]:
    return {"contiguous": 0, "pending_from_id": None, "pending_top_id": None, "updated_at": None}


def _load_unlocked() -> dict[str, dict[str, Any]]:
    """Marks as currently known; a missing or corrupt file means "no marks"."""
    global _cache
    if _cache is not None:
        return _cache
    loaded: dict[str, dict[str, Any]] = {}
    path = _path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, dict) and raw.get("version", _VERSION) == _VERSION:
        stored = raw.get("marks")
        if isinstance(stored, dict):
            for key, value in stored.items():
                canonical = _coerce_stored_id(key)
                mark = _normalize_mark(value)
                if canonical is None or mark is None:
                    continue
                loaded[str(canonical)] = mark
    _cache = loaded
    return _cache


def _save_unlocked(marks: dict[str, dict[str, Any]]) -> None:
    global _cache
    path = _path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps({"version": _VERSION, "marks": marks}, ensure_ascii=False, indent=2)
    tmp.write_text(payload + "\n", encoding="utf-8")
    private_file(tmp)
    os.replace(tmp, path)
    private_file(path)
    _cache = marks


def _row(peer_id: str, mark: dict[str, Any]) -> dict[str, Any]:
    return {
        "peer_id": peer_id,
        "contiguous": mark["contiguous"],
        "pending_from_id": mark["pending_from_id"],
        "pending_top_id": mark["pending_top_id"],
        "has_hole": mark["pending_from_id"] is not None,
        "updated_at": mark["updated_at"],
    }


def _peer_lock(peer_id: str | int) -> threading.Lock:
    key = _peer_key(peer_id)
    with _PEER_LOCKS_LOCK:
        lock = _PEER_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PEER_LOCKS[key] = lock
        return lock


@asynccontextmanager
async def watermark_lock(peer_id: str | int) -> AsyncIterator[None]:
    """Serialise one chat's read-fetch-advance cycle.

    Prevents two concurrent digests of the same chat from both reading the same
    ``min_id``, both fetching the same window and both advancing the mark: the
    loser would report messages the winner already digested, and a hole it
    opens could be closed by the wrong run. The lock is per peer, so digests of
    different chats still run concurrently.
    """
    lock = _peer_lock(peer_id)
    await asyncio.to_thread(lock.acquire)
    try:
        yield
    finally:
        lock.release()


def get_mark(peer_id: str | int) -> Optional[dict[str, Any]]:
    """One chat's mark, or ``None`` when that chat has never been digested.

    Prevents a caller from inventing a default mark (typically "everything so
    far") for a chat that has no mark at all: the distinction between "nothing
    digested yet" and "digested up to id N" is the whole reason the file exists,
    so its absence is reported rather than papered over.
    """
    key = _peer_key(peer_id)
    with _LOCK:
        mark = _load_unlocked().get(key)
        return _row(key, mark) if mark is not None else None


def list_marks() -> list[dict[str, Any]]:
    """Every stored mark, ordered by numeric peer id.

    Prevents a caller from reporting on "all chats" by walking an unordered
    dict: the order is fixed here so two runs with the same state produce the
    same list, which is what makes a diff of two digests readable.
    """
    with _LOCK:
        rows = [_row(key, mark) for key, mark in _load_unlocked().items()]
    rows.sort(key=lambda row: int(row["peer_id"]))
    return rows


def advance(peer_id: str | int, *, contiguous: int, top: int | None = None) -> dict[str, Any]:
    """Record the outcome of one walk over a chat and return the resulting mark.

    ``contiguous`` is the lowest id the walk reached; ``top`` is the highest id
    it saw. With ``top=None`` the walk reached the floor it aimed at and
    ``contiguous`` is the highest id it covered — that is the only way the mark
    moves forward. With ``top`` given the walk stopped short, so the range
    between the mark and ``contiguous`` is a hole: the mark stays put, and both
    ends are recorded for :func:`resume_bounds` (an open hole only ever grows;
    it is cleared by a walk that reaches the mark, or by ``top=None``).

    Prevents the two failures of a single-number mark: moving it backwards
    (a shorter walk being mistaken for an older state) and jumping it across a
    limit-truncated page, which would leave the messages under the jump
    undigested and unreachable. A no-op report writes nothing, so ``updated_at``
    still means "when the mark last changed".
    """
    key = _peer_key(peer_id)
    low = _message_id(contiguous, field="contiguous")
    high = None if top is None else _message_id(top, field="top")
    if high is not None and high < low:
        raise ValueError("top must not be below contiguous")
    with _LOCK:
        marks = dict(_load_unlocked())
        mark = dict(marks.get(key) or _empty_mark())
        before = (mark["contiguous"], mark["pending_from_id"], mark["pending_top_id"])
        pending_from = mark["pending_from_id"]
        pending_top = mark["pending_top_id"]
        # Telegram's cursors are exclusive, so a walk that stopped at id
        # `mark + 1` has nothing left between it and the mark: the hole is empty
        # and the walk counts as having reached the floor.
        if high is not None and low >= mark["contiguous"] + 2:
            mark["pending_from_id"] = low if pending_from is None else min(pending_from, low)
            mark["pending_top_id"] = high if pending_top is None else max(pending_top, high)
        else:
            mark["contiguous"] = max(mark["contiguous"], pending_top or 0, high or 0, low)
            mark["pending_from_id"] = None
            mark["pending_top_id"] = None
        after = (mark["contiguous"], mark["pending_from_id"], mark["pending_top_id"])
        if after != before:
            mark["updated_at"] = _now()
            marks[key] = mark
            _save_unlocked(marks)
        return _row(key, mark)


def resume_bounds(peer_id: str | int) -> dict[str, Any]:
    """The cursors a fetch should use, closing a pending hole before anything new.

    ``min_id`` is the mark (the exclusive floor: digested ids are never
    re-read), ``max_id`` is where an interrupted run stopped, or ``None`` for a
    walk that should start from the newest message. ``has_hole`` says which of
    the two cases the caller is in. Closing the hole first is what prevents the
    hole from being refilled by newer traffic forever: every run that starts at
    the top without it re-reads the same newest page and the old messages are
    never reached.
    """
    key = _peer_key(peer_id)
    with _LOCK:
        mark = _load_unlocked().get(key)
        mark = dict(mark) if mark is not None else _empty_mark()
    pending_from = mark["pending_from_id"]
    return {
        "peer_id": key,
        "min_id": mark["contiguous"],
        "max_id": pending_from,
        "has_hole": pending_from is not None,
        "contiguous": mark["contiguous"],
        "pending_from_id": pending_from,
        "pending_top_id": mark["pending_top_id"],
    }


def forget_mark(peer_id: str | int) -> bool:
    """Drop one chat's mark; ``True`` if it existed.

    Prevents a stale mark from outliving the chat it describes — a mark left
    behind by a mis-keyed or deleted target would otherwise make the next
    digest of a chat that reuses the id answer "nothing is new".
    """
    key = _peer_key(peer_id)
    with _LOCK:
        marks = dict(_load_unlocked())
        existed = marks.pop(key, None) is not None
        if existed:
            _save_unlocked(marks)
    return existed


def forget_all() -> int:
    """Drop every mark and return how many were dropped.

    Prevents a wholesale reset from being a partial one: the count returned is
    the number of chats that were actually forgotten, so a caller can tell an
    empty store from a failed clear.
    """
    with _LOCK:
        marks = dict(_load_unlocked())
        count = len(marks)
        if count:
            _save_unlocked({})
    return count
