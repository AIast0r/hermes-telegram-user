"""Resolving a named collection against the live dialog list.

A collection is a stored set of scopes (``core.state.collections``); this module
turns it into the dialogs a caller can then read, and says where a stored peer
went when it is no longer in the account's dialog list.

Resolution is by canonical peer id and nothing else. A title and a username are
both mutable -- a chat gets renamed, a username changes hands -- so neither can
be trusted to keep pointing at the peer the user meant. The stored name travels
with a row only so a caller can print it. A member may also name one forum
thread of a chat, so what a selection returns is a scope: the dialog together
with the thread to read it in, or ``None`` for the whole chat.

Membership is checked against a single sweep of ``client.iter_dialogs()``:
one pass per call, in the order Telegram returns the dialogs (most recent
first). A peer that is missing from the sweep is not an error here -- the rest
of the collection is still usable -- and :func:`describe_collection` is where a
caller goes to see exactly which peers are missing. A thread is only as present
as the chat it lives in: the sweep says nothing about individual topics, so a
thread member whose peer is gone is missing like any other.
"""

from __future__ import annotations

from typing import Any, Optional

from .sanitize import sanitize_name
from .state.collections import get_collection

__all__ = ["describe_collection", "select_dialogs", "select_scopes"]

#: Long enough for any human label this module echoes back in an error.
_NAME_LIMIT = 128


def _dialog_peer_id(dialog: Any) -> Optional[str]:
    """The canonical peer id of a dialog, or ``None`` when it has none.

    ``telethon.utils.get_peer_id`` is the marking every other file in this
    plugin stores by, so a dialog found here compares equal to a stored row.
    A dialog whose entity cannot be marked falls back to ``Dialog.id``, which
    Telethon already keeps in the same space.
    """
    entity = getattr(dialog, "entity", None)
    try:
        from telethon import utils

        if entity is not None:
            return str(utils.get_peer_id(entity))
    except Exception:
        pass
    raw = getattr(dialog, "id", None)
    if raw is None:
        return None
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        return None


async def _visible_peers(client: Any) -> dict[str, Any]:
    """One sweep of the dialog list, keyed by canonical peer id."""
    found: dict[str, Any] = {}
    async for dialog in client.iter_dialogs():
        peer = _dialog_peer_id(dialog)
        if peer is not None and peer not in found:
            found[peer] = dialog
    return found


def _missing_collection(name: Any) -> ValueError:
    return ValueError(f"Telegram collection not found: {sanitize_name(name, limit=_NAME_LIMIT)}")


def _selectable_members(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """Member rows that survive the collection's exclusions, in stored order.

    The store already repairs its own file, but a reader that trusted it blindly
    would still disagree with it the day the rule changed: a whole-chat
    exclusion removes every row of that peer, while an exclusion that names a
    thread removes only that ``(peer, thread)`` scope.
    """
    across = {row["peer_id"] for row in collection["exclude"] if row.get("thread") is None}
    scoped = {
        (row["peer_id"], row.get("thread"))
        for row in collection["exclude"]
        if row.get("thread") is not None
    }
    return [
        row
        for row in collection["members"]
        if row["peer_id"] not in across and (row["peer_id"], row.get("thread")) not in scoped
    ]


async def select_scopes(
    client: Any, name: str
) -> tuple[dict[str, Any], list[tuple[Any, Optional[int]]]]:
    """The scopes a collection names: its members, minus its exclusions.

    Returns the stored collection (so a caller can print its name and counts)
    together with one ``(dialog, thread)`` pair per member that is present in
    the sweep, in dialog-list order: ``thread`` is a topic's root message id for
    a thread member and ``None`` for a whole-chat one. A member whose peer did
    not show up is skipped here -- :func:`describe_collection` is where it
    becomes visible -- and a peer that is a member both as a whole and once per
    thread yields one pair per scope, all carrying the same dialog.
    """
    collection = get_collection(name)
    if collection is None:
        raise _missing_collection(name)
    label = sanitize_name(collection["name"], limit=_NAME_LIMIT)
    if not collection["members"]:
        raise ValueError(f"Telegram collection has no members: {label}")
    members = _selectable_members(collection)
    if not members:
        raise ValueError(
            "Telegram collection has no selectable members (every member is excluded): "
            f"{label}"
        )
    visible = await _visible_peers(client)
    threads: dict[str, list[Optional[int]]] = {}
    for row in members:
        thread = row.get("thread")
        threads.setdefault(row["peer_id"], []).append(
            int(thread) if thread is not None else None
        )
    scopes: list[tuple[Any, Optional[int]]] = []
    for peer, dialog in visible.items():
        for thread in threads.get(peer, ()):
            scopes.append((dialog, thread))
    return collection, scopes


async def select_dialogs(client: Any, name: str) -> tuple[dict[str, Any], list[Any]]:
    """The dialogs a collection names: its members, minus its exclusions.

    Defined as the unique dialogs of :func:`select_scopes`, for callers that
    read a chat as a whole and would only be confused by one dialog appearing
    once per thread it was saved with.
    """
    collection, scopes = await select_scopes(client, name)
    seen: set[int] = set()
    dialogs: list[Any] = []
    for dialog, _thread in scopes:
        if id(dialog) in seen:
            continue
        seen.add(id(dialog))
        dialogs.append(dialog)
    return collection, dialogs


async def describe_collection(client: Any, name: str) -> dict[str, Any]:
    """A collection plus, for each stored row, whether its chat still exists.

    ``present`` is answered by the same single sweep selection uses, so a chat
    that left the account, was deleted, or was only ever reachable as an id is
    visible to the caller instead of quietly shrinking what a selection
    returns. Presence is a property of the peer, never of a thread -- the sweep
    names chats, not topics -- so each row carries its ``thread`` (``None`` for
    a whole-chat scope) alongside it. ``missing`` lists the member peers that
    are gone; an excluded peer that is gone is still visible in ``exclude`` with
    ``present`` false, but it cannot affect what a selection returns, so it is
    not counted as missing.
    """
    collection = get_collection(name)
    if collection is None:
        raise _missing_collection(name)
    visible = await _visible_peers(client)
    members = [
        {**row, "present": row["peer_id"] in visible} for row in collection["members"]
    ]
    exclude = [
        {**row, "present": row["peer_id"] in visible} for row in collection["exclude"]
    ]
    return {
        "name": collection["name"],
        "member_count": len(members),
        "exclude_count": len(exclude),
        "members": members,
        "exclude": exclude,
        "missing": [row["peer_id"] for row in members if not row["present"]],
    }
