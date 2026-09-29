"""Named collections of chats: a reusable set, keyed by canonical peer id.

Naming a group of chats once beats re-enumerating them on every request, and
the tempting shortcut -- storing titles or usernames -- is the bug that makes
such a list rot: a chat can be renamed, and a title is neither unique nor
stable. A collection therefore stores the canonical numeric peer id produced by
``telethon.utils.get_peer_id`` -- the same key space as ``aliases.json``'s
``peer_id``, ``digest_watermarks.json``'s keys and ``transcripts.sqlite3``'s
``chat_id`` -- and treats a peer's name as decoration for a human reader,
refreshed by whoever resolves the collection against the live dialog list
(``core.collections``).

Two things a naive store gets wrong, and this one does not:

* **a peer may not be both a member and excluded.** The two lists would then
  disagree about whether the chat belongs, and whichever branch a reader
  happened to check first would win. Exclusion wins here: on write, and again
  on every read, so the invariant holds even for a hand-edited file.
* **a row without a peer id is not a member.** It can never be resolved by id,
  so it is refused rather than stored, where it would masquerade as a chat that
  has disappeared from the account.

The name is a key, not an identifier: it is normalized with NFKC and casefolded
(so ``Work`` and ``ｗｏｒｋ`` are one collection), which also means renaming a
collection is a save under a new name, not an edit of the old one.
"""

from __future__ import annotations

import json
import os
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..sanitize import sanitize_name
from .paths import private_file, state_dir

__all__ = [
    "collections_path",
    "delete_collection",
    "get_collection",
    "list_collections",
    "save_collection",
]

_FILENAME = "collections.json"
_VERSION = 1
#: Long enough for a human label, short enough that a pasted paragraph is not
#: accepted as one.
_MAX_NAME_LEN = 128
#: Telegram ids are int32 on the wire; anything longer is a caller bug, and a
#: row carrying one could never match a real dialog.
_MAX_PEER_ID_LEN = 40

_LOCK = threading.RLock()
_CACHE: Optional[dict[str, dict[str, Any]]] = None


def collections_path() -> Path:
    """Where collections live, next to the rest of this plugin's state."""
    return state_dir() / _FILENAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(name: Any) -> str:
    """The storage key of a collection name: NFKC, trimmed, casefolded."""
    return unicodedata.normalize("NFKC", str(name or "")).strip().casefold()


def _peer_key(value: Any) -> Optional[str]:
    """The canonical peer id a row names, or ``None`` when it names none.

    Deliberately strict: only an integer or its decimal text is accepted, so a
    title, a username or a path pasted into the peer field is refused instead
    of becoming a key that can never match a dialog.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        token = value.strip()
        if not token:
            return None
        try:
            text = str(int(token, 10))
        except ValueError:
            return None
    else:
        return None
    if len(text) > _MAX_PEER_ID_LEN:
        return None
    return text


def _row(raw: Any) -> Optional[dict[str, Any]]:
    """One stored row, normalized, or ``None`` when it names no peer."""
    if not isinstance(raw, dict):
        return None
    peer = _peer_key(raw.get("peer_id"))
    if peer is None:
        return None
    username = raw.get("username")
    return {
        "peer_id": peer,
        "name": sanitize_name(raw.get("name"), limit=256),
        "username": sanitize_name(username, limit=128) if username else None,
    }


def _checked_rows(values: Any, *, field: str) -> list[dict[str, Any]]:
    """Every row a caller supplied, refusing anything unresolvable."""
    if values is None:
        return []
    try:
        items = list(values)
    except TypeError:
        raise ValueError(f"collection {field} must be a list of rows") from None
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"collection {field}[{index}] must be a mapping with a peer_id")
        row = _row(raw)
        if row is None:
            raise ValueError(f"collection {field}[{index}] has no usable peer_id")
        rows.append(row)
    return rows


def _stored_rows(value: Any) -> list[dict[str, Any]]:
    """Rows read off disk: unusable ones are dropped, never resolved."""
    if not isinstance(value, (list, tuple)):
        return []
    return _dedupe(row for row in (_row(item) for item in value) if row is not None)


def _dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows keyed by peer id, the first row seen for a peer winning."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out.setdefault(row["peer_id"], row)
    return list(out.values())


def _disjoint(members: list[dict[str, Any]], exclude: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Members minus excluded peers: exclusion wins."""
    blocked = {row["peer_id"] for row in exclude}
    return [row for row in members if row["peer_id"] not in blocked]


