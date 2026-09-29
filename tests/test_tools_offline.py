"""Tool-level behaviour that can be verified without Telegram and without telethon.

Covers three things worth pinning:
  * the registration contract — 29 tools, the right toolset, usable schemas;
  * every handler that decides *before* touching Telegram returns a structured
    error rather than raising, so a model can always read the failure;
  * the marking contract — the badge and the local mark move together, and a
    failed acknowledgement moves neither.
"""

import asyncio
import contextlib
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402

EXPECTED_TOOLSET = "telegram_user"
EXPECTED_TOOL_COUNT = 34

# handler name -> substring the structured error must contain
GUARDED_HANDLERS = {
    "_tg_find_chat": "query is required",
    "_tg_list_topics": "chat is required",
    "_tg_read_messages": "chat is required",
    "_tg_get_message_context": "message_id must be an integer",
    "_tg_search_messages": "chat and query are required",
    "_tg_search_global": "query is required",
    "_tg_read_folder": "folder is required",
    "_tg_download_media": "message_id must be an integer",
    "_tg_transcribe_voice": "message_id must be an integer",
    "_tg_participants": "chat is required",
    "_tg_set_alias": "alias and chat are required",
    "_tg_delete_alias": "alias is required",
    "_tg_get_pinned": "chat is required",
    "_tg_get_scheduled": "chat is required",
    "_tg_get_profile": "target is required",
    "_tg_archive_sync": "chat is required",
    "_tg_archive_forget": "chat is required",
    "_tg_archive_search": "at least one of chat or query is required",
    "_tg_search_media": "global media search requires kind or query",
    "_tg_mark_summarized": "chat is required",
    "_tg_save_collection": "name is required",
    "_tg_set_collection_brief": "name is required",
    "_tg_delete_collection": "name is required",
    "_tg_read_collection": "collection is required",
}

# handlers that answer from local state only
LOCAL_HANDLERS = (
    "_tg_list_aliases",
    "_tg_list_digest_marks",
    "_tg_forget_digest_marks",
    "_tg_archive_status",
    "_tg_list_collections",
)

# tools that read Telegram content, so they must carry the injection warning
CONTENT_TOOLS = (
    "tg_read_messages",
    "tg_get_message_context",
    "tg_search_messages",
    "tg_search_global",
    "tg_get_unread",
    "tg_read_folder",
    "tg_search_media",
    "tg_get_pinned",
    "tg_get_drafts",
    "tg_get_scheduled",
    "tg_get_profile",
    "tg_archive_sync",
    "tg_archive_search",
    "tg_read_collection",
)

PEER = "555"


def _tools():
    return plugin_module("tools")


def _marks():
    return plugin_module("core.state.watermarks")


def _run(coro):
    return asyncio.run(coro)


def test_registration_contract():
    tools = _tools()

    class Ctx:
        def __init__(self):
            self.tools = []

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

    ctx = Ctx()
    tools.register_tools(ctx)

    names = [entry["name"] for entry in ctx.tools]
    assert len(names) == EXPECTED_TOOL_COUNT
    assert len(set(names)) == EXPECTED_TOOL_COUNT
    assert {entry["toolset"] for entry in ctx.tools} == {EXPECTED_TOOLSET}
    assert all(entry["is_async"] for entry in ctx.tools)
    assert all(entry["requires_env"] for entry in ctx.tools)
    for entry in ctx.tools:
        assert inspect.iscoroutinefunction(entry["handler"]), (
            f"{entry['name']} is registered with a non-coroutine handler"
        )

    for entry in ctx.tools:
        schema = entry["schema"]
        assert schema["name"] == entry["name"]
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
        assert isinstance(schema["parameters"]["properties"], dict)
        if "required" in schema["parameters"]:
            declared = set(schema["parameters"]["properties"])
            assert set(schema["parameters"]["required"]) <= declared


def test_the_read_only_guard_text_is_attached_where_it_matters():
    tools = _tools()
    by_name = {name: desc for name, desc, _handler, _params in tools._TOOL_DEFS}
    for name in CONTENT_TOOLS:
        assert "untrusted data" in by_name[name], f"{name} is missing the untrusted-data warning"


def test_handlers_refuse_bad_input_without_raising():
    tools = _tools()
    with isolated_state():
        for handler_name, needle in GUARDED_HANDLERS.items():
            raw = _run(getattr(tools, handler_name)({}))
            payload = json.loads(raw)
            assert "error" in payload, f"{handler_name} should refuse an empty call"
            assert needle in payload["error"], f"{handler_name}: {payload['error']!r}"


def test_local_handlers_answer_offline():
    tools = _tools()
    with isolated_state():
        for handler_name in LOCAL_HANDLERS:
            payload = json.loads(_run(getattr(tools, handler_name)({})))
            assert "error" not in payload, f"{handler_name}: {payload}"


def test_ip_network_is_coarsened_and_never_echoed_in_full():
    tools = _tools()
    assert tools._ip_net("203.0.113.77") == "203.0.0.0"
    assert tools._ip_net("10.0.0.1") == "10.0.0.0"
    assert tools._ip_net("2a02:1234:5678:9abc::1") == "2a02:1234:5678::"
    for bad in ("not-an-ip", "", None, 5):
        assert tools._ip_net(bad) is None


