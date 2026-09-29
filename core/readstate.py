"""The one place in this package that marks Telegram messages as read.

Every other module is read-only: looking at a chat never clears a badge. This one
exists because a digest that already summarised a chat should also stop the
owner's phone from showing it as unread, and that is a write to Telegram.

Three rules keep it safe:

* an acknowledgement is always scoped to one chat, optionally to one forum
  thread — there is no account-wide entry point;
* it is always bounded by an explicit ``up_to`` message id, so nothing that
  arrived after the summary can be swallowed by the same call;
* ``up_to=0`` (Telegram's "mark everything read") is refused rather than sent,
  because that is the value a careless default would produce.

The requests are built by hand instead of via ``send_read_acknowledge`` so the
peer-kind dispatch is visible: a channel takes a different request than a basic
group, and a forum thread takes a third one.
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = ["acknowledge_read"]


def _is_channel(entity: Any) -> bool:
    from telethon.tl import types

    return isinstance(entity, (types.Channel, types.InputPeerChannel))


async def acknowledge_read(
    client: Any,
    entity: Any,
    *,
    topic_id: Optional[int] = None,
    up_to: Optional[int] = None,
) -> dict[str, Any]:
    """Clear the unread badge for one chat, or one forum thread, up to ``up_to``.

    ``up_to`` is required and must be a positive message id. Passing Telegram's
    ``0`` would mark the entire chat read, including messages the caller never
    looked at, so it is rejected instead of forwarded.
    """
    if up_to is None:
        raise ValueError(
            "marking a chat read needs an explicit up_to message id, so nothing "
            "that arrived after the summary is swallowed"
        )
    try:
        up_to = int(up_to)
    except (TypeError, ValueError) as exc:
        raise ValueError("up_to must be a message id") from exc
    if up_to <= 0:
        raise ValueError(
            "up_to must be a positive message id; 0 means 'mark everything read' "
            "in Telegram and is deliberately not allowed"
        )

    topic: Optional[int] = None
    if topic_id is not None:
        try:
            topic = int(topic_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("topic_id must be a message id") from exc
        if topic <= 0:
            raise ValueError("topic_id must be a positive message id")

    from telethon.tl import functions

    # Telegram counts three badges separately: plain unread, unread mentions and
    # unread reactions. ReadHistory alone leaves the other two lit, and because
    # the local mark moves afterwards nothing would ever re-surface that message
    # — the badge would be unfixable from here. top_msg_id scopes both to the
    # thread when one is in play, and None means the whole chat.
    scope = topic if topic is not None else None
    await client(functions.messages.ReadMentionsRequest(peer=entity, top_msg_id=scope))
    await client(functions.messages.ReadReactionsRequest(peer=entity, top_msg_id=scope))

    if topic is not None:
        # A forum thread is read on its own: readDiscussion targets the topic's
        # root message and leaves the other threads of the chat untouched.
        await client(
            functions.messages.ReadDiscussionRequest(
                peer=entity, msg_id=topic, read_max_id=up_to
            )
        )
        return {
            "scope": "topic",
            "topic_id": topic,
            "up_to": up_to,
            "badges": ["unread", "mentions", "reactions"],
        }

    if _is_channel(entity):
        await client(functions.channels.ReadHistoryRequest(channel=entity, max_id=up_to))
        return {
            "scope": "channel",
            "topic_id": None,
            "up_to": up_to,
            "badges": ["unread", "mentions", "reactions"],
        }

    await client(functions.messages.ReadHistoryRequest(peer=entity, max_id=up_to))
    return {
        "scope": "chat",
        "topic_id": None,
        "up_to": up_to,
        "badges": ["unread", "mentions", "reactions"],
    }
