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

    # a plain user/chat peer
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(rec, types.InputPeerUser(user_id=1, access_hash=2), up_to=100)
    )
    assert result == {"scope": "chat", "topic_id": None, "up_to": 100}
    assert len(rec.requests) == 1
    request = rec.requests[0]
    assert type(request).__name__ == "ReadHistoryRequest"
    assert isinstance(request, functions.messages.ReadHistoryRequest)
    assert request.max_id == 100

    # a channel / supergroup
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(
            rec, types.InputPeerChannel(channel_id=3, access_hash=4), up_to=200
        )
    )
    assert result == {"scope": "channel", "topic_id": None, "up_to": 200}
    request = rec.requests[0]
    assert isinstance(request, functions.channels.ReadHistoryRequest)
    assert request.max_id == 200

    # one forum thread: readDiscussion, so the other threads keep their badge
    rec = Recorder()
    result = asyncio.run(
        readstate.acknowledge_read(
            rec, types.InputPeerChannel(channel_id=3, access_hash=4), topic_id=77, up_to=300
        )
    )
    assert result == {"scope": "topic", "topic_id": 77, "up_to": 300}
    request = rec.requests[0]
    assert isinstance(request, functions.messages.ReadDiscussionRequest)
    assert request.msg_id == 77
    assert request.read_max_id == 300
    assert not isinstance(request, functions.channels.ReadHistoryRequest), (
        "a thread must never be acknowledged with the whole-chat request"
    )


def test_the_bound_is_never_widened_to_zero():
    """Every request carries the caller's id; Telegram's 0 never reaches the wire."""
    readstate = _readstate()
    if not HAS_TELETHON:
        return

    from telethon.tl import types

    rec = Recorder()
    asyncio.run(readstate.acknowledge_read(rec, types.InputPeerUser(user_id=1, access_hash=2), up_to=1))
    assert rec.requests[0].max_id == 1
