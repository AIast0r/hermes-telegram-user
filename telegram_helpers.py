from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .shared import entity_label, utc_iso
from .telegram_media import media_info


def parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip()
    now = datetime.now().astimezone()
    if s.lower() == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if s.lower() == "yesterday":
        day = now - timedelta(days=1)
        return day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def clean_text(value: Any, *, limit: int = 12000) -> str:
    text = str(value or "")
    # JSON already escapes controls, but stripping non-whitespace C0 controls
    # keeps tool output safe for terminals/loggers too.
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    return text if len(text) <= limit else text[:limit] + "…"


def person_name(entity: Any) -> str:
    parts = [
        str(x).strip()
        for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None))
        if x
    ]
    return clean_text(" ".join(parts) or entity_label(entity), limit=256)


def message_to_dict(message: Any, *, chat: Any = None) -> dict[str, Any]:
    sender = getattr(message, "sender", None)
    reply = getattr(message, "reply_to", None)
    row: dict[str, Any] = {
        "id": int(message.id),
        "date": utc_iso(getattr(message, "date", None)),
        "sender_id": str(getattr(message, "sender_id", "") or ""),
        "sender": clean_text(entity_label(sender), limit=256) if sender else None,
        "text": clean_text(getattr(message, "message", None) or ""),
        "out": bool(getattr(message, "out", False)),
        "reply_to_msg_id": getattr(reply, "reply_to_msg_id", None),
        "topic_id": getattr(reply, "reply_to_top_id", None),
    }
    username = getattr(sender, "username", None) if sender else None
    if username:
        row["sender_username"] = clean_text(username, limit=128)
    attachment = media_info(message)
    if attachment:
        row["media"] = attachment
    if chat is not None:
        try:
            from telethon import utils

            row["chat_id"] = str(utils.get_peer_id(chat))
        except Exception:
            row["chat_id"] = str(getattr(chat, "id", "") or "")
        row["chat"] = clean_text(entity_label(chat), limit=256)
        chat_username = getattr(chat, "username", None)
        if chat_username:
            row["chat_username"] = clean_text(chat_username, limit=128)
    return row


async def resolve_chat(client: Any, chat: str):
    raw = str(chat).strip()
    if not raw:
        raise ValueError("chat is required")
    if raw.lstrip("-").isdigit():
        return await client.get_entity(int(raw))
    try:
        return await client.get_entity(raw)
    except Exception:
        needle = raw.lstrip("@").casefold()
        exact = []
        partial = []
        async for dialog in client.iter_dialogs():
            name = (dialog.name or "").casefold()
            username = (getattr(dialog.entity, "username", None) or "").casefold()
            if needle in {name, username}:
                exact.append(dialog.entity)
            elif needle in name or (username and needle in username):
                partial.append(dialog.entity)
        choices = exact or partial
        if len(choices) == 1:
            return choices[0]
        if len(choices) > 1:
            raise ValueError(f"Telegram chat is ambiguous: {chat}")
        raise ValueError(f"Telegram chat not found: {chat}")


async def find_topic_root(client: Any, entity: Any, topic: str | int):
    raw = str(topic).strip()
    if raw.isdigit():
        return int(raw)
    needle = raw.casefold()
    try:
        from telethon.tl.functions.messages import GetForumTopicsRequest

        result = await client(
            GetForumTopicsRequest(
                peer=entity,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=100,
                q=raw,
            )
        )
    except Exception as exc:
        raise ValueError(f"Could not resolve topic {topic!r}") from exc
    matches = []
    for item in result.topics:
        title = (getattr(item, "title", None) or "").casefold()
        if title == needle:
            return int(item.id)
        if needle in title:
            matches.append(item)
    if len(matches) == 1:
        return int(matches[0].id)
    if len(matches) > 1:
        raise ValueError(f"Telegram topic is ambiguous: {topic}")
    raise ValueError(f"Telegram topic not found: {topic}")


async def message_chat(message: Any):
    chat = getattr(message, "chat", None)
    if chat is not None:
        return chat
    try:
        return await message.get_chat()
    except Exception:
        return None


def media_filter(kind: str):
    normalized = (kind or "any").strip().lower()
    if normalized in {"", "any", "media"}:
        return None
    from telethon.tl import types

    names = {
        "photo": "InputMessagesFilterPhotos",
        "image": "InputMessagesFilterPhotos",
        "video": "InputMessagesFilterVideo",
        "voice": "InputMessagesFilterVoice",
        "audio": "InputMessagesFilterMusic",
        "music": "InputMessagesFilterMusic",
        "document": "InputMessagesFilterDocument",
        "file": "InputMessagesFilterDocument",
        "gif": "InputMessagesFilterGif",
        "video_note": "InputMessagesFilterRoundVideo",
    }
    cls_name = names.get(normalized)
    if cls_name is None:
        raise ValueError(f"unsupported media kind: {kind}")
    cls = getattr(types, cls_name, None)
    if cls is None:
        raise ValueError(f"media filter is unavailable in this Telethon version: {kind}")
    return cls()


def configured_media_limit_bytes() -> int:
    raw = (os.getenv("HERMES_TG_USER_MAX_MEDIA_MB") or "50").strip()
    try:
        mb = max(1, min(int(raw), 2048))
    except ValueError:
        mb = 50
    return mb * 1024 * 1024
