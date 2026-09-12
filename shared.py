from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Optional

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


async def get_client():
    global _client
    if _client is not None and _client.is_connected():
        return _client
    async with _client_lock:
        if _client is not None and _client.is_connected():
            return _client
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed: pip install telethon") from exc
        api_id, api_hash, session = credentials()
        client = TelegramClient(StringSession(session), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError("Telegram StringSession is not authorized")
        _client = client
        return _client


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
