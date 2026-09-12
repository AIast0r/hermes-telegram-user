from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .shared import credentials, entity_label, tool_client, utc_iso


def _json(payload: dict[str, Any]) -> str:
    """Hermes tool handlers return text; keep structured Telegram results as JSON text."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _check_requirements() -> bool:
    """Passive availability probe used by the Hermes tool registry."""
    try:
        import telethon  # noqa: F401
        credentials()
    except Exception:
        return False
    return True


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip()
    if s.lower() == "today":
        now = datetime.now().astimezone()
        return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if s.lower() == "yesterday":
        now = datetime.now().astimezone() - timedelta(days=1)
        return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


async def _resolve_chat(client, chat: str):
    raw = str(chat).strip()
    if not raw:
        raise ValueError("chat is required")
    if raw.lstrip("-").isdigit():
        return await client.get_entity(int(raw))
    try:
        return await client.get_entity(raw)
    except Exception:
        needle = raw.casefold()
        async for dialog in client.iter_dialogs():
            name = (dialog.name or "").casefold()
            username = (getattr(dialog.entity, "username", None) or "").casefold()
            if needle == name or needle == username or needle in name:
                return dialog.entity
        raise ValueError(f"Telegram chat not found: {chat}")


async def _find_topic_root(client, entity, topic: str | int):
    raw = str(topic).strip()
    if raw.isdigit():
        return int(raw)
    needle = raw.casefold()
    try:
        from telethon.tl.functions.channels import GetForumTopicsRequest

        result = await client(
            GetForumTopicsRequest(
                channel=entity,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=100,
                q=raw,
            )
        )
        for item in result.topics:
            title = (getattr(item, "title", None) or "").casefold()
            if title == needle or needle in title:
                return int(item.id)
    except Exception as exc:
        raise ValueError(f"Could not resolve topic {topic!r}") from exc
    raise ValueError(f"Telegram topic not found: {topic}")


def _message_to_dict(m: Any) -> dict[str, Any]:
    sender = getattr(m, "sender", None)
    return {
        "id": int(m.id),
        "date": utc_iso(getattr(m, "date", None)),
        "sender_id": str(getattr(m, "sender_id", "") or ""),
        "sender": entity_label(sender) if sender else None,
        "text": getattr(m, "message", None) or "",
        "reply_to_msg_id": getattr(getattr(m, "reply_to", None), "reply_to_msg_id", None),
    }


async def _tg_find_chat(args: dict[str, Any], **_: Any) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return _json({"error": "query is required"})
    limit = max(1, min(int(args.get("limit") or 20), 100))
    try:
        async with tool_client() as client:
            needle = query.casefold()
            out = []
            async for d in client.iter_dialogs():
                name = d.name or ""
                username = getattr(d.entity, "username", None)
                if needle in name.casefold() or (username and needle in username.casefold()):
                    out.append(
                        {
                            "id": str(d.id),
                            "name": name,
                            "username": username,
                            "is_group": bool(d.is_group),
                            "is_channel": bool(d.is_channel),
                            "is_user": bool(d.is_user),
                        }
                    )
                    if len(out) >= limit:
                        break
            return _json({"chats": out})
    except Exception as exc:
        return _json({"error": str(exc)})


async def _tg_list_topics(args: dict[str, Any], **_: Any) -> str:
    chat = str(args.get("chat") or "").strip()
    if not chat:
        return _json({"error": "chat is required"})
    limit = max(1, min(int(args.get("limit") or 100), 100))
    try:
        async with tool_client() as client:
            entity = await _resolve_chat(client, chat)
            from telethon.tl.functions.channels import GetForumTopicsRequest

            result = await client(
                GetForumTopicsRequest(
                    channel=entity,
                    offset_date=None,
                    offset_id=0,
                    offset_topic=0,
                    limit=limit,
                    q="",
                )
            )
            topics = [
                {
                    "id": int(t.id),
                    "title": getattr(t, "title", ""),
                    "closed": bool(getattr(t, "closed", False)),
                }
                for t in result.topics
            ]
            return _json({"chat": entity_label(entity), "topics": topics})
    except Exception as exc:
        return _json({"error": str(exc)})


async def _tg_read_messages(args: dict[str, Any], **_: Any) -> str:
    chat = str(args.get("chat") or "").strip()
    if not chat:
        return _json({"error": "chat is required"})
    try:
        since = _parse_dt(args.get("since"))
        until = _parse_dt(args.get("until"))
        topic = args.get("topic")
        limit = max(1, min(int(args.get("limit") or 200), 1000))
        async with tool_client() as client:
            entity = await _resolve_chat(client, chat)
            reply_to = (
                await _find_topic_root(client, entity, topic)
                if topic not in (None, "")
                else None
            )
            rows = []
            kwargs: dict[str, Any] = {"limit": limit}
            if reply_to is not None:
                kwargs["reply_to"] = reply_to
            async for m in client.iter_messages(entity, **kwargs):
                dt = getattr(m, "date", None)
                if dt and until and dt >= until:
                    continue
                if dt and since and dt < since:
                    break
                rows.append(_message_to_dict(m))
            rows.reverse()
            return _json(
                {
                    "chat": entity_label(entity),
                    "topic": topic,
                    "count": len(rows),
                    "messages": rows,
                }
            )
    except Exception as exc:
        return _json({"error": str(exc)})


async def _tg_search_messages(args: dict[str, Any], **_: Any) -> str:
    chat = str(args.get("chat") or "").strip()
    query = str(args.get("query") or "").strip()
    if not chat:
        return _json({"error": "chat is required"})
    if not query:
        return _json({"error": "query is required"})
    try:
        since = _parse_dt(args.get("since"))
        until = _parse_dt(args.get("until"))
        topic = args.get("topic")
        limit = max(1, min(int(args.get("limit") or 100), 500))
        async with tool_client() as client:
            entity = await _resolve_chat(client, chat)
            reply_to = (
                await _find_topic_root(client, entity, topic)
                if topic not in (None, "")
                else None
            )
            kwargs: dict[str, Any] = {"search": query, "limit": limit}
            if reply_to is not None:
                kwargs["reply_to"] = reply_to
            rows = []
            async for m in client.iter_messages(entity, **kwargs):
                dt = getattr(m, "date", None)
                if dt and until and dt >= until:
                    continue
                if dt and since and dt < since:
                    break
                rows.append(_message_to_dict(m))
            rows.reverse()
            return _json(
                {
                    "chat": entity_label(entity),
                    "topic": topic,
                    "query": query,
                    "count": len(rows),
                    "messages": rows,
                }
            )
    except Exception as exc:
        return _json({"error": str(exc)})


_REQUIRED_ENV = [
    "HERMES_TG_USER_API_ID",
    "HERMES_TG_USER_API_HASH",
    "HERMES_TG_USER_SESSION",
]

_TOOL_DEFS = [
    (
        "tg_find_chat",
        "Find Telegram dialogs available to the connected user account by title or username.",
        _tg_find_chat,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Chat title, partial title, or username."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
    ),
    (
        "tg_list_topics",
        "List forum topics in a Telegram supergroup/channel.",
        _tg_list_topics,
        {
            "type": "object",
            "properties": {
                "chat": {"type": "string", "description": "Chat id, title, or username."},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 100},
            },
            "required": ["chat"],
        },
    ),
    (
        "tg_read_messages",
        "Read recent Telegram messages from a chat, optionally restricted to a forum topic and time window.",
        _tg_read_messages,
        {
            "type": "object",
            "properties": {
                "chat": {"type": "string", "description": "Chat id, title, or username."},
                "topic": {"type": "string", "description": "Optional forum topic title or numeric topic id as text."},
                "since": {"type": "string", "description": "ISO datetime, 'today', or 'yesterday'."},
                "until": {"type": "string", "description": "Optional exclusive ISO datetime upper bound."},
                "limit": {"type": "integer", "default": 200, "minimum": 1, "maximum": 1000},
            },
            "required": ["chat"],
        },
    ),
    (
        "tg_search_messages",
        "Search message text in a Telegram chat, optionally restricted to a forum topic and time window.",
        _tg_search_messages,
        {
            "type": "object",
            "properties": {
                "chat": {"type": "string", "description": "Chat id, title, or username."},
                "query": {"type": "string", "description": "Telegram message search query."},
                "topic": {"type": "string", "description": "Optional forum topic title or numeric topic id as text."},
                "since": {"type": "string", "description": "Optional ISO datetime, 'today', or 'yesterday'."},
                "until": {"type": "string", "description": "Optional exclusive ISO datetime upper bound."},
                "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
            },
            "required": ["chat", "query"],
        },
    ),
]


def register_tools(ctx) -> None:
    """Register deferred client tools using the current Hermes tool contract."""
    for name, description, handler, parameters in _TOOL_DEFS:
        ctx.register_tool(
            name=name,
            toolset="telegram_user",
            schema={"name": name, "description": description, "parameters": parameters},
            handler=handler,
            check_fn=_check_requirements,
            requires_env=_REQUIRED_ENV,
            is_async=True,
            description=description,
            emoji="🟦",
        )
