from __future__ import annotations

import re
import unicodedata
from typing import Any

# Unicode controls that can visually reorder/hide prompt text. We deliberately
# keep ZWJ/ZWNJ so emoji and legitimate scripts are not mangled.
_STRIP_FORMAT_CODEPOINTS = {
    0x061C,  # Arabic letter mark
    0x200B,  # zero-width space
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embedding/override
    0x2060,  # word joiner
    0x2066, 0x2067, 0x2068, 0x2069,  # bidi isolates
    0xFEFF,  # BOM / zero-width no-break space
}
_EXCESSIVE_NEWLINES = re.compile(r"\n{4,}")


def sanitize_text(value: Any, *, limit: int = 12000) -> str:
    """Normalize user-controlled Telegram text without keyword filtering."""
    text = unicodedata.normalize("NFC", str(value or ""))
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if ch in "\n\t":
            out.append(ch)
            continue
        if cp in _STRIP_FORMAT_CODEPOINTS:
            continue
        # C0/C1 controls (and other Unicode controls) have no useful place in
        # model-facing Telegram text. Formatting marks are handled explicitly
        # above so legitimate ZWJ/ZWNJ remain intact.
        if unicodedata.category(ch) == "Cc":
            continue
        out.append(ch)
    cleaned = _EXCESSIVE_NEWLINES.sub("\n\n\n", "".join(out))
    if limit > 0 and len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


def sanitize_name(value: Any, *, limit: int = 256) -> str:
    """Compact a Telegram-controlled display label to one safe line."""
    cleaned = sanitize_text(value, limit=max(limit * 2, limit))
    cleaned = " ".join(cleaned.replace("\n", " ").replace("\t", " ").split())
    if limit > 0 and len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


def sanitize_structure(value: Any, *, string_limit: int = 4096) -> Any:
    """Recursively sanitize strings in a JSON-like Telegram payload."""
    if isinstance(value, str):
        return sanitize_text(value, limit=string_limit)
    if isinstance(value, list):
        return [sanitize_structure(item, string_limit=string_limit) for item in value]
    if isinstance(value, tuple):
        return [sanitize_structure(item, string_limit=string_limit) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_name(key, limit=128) if isinstance(key, str) else key:
            sanitize_structure(item, string_limit=string_limit)
            for key, item in value.items()
        }
    return value
