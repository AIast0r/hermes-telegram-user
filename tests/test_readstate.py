"""The read-acknowledgement module — the only writer in the package.

The request shapes are asserted against the real telethon when it is installed
(this suite still runs without it), because "which request, with which bound" is
the whole safety story here: the wrong one clears a badge the owner never asked
to clear.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import plugin_module  # noqa: E402


def _has_telethon():
    try:
        import telethon  # noqa: F401
    except Exception:
        return False
    return True


HAS_TELETHON = _has_telethon()


class Recorder:
    """Stands in for the Telethon client and keeps every request it is given."""

    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return True


def _readstate():
    return plugin_module("core.readstate")


def test_an_unbounded_acknowledgement_is_refused():
    """`up_to=0` is Telegram's 'mark everything read' — never send it by accident."""
    readstate = _readstate()
    rec = Recorder()
    for bad in (None, 0, -5, "nope", ""):
        try:
            asyncio.run(readstate.acknowledge_read(rec, object(), up_to=bad))
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for up_to={bad!r}")
    assert rec.requests == [], "nothing may reach Telegram when the bound is refused"


def test_a_nonsense_topic_id_is_refused():
    readstate = _readstate()
    rec = Recorder()
    try:
        asyncio.run(readstate.acknowledge_read(rec, object(), topic_id=0, up_to=10))
    except ValueError:
        pass
    else:
        raise AssertionError("topic_id=0 must be refused")
    assert rec.requests == []


def test_dispatch_picks_the_request_that_matches_the_peer_kind():
    readstate = _readstate()
    if not HAS_TELETHON:
        # Without the dependency the module must fail loudly, not silently skip.
        rec = Recorder()
        try:
            asyncio.run(readstate.acknowledge_read(rec, object(), up_to=5))
        except ModuleNotFoundError as exc:
            assert "telethon" in str(exc)
            return
        raise AssertionError("expected a telethon import failure")

    from telethon.tl import functions, types

    def kinds(reqs):
        return [type(request).__name__ for request in reqs]

    # a plain user/chat peer: all three badges, then the history bound
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(rec, types.InputPeerUser(user_id=1, access_hash=2), up_to=100)
    )
    assert result["scope"] == "chat"
    assert result["badges"] == ["unread", "mentions", "reactions"]
    assert kinds(rec.requests) == [
        "ReadMentionsRequest",
        "ReadReactionsRequest",
        "ReadHistoryRequest",
    ]
    assert isinstance(rec.requests[-1], functions.messages.ReadHistoryRequest)
    assert rec.requests[-1].max_id == 100
    for request in rec.requests[:2]:
        assert request.top_msg_id is None, "a whole-chat ack must not scope to a thread"

    # a channel / supergroup takes the channels request for the history part
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(
            rec, types.InputPeerChannel(channel_id=3, access_hash=4), up_to=200
        )
    )
    assert result["scope"] == "channel"
    assert kinds(rec.requests) == [
        "ReadMentionsRequest",
        "ReadReactionsRequest",
        "ReadHistoryRequest",
    ]
    for request in rec.requests[:2]:
        assert request.top_msg_id is None, "a whole-chat ack must not scope to a thread"
    assert isinstance(rec.requests[-1], functions.channels.ReadHistoryRequest)
    assert rec.requests[-1].max_id == 200

    # one forum thread: readDiscussion plus thread-scoped badge clearing, so the
    # sibling threads keep their badges
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(
            rec, types.InputPeerChannel(channel_id=3, access_hash=4), topic_id=77, up_to=300
        )
    )
    assert result == {
        "scope": "topic",
        "topic_id": 77,
        "up_to": 300,
        "badges": ["unread", "mentions", "reactions"],
    }
    assert kinds(rec.requests) == [
        "ReadMentionsRequest",
        "ReadReactionsRequest",
        "ReadDiscussionRequest",
    ]
    for request in rec.requests[:2]:
        assert request.top_msg_id == 77, "badge clearing must be scoped to the thread"
    thread_request = rec.requests[-1]
    assert isinstance(thread_request, functions.messages.ReadDiscussionRequest)
    assert thread_request.msg_id == 77
    assert thread_request.read_max_id == 300
    for request in rec.requests:
        assert not isinstance(request, functions.channels.ReadHistoryRequest), (
            "a thread must never be acknowledged with the whole-chat request, "
            "which would clear every topic in the forum"
        )


def test_no_request_widens_the_bound_to_telegrams_zero():
    """The caller's id must reach the wire; Telegram reads 0 as 'everything'.

    The bound assertions live in the dispatch test above, which selects each
    request by type; this pins the one case that must never be sent at all.
    """
    readstate = _readstate()
    rec = Recorder()
    for bad in (0, None):
        try:
            asyncio.run(readstate.acknowledge_read(rec, object(), up_to=bad))
        except ValueError:
            continue
        raise AssertionError(f"up_to={bad!r} must be refused before any request is built")
    assert rec.requests == []
