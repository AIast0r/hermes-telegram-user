from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .shared import entity_label, get_client, utc_iso


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
    if str(topic).isdigit():
        return int(topic)
    needle = str(topic).strip().casefold()
    try:
        from telethon.tl.functions.channels import GetForumTopicsRequest
        result = await client(GetForumTopicsRequest(
            channel=entity, offset_date=None, offset_id=0, offset_topic=0,
            limit=100, q=str(topic),
        ))
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


async def _tg_find_chat(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    client = await get_client()
    needle = query.casefold()
    out = []
    async for d in client.iter_dialogs():
        name = d.name or ""
        username = getattr(d.entity, "username", None)
        if needle in name.casefold() or (username and needle in username.casefold()):
            out.append({
                "id": str(d.id), "name": name, "username": username,
                "is_group": bool(d.is_group), "is_channel": bool(d.is_channel), "is_user": bool(d.is_user),
            })
            if len(out) >= int(args.get("limit") or 20):
                break
    return {"chats": out}


async def _tg_list_topics(args: dict[str, Any]) -> dict[str, Any]:
    client = await get_client()
    entity = await _resolve_chat(client, args.get("chat"))
    from telethon.tl.functions.channels import GetForumTopicsRequest
    result = await client(GetForumTopicsRequest(
        channel=entity, offset_date=None, offset_id=0, offset_topic=0,
        limit=min(int(args.get("limit") or 100), 100), q="",
    ))
    topics = []
    for t in result.topics:
        topics.append({"id": int(t.id), "title": getattr(t, "title", ""), "closed": bool(getattr(t, "closed", False))})
    return {"chat": entity_label(entity), "topics": topics}


async def _tg_read_messages(args: dict[str, Any]) -> dict[str, Any]:
    client = await get_client()
    entity = await _resolve_chat(client, args.get("chat"))
    since = _parse_dt(args.get("since"))
    until = _parse_dt(args.get("until"))
    topic = args.get("topic")
    reply_to = await _find_topic_root(client, entity, topic) if topic not in (None, "") else None
    limit = max(1, min(int(args.get("limit") or 200), 1000))
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
    return {"chat": entity_label(entity), "topic": topic, "count": len(rows), "messages": rows}


async def _tg_search_messages(args: dict[str, Any]) -> dict[str, Any]:
    client = await get_client()
    entity = await _resolve_chat(client, args.get("chat"))
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    since = _parse_dt(args.get("since"))
    until = _parse_dt(args.get("until"))
    topic = args.get("topic")
    reply_to = await _find_topic_root(client, entity, topic) if topic not in (None, "") else None
    limit = max(1, min(int(args.get("limit") or 100), 500))
    kwargs: dict[str, Any] = {"search": query, "limit": limit}
    if reply_to is not None:
        kwargs["reply_to"] = reply_to
    rows = []
    async for m in client.iter_messages(entity, **kwargs):
        dt = getattr(m, "date", None)
        if dt and until and dt >= until:
            continue
        if dt and since and dt < since:
            continue
        rows.append(_message_to_dict(m))
    rows.reverse()
    return {"chat": entity_label(entity), "topic": topic, "query": query, "count": len(rows), "messages": rows}


def register_tools(ctx):
    ctx.register_tool(
        name="tg_find_chat", toolset="telegram_user",
        schema={"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","default":20}},"required":["query"]},
        handler=_tg_find_chat,
    )
    ctx.register_tool(
        name="tg_list_topics", toolset="telegram_user",
        schema={"type":"object","properties":{"chat":{"type":"string"},"limit":{"type":"integer","default":100}},"required":["chat"]},
        handler=_tg_list_topics,
    )
    ctx.register_tool(
        name="tg_read_messages", toolset="telegram_user",
        schema={"type":"object","properties":{
            "chat":{"type":"string"},"topic":{"oneOf":[{"type":"string"},{"type":"integer"}]},
            "since":{"type":"string","description":"ISO datetime, today, or yesterday"},
            "until":{"type":"string","description":"ISO datetime"},"limit":{"type":"integer","default":200}
        },"required":["chat"]}, handler=_tg_read_messages,
    )
    ctx.register_tool(
        name="tg_search_messages", toolset="telegram_user",
        schema={"type":"object","properties":{
            "chat":{"type":"string"},"query":{"type":"string"},"topic":{"oneOf":[{"type":"string"},{"type":"integer"}]},
            "since":{"type":"string"},"until":{"type":"string"},"limit":{"type":"integer","default":100}
        },"required":["chat","query"]}, handler=_tg_search_messages,
    )
