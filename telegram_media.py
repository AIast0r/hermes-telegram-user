from __future__ import annotations

from typing import Any, Optional

from .telegram_sanitize import sanitize_name, sanitize_text


_MIME_FALLBACKS = {
    "photo": "image/jpeg",
    "voice": "audio/ogg",
    "audio": "audio/mpeg",
    "video": "video/mp4",
    "video_note": "video/mp4",
    "gif": "image/gif",
    "sticker": "image/webp",
    "document": "application/octet-stream",
}


def media_info(message: Any) -> Optional[dict[str, Any]]:
    """Return compact, sanitized attachment metadata without another Telegram request.

    Link previews are deliberately not treated as attachments. Telegram exposes
    preview images through ``message.photo`` too, which otherwise makes a plain
    message containing a URL look like an uploaded photo.
    """
    if message is None or getattr(message, "web_preview", None) is not None:
        return None

    kind: Optional[str] = None
    if getattr(message, "sticker", None) is not None:
        kind = "sticker"
    elif getattr(message, "photo", None) is not None:
        kind = "photo"
    elif getattr(message, "voice", None) is not None:
        kind = "voice"
    elif getattr(message, "video_note", None) is not None:
        kind = "video_note"
    elif getattr(message, "video", None) is not None:
        kind = "video"
    elif getattr(message, "audio", None) is not None:
        kind = "audio"
    elif getattr(message, "gif", None) is not None:
        kind = "gif"
    elif getattr(message, "document", None) is not None:
        kind = "document"
    elif getattr(message, "contact", None) is not None:
        kind = "contact"
    elif getattr(message, "geo", None) is not None:
        kind = "location"
    elif getattr(message, "poll", None) is not None:
        kind = "poll"
    elif getattr(message, "media", None) is not None:
        kind = "media"
    if kind is None:
        return None

    file_obj = getattr(message, "file", None)
    document = getattr(message, "document", None)
    mime = (
        getattr(file_obj, "mime_type", None)
        or getattr(document, "mime_type", None)
        or _MIME_FALLBACKS.get(kind)
        or "application/octet-stream"
    )
    name = getattr(file_obj, "name", None)
    size = getattr(file_obj, "size", None) or getattr(document, "size", None)

    duration = getattr(file_obj, "duration", None)
    if duration is None and document is not None:
        for attr in getattr(document, "attributes", None) or []:
            value = getattr(attr, "duration", None)
            if value is not None:
                duration = value
                break

    result: dict[str, Any] = {
        "kind": sanitize_name(kind, limit=32),
        "mime_type": sanitize_text(mime, limit=128),
    }
    if name:
        result["file_name"] = sanitize_name(name, limit=512)
    if size is not None:
        try:
            result["size"] = int(size)
        except (TypeError, ValueError):
            pass
    if duration is not None:
        try:
            result["duration"] = float(duration)
        except (TypeError, ValueError):
            pass
    return result


def message_kind_hint(kind: str) -> Optional[str]:
    if kind in {"photo", "sticker"}:
        return "image"
    if kind in {"voice", "audio"}:
        return "audio"
    if kind in {"document", "video", "video_note", "gif"}:
        return "document"
    return None


async def cache_message_media(client: Any, message: Any, *, max_bytes: int) -> dict[str, Any]:
    """Download one Telegram attachment into Hermes' normal inbound media cache."""
    info = media_info(message)
    if info is None:
        raise ValueError("message has no downloadable media")
    if info["kind"] in {"contact", "location", "poll", "media"}:
        raise ValueError(f"unsupported media kind: {info['kind']}")

    size = int(info.get("size") or 0)
    if max_bytes > 0 and size > max_bytes:
        raise ValueError(
            f"media is too large ({size} bytes; configured limit is {max_bytes} bytes)"
        )

    payload = await client.download_media(message, file=bytes)
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise RuntimeError("Telegram returned no media bytes")
    if max_bytes > 0 and len(payload) > max_bytes:
        raise ValueError(
            f"downloaded media is too large ({len(payload)} bytes; configured limit is {max_bytes} bytes)"
        )

    from gateway.platforms.media_cache import cache_media_bytes

    path = cache_media_bytes(
        bytes(payload),
        str(info.get("mime_type") or "application/octet-stream"),
        filename_hint=str(info.get("file_name") or ""),
        kind_hint=message_kind_hint(str(info.get("kind") or "")),
    )
    return {**info, "path": path}