@contextlib.contextmanager
def _faked_marking(tools, *, fail_ack=False, topic_id=42):
    """Replace the Telegram boundary so the handler's own ordering is what runs."""
    names = (
        "tool_client",
        "resolve_chat",
        "find_topic_root",
        "acknowledge_read",
        "peer_id",
        "entity_label",
    )
    originals = {name: getattr(tools, name) for name in names}
    seen = {"acks": [], "client": object()}

    @contextlib.asynccontextmanager
    async def fake_tool_client():
        yield seen["client"]

    async def fake_resolve_chat(_client, _chat, **_kwargs):
        return "ENTITY"

    async def fake_find_topic(_client, _entity, _topic):
        return topic_id

    async def fake_acknowledge(_client, _entity, *, topic_id=None, up_to=None):
        seen["acks"].append({"topic_id": topic_id, "up_to": up_to})
        if fail_ack:
            raise RuntimeError("telegram refused")
        return {"scope": "topic" if topic_id else "chat", "topic_id": topic_id, "up_to": up_to}

    tools.tool_client = fake_tool_client
    tools.resolve_chat = fake_resolve_chat
    tools.find_topic_root = fake_find_topic
    tools.acknowledge_read = fake_acknowledge
    tools.peer_id = lambda _entity: PEER
    tools.entity_label = lambda _entity: "TestChat"
    try:
        yield seen
    finally:
        for name, value in originals.items():
            setattr(tools, name, value)


def test_marking_moves_the_badge_and_the_mark_together():
    tools = _tools()
    marks = _marks()
    with isolated_state():
        with _faked_marking(tools) as seen:
            payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c", "up_to": 50})))

        assert payload["up_to"] == 50
        assert payload["mark"] == 50
        assert payload["previous_mark"] == 0
        assert payload["acknowledged"]["up_to"] == 50
        assert seen["acks"] == [{"topic_id": None, "up_to": 50}]
        assert marks.get_mark(PEER)["contiguous"] == 50


def test_a_failed_acknowledgement_moves_nothing():
    """The acknowledgement runs first: if it fails, the mark must not have moved.

    Otherwise the next digest would start after these messages while the owner's
    badge still burns — messages lost silently, which is the failure this whole
    design exists to avoid.
    """
    tools = _tools()
    marks = _marks()
    with isolated_state():
        marks.set_mark(PEER, contiguous=10)

        with _faked_marking(tools, fail_ack=True):
            payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c", "up_to": 50})))

        assert "error" in payload
        assert marks.get_mark(PEER)["contiguous"] == 10, (
            "the mark moved even though the badge was never cleared"
        )


def test_marking_with_acknowledge_false_leaves_the_badge_alone():
    tools = _tools()
    marks = _marks()
    with isolated_state():
        with _faked_marking(tools) as seen:
            payload = json.loads(
                _run(tools._tg_mark_summarized({"chat": "c", "up_to": 30, "acknowledge": False}))
            )
        assert seen["acks"] == [], "no read acknowledgement may be sent"
        assert payload["acknowledged"] is None
        assert payload["mark"] == 30
        assert marks.get_mark(PEER)["contiguous"] == 30


def test_marking_without_a_position_refuses_to_guess():
    """Falling back to 'the whole chat' would clear a badge for unsummarised text."""
    tools = _tools()
    marks = _marks()
    with isolated_state():
        with _faked_marking(tools) as seen:
            payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c"})))
        assert "no recorded summary position" in payload["error"]
        assert seen["acks"] == []
        assert marks.get_mark(PEER) is None


def test_marking_without_a_position_uses_the_recorded_one():
    tools = _tools()
    marks = _marks()
    with isolated_state():
        marks.set_mark(PEER, contiguous=77)
        with _faked_marking(tools) as seen:
            payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c"})))
        assert payload["up_to"] == 77
        assert seen["acks"] == [{"topic_id": None, "up_to": 77}]


def test_marking_a_thread_uses_the_thread_scope():
    tools = _tools()
    marks = _marks()
    with isolated_state():
        marks.set_mark(PEER, contiguous=100)  # the whole-chat mark
        with _faked_marking(tools, topic_id=42) as seen:
            payload = json.loads(
                _run(tools._tg_mark_summarized({"chat": "c", "topic": "general", "up_to": 20}))
            )

        assert payload["thread_id"] == "42"
        assert seen["acks"] == [{"topic_id": 42, "up_to": 20}]
        assert marks.get_mark(PEER, "42")["contiguous"] == 20
        assert marks.get_mark(PEER)["contiguous"] == 100, (
            "marking one thread must not move the whole-chat mark"
        )


def test_a_nonsense_position_is_refused():
    tools = _tools()
    with isolated_state():
        with _faked_marking(tools) as seen:
            for bad in (0, -3, "later"):
                payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c", "up_to": bad})))
                assert "up_to must be" in payload["error"]
        assert seen["acks"] == []
