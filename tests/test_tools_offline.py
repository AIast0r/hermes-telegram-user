"""Tool-level behaviour that can be verified without Telegram and without telethon.

Covers three things worth pinning:
  * the registration contract — 28 tools, the right toolset, usable schemas;
  * every handler that decides *before* touching Telegram returns a structured
    error rather than raising, so a model can always read the failure;
  * the digest watermark advance rule, faked at the client boundary.
"""

import asyncio
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402

EXPECTED_TOOLSET = "telegram_user"
EXPECTED_TOOL_COUNT = 28

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
}

# handlers that answer from local state only
LOCAL_HANDLERS = ("_tg_list_aliases", "_tg_list_digest_marks", "_tg_forget_digest_marks", "_tg_archive_status")


def _tools():
    return plugin_module("tools")


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
    assert all(callable(entry["handler"]) for entry in ctx.tools)

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
    guarded = {
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
    }
    by_name = {name: desc for name, desc, _h, _p in tools._TOOL_DEFS}
    for name in sorted(guarded):
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
def _faked_client(tools, rows):
    """Replace the Telegram boundary so the handler's own logic is what runs."""
    originals = {name: getattr(tools, name) for name in
                 ("tool_client", "resolve_chat", "_read_messages", "peer_id", "entity_label")}
    fetched = []

    @contextlib.asynccontextmanager
    async def fake_tool_client():
        yield None

    async def fake_resolve_chat(client, chat, **kwargs):
        return object()

    async def fake_read_messages(client, entity, *, limit, since=None, until=None, **kwargs):
        fetched.append(dict(kwargs))
        return list(rows[0])

    tools.tool_client = fake_tool_client
    tools.resolve_chat = fake_resolve_chat
    tools._read_messages = fake_read_messages
    tools.peer_id = lambda entity: "555"
    tools.entity_label = lambda entity: "TestChat"
    try:
        yield fetched
    finally:
        for name, value in originals.items():
            setattr(tools, name, value)


def _ids(*values):
    return [{"id": value, "text": f"m{value}"} for value in values]


def test_digest_flag_refuses_a_filtered_walk():
    """A filtered walk cannot prove it reached the mark, so it must not advance one."""
    tools = _tools()
    with isolated_state():
        rows = [[]]
        with _faked_client(tools, rows):
            for args in (
                {"chat": "c", "since_last_digest": True, "since": "today"},
                {"chat": "c", "since_last_digest": True, "until": "today"},
                {"chat": "c", "since_last_digest": True, "topic": "general"},
            ):
                payload = json.loads(_run(tools._tg_read_messages(args)))
                assert "cannot be combined" in payload["error"]


def test_digest_advances_only_on_a_completed_walk():
    tools = _tools()
    marks = plugin_module("core.state.watermarks")
    with isolated_state():
        rows = [_ids(10, 11, 12)]
        with _faked_client(tools, rows):
            payload = json.loads(_run(tools._tg_read_messages({"chat": "c", "since_last_digest": True})))
        assert payload["digest"]["mark"] == 12
        assert payload["digest"]["has_hole"] is False
        assert marks.get_mark("555")["contiguous"] == 12


def test_a_page_limited_walk_records_a_hole_and_does_not_advance():
    tools = _tools()
    marks = plugin_module("core.state.watermarks")
    with isolated_state():
        marks.advance("555", contiguous=12)
        rows = [_ids(20, 21)]
        with _faked_client(tools, rows) as fetched:
            payload = json.loads(
                _run(tools._tg_read_messages({"chat": "c", "since_last_digest": True, "limit": 2}))
            )
        assert fetched[0]["min_id"] == 12, "the fetch must start above the mark"
        assert payload["digest"]["mark"] == 12, "a truncated walk must not advance"
        assert payload["digest"]["has_hole"] is True
        assert payload["digest"]["resume_max_id"] == 20
        assert marks.get_mark("555")["contiguous"] == 12


def test_the_next_walk_closes_the_hole_before_taking_new_traffic():
    tools = _tools()
    marks = plugin_module("core.state.watermarks")
    with isolated_state():
        marks.advance("555", contiguous=100)
        marks.advance("555", contiguous=200, top=500)

        rows = [_ids(101, 150, 199)]  # the gap between the mark and the recorded hole
        with _faked_client(tools, rows) as fetched:
            payload = json.loads(_run(tools._tg_read_messages({"chat": "c", "since_last_digest": True})))

        assert fetched[0]["min_id"] == 100
        assert fetched[0]["max_id"] == 200, "the hole must be closed before newer traffic"
        assert payload["digest"]["previous_mark"] == 100
        assert payload["digest"]["resumed_hole"] is True
        # Closing the gap makes 100..500 contiguous, so the mark jumps to the top
        # the earlier truncated walk had already seen.
        assert marks.get_mark("555")["contiguous"] == 500
        assert marks.get_mark("555")["has_hole"] is False


def test_an_empty_digest_read_leaves_the_mark_alone():
    tools = _tools()
    marks = plugin_module("core.state.watermarks")
    with isolated_state():
        marks.advance("555", contiguous=99)
        rows = [[]]
        with _faked_client(tools, rows):
            payload = json.loads(_run(tools._tg_read_messages({"chat": "c", "since_last_digest": True})))
        assert payload["count"] == 0
        assert payload["digest"]["mark"] == 99
        assert marks.get_mark("555")["contiguous"] == 99


def test_reading_without_the_digest_flag_never_touches_the_mark():
    tools = _tools()
    marks = plugin_module("core.state.watermarks")
    with isolated_state():
        rows = [_ids(5, 6)]
        with _faked_client(tools, rows) as fetched:
            payload = json.loads(_run(tools._tg_read_messages({"chat": "c"})))
        assert payload["digest"] is None
        assert payload["since_last_digest"] is False
        assert "min_id" not in fetched[0]
        assert marks.get_mark("555") is None
