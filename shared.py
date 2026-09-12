from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

# The gateway adapter owns this long-lived client. Tool calls intentionally do NOT
# reuse it: current Hermes may execute async tool handlers on a fresh worker event
# loop, and Telethon clients must stay on the event loop they were connected on.
_client = None
_client_lock = asyncio.Lock()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def credentials() -> tuple[int, str, str]:
    api_id_raw = _env("HERMES_TG_USER_API_ID")
    api_hash = _env("HERMES_TG_USER_API_HASH")
    session = _env("HERMES_TG_USER_SESSION")
    if not (api_id_raw and api_hash and session):
        raise RuntimeError(
            "Missing HERMES_TG_USER_API_ID / HERMES_TG_USER_API_HASH / HERMES_TG_USER_SESSION"
        )
    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("HERMES_TG_USER_API_ID must be an integer") from exc
    return api_id, api_hash, session


def _new_client():
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("Telethon is not installed: pip install telethon") from exc

    api_id, api_hash, session = credentials()
    return TelegramClient(StringSession(session), api_id, api_hash)


async def _connect_authorized(client):
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Telegram StringSession is not authorized")
    return client


async def get_client():
    """Return the long-lived gateway client bound to the gateway event loop."""
    global _client
    if _client is not None and _client.is_connected():
        return _client
    async with _client_lock:
        if _client is not None and _client.is_connected():
            return _client
        _client = await _connect_authorized(_new_client())
        return _client


@asynccontextmanager
async def tool_client() -> AsyncIterator[Any]:
    """Create a loop-local client for one Hermes tool invocation.

    Hermes' async tool bridge can run each call on a fresh event loop/thread. A
    Telethon client connected by the gateway cannot safely be awaited there, so
    read-only tools use a short-lived connection and always disconnect it before
    Hermes tears the worker loop down.
    """
    client = _new_client()
    try:
        await _connect_authorized(client)
        yield client
    finally:
        with suppress(Exception):
            await client.disconnect()


async def disconnect_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.disconnect()
        finally:
            _client = None


def utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def entity_label(entity: Any) -> str:
    for attr in ("title", "username", "first_name"):
        value = getattr(entity, attr, None)
        if value:
            return str(value)
    return str(getattr(entity, "id", "unknown"))
