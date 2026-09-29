"""Resolving a named collection against the live dialog list.

A collection is a stored set of peers (``core.state.collections``); this module
turns it into the dialogs a caller can then read, and says where a stored peer
went when it is no longer in the account's dialog list.

Resolution is by canonical peer id and nothing else. A title and a username are
both mutable -- a chat gets renamed, a username changes hands -- so neither can
be trusted to keep pointing at the peer the user meant. The stored name travels
with a row only so a caller can print it.

Membership is checked against a single sweep of ``client.iter_dialogs()``:
one pass per call, in the order Telegram returns the dialogs (most recent
first). A peer that is missing from the sweep is not an error here -- the rest
of the collection is still usable -- and :func:`describe_collection` is where a
caller goes to see exactly which peers are missing.
"""

from __future__ import annotations

from typing import Any, Optional

from .sanitize import sanitize_name
from .state.collections import get_collection

__all__ = ["describe_collection", "select_dialogs"]

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


async def select_dialogs(client: Any, name: str) -> tuple[dict[str, Any], list[Any]]:
    """The dialogs a collection names: its members, minus its exclusions.

    Returns the stored collection (so a caller can print its name and counts,
    or refresh a display name from the resolved dialogs) together with the
    dialogs, in dialog-list order.
    """
    collection = get_collection(name)
    if collection is None:
        raise _missing_collection(name)
    members = [row["peer_id"] for row in collection["members"]]
    if not members:
        raise ValueError(
            "Telegram collection has no members: "
            f"{sanitize_name(collection['name'], limit=_NAME_LIMIT)}"
        )
    excluded = {row["peer_id"] for row in collection["exclude"]}
    wanted = set(members) - excluded
    if not wanted:
        raise ValueError(
            "Telegram collection has no selectable members (every member is excluded): "
            f"{sanitize_name(collection['name'], limit=_NAME_LIMIT)}"
        )
    visible = await _visible_peers(client)
    dialogs = [dialog for peer, dialog in visible.items() if peer in wanted]
    return collection, dialogs


async def describe_collection(client: Any, name: str) -> dict[str, Any]:
    """A collection plus, for each stored peer, whether it still exists.

    ``present`` is answered by the same single sweep selection uses, so a chat
    that left the account, was deleted, or was only ever reachable as an id is
    visible to the caller instead of quietly shrinking what a selection
    returns. ``missing`` lists the member peers that are gone; an excluded peer
    that is gone is still visible in ``exclude`` with ``present`` false, but it
    cannot affect what a selection returns, so it is not counted as missing.
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