def _stored_row(key: Any, raw: Any) -> Optional[dict[str, Any]]:
    """One collection read off disk, repaired, or ``None`` when unusable."""
    storage_key = _key(key)
    if not storage_key or not isinstance(raw, dict):
        return None
    name = sanitize_name(raw.get("name"), limit=_MAX_NAME_LEN) or storage_key
    exclude = _stored_rows(raw.get("exclude"))
    members = _disjoint(_stored_rows(raw.get("members")), exclude)
    updated = raw.get("updated_at")
    return {
        "name": name,
        "members": members,
        "exclude": exclude,
        "updated_at": sanitize_name(updated, limit=64) if updated else None,
    }


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """A copy of a collection, safe for a caller to mutate."""
    return {
        "name": row["name"],
        "members": [dict(item) for item in row["members"]],
        "exclude": [dict(item) for item in row["exclude"]],
        "updated_at": row["updated_at"],
    }


def _load_unlocked() -> dict[str, dict[str, Any]]:
    """Collections as currently known; a missing or corrupt file means none."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = collections_path()
    if not path.exists():
        _CACHE = {}
        return _CACHE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _CACHE = {}
        return _CACHE
    stored = raw.get("collections", {}) if isinstance(raw, dict) else {}
    collections: dict[str, dict[str, Any]] = {}
    if isinstance(stored, dict):
        for key, value in stored.items():
            row = _stored_row(key, value)
            if row is not None:
                collections[_key(key)] = row
    _CACHE = collections
    return _CACHE


def _save_unlocked(collections: dict[str, dict[str, Any]]) -> None:
    global _CACHE
    path = collections_path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    ordered = {key: collections[key] for key in sorted(collections)}
    payload = json.dumps(
        {"version": _VERSION, "collections": ordered}, ensure_ascii=False, indent=2
    )
    tmp.write_text(payload + "\n", encoding="utf-8")
    private_file(tmp)
    os.replace(tmp, path)
    private_file(path)
    _CACHE = collections


def list_collections() -> list[dict[str, Any]]:
    """Every collection as a summary row, ordered by name."""
    with _LOCK:
        rows = [
            {
                "name": row["name"],
                "member_count": len(row["members"]),
                "exclude_count": len(row["exclude"]),
                "updated_at": row["updated_at"],
            }
            for row in _load_unlocked().values()
        ]
    rows.sort(key=lambda row: str(row.get("name") or "").casefold())
    return rows


def get_collection(name: str) -> Optional[dict[str, Any]]:
    """One collection with its rows, or ``None`` when it does not exist."""
    key = _key(name)
    if not key:
        return None
    with _LOCK:
        row = _load_unlocked().get(key)
        return _public(row) if row is not None else None


def save_collection(
    name: str,
    *,
    members: Any,
    exclude: Any = (),
    replace: bool = True,
) -> dict[str, Any]:
    """Store a collection and return it.

    ``replace=True`` overwrites both lists. ``replace=False`` merges: rows are
    unioned by peer id, keeping the first row seen for a peer, so a call can add
    members without restating the ones already stored (and an existing name or
    username is not clobbered by a coarser one). A peer named in ``exclude`` is
    dropped from the members either way.
    """
    display = sanitize_name(name, limit=_MAX_NAME_LEN)
    key = _key(display)
    if not key:
        raise ValueError("collection name is required")
    incoming_members = _checked_rows(members, field="members")
    incoming_exclude = _checked_rows(exclude, field="exclude")
    with _LOCK:
        store = dict(_load_unlocked())
        previous = None if replace else store.get(key)
        if isinstance(previous, dict):
            member_rows = _dedupe([*previous.get("members", ()), *incoming_members])
            exclude_rows = _dedupe([*previous.get("exclude", ()), *incoming_exclude])
        else:
            member_rows = _dedupe(incoming_members)
            exclude_rows = _dedupe(incoming_exclude)
        row = {
            "name": display,
            "members": _disjoint(member_rows, exclude_rows),
            "exclude": exclude_rows,
            "updated_at": _now(),
        }
        store[key] = row
        _save_unlocked(store)
    return _public(row)


def delete_collection(name: str) -> bool:
    """Drop one collection; ``True`` if it existed."""
    key = _key(name)
    if not key:
        return False
    with _LOCK:
        store = dict(_load_unlocked())
        existed = key in store
        if existed:
            store.pop(key, None)
            _save_unlocked(store)
    return existed
