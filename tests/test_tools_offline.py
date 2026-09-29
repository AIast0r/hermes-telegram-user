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
from typing import Any

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


@contextlib.contextmanager
def _faked_unread(tools, scopes):
    """Replace the Telegram boundary for a collection-scoped unread read."""
    names = (
        "tool_client",
        "select_scopes",
        "_read_messages",
        "dialog_summary",
        "dialog_waiting",
        "dialog_is_muted",
    )
    originals = {name: getattr(tools, name) for name in names}
    seen = {"fetch": [], "collection": None}

    @contextlib.asynccontextmanager
    async def fake_tool_client():
        yield object()

    async def fake_select_scopes(_client, name):
        seen["collection"] = name
        stored = plugin_module("core.state.collections").get_collection(name) or {}
        return {
            "name": name,
            "brief": stored.get("brief", ""),
            "members": stored.get("members") or [],
        }, list(scopes)

    async def fake_read(_client, _entity, *, limit, since=None, until=None, **kwargs):
        seen["fetch"].append(dict(kwargs))
        return [{"id": 1, "out": False, "text": "hello"}]

    tools.tool_client = fake_tool_client
    tools.select_scopes = fake_select_scopes
    tools._read_messages = fake_read
    tools.dialog_summary = lambda _dialog: {"id": "111", "name": "Forum"}
    tools.dialog_waiting = lambda _dialog: True
    tools.dialog_is_muted = lambda _dialog: False
    try:
        yield seen
    finally:
        for name, value in originals.items():
            setattr(tools, name, value)


class _FakeDialog:
    """A dialog carrying a chat-wide read pointer, as a real forum dialog does."""

    class _Raw:
        read_inbox_max_id = 5
        top_message = 9
        unread_mark = False

    def __init__(self):
        self.dialog = self._Raw()
        self.entity = "ENTITY"
        self.name = "Forum"
        self.id = 111


def test_unread_of_a_collection_honours_thread_scopes():
    """The same collection must not mean two different things in two tools.

    tg_read_collection reads a thread member through its own topic. If
    tg_get_unread fell back to whole-chat dialogs for the same collection it
    would silently widen the scope, with nothing in the payload to say so.
    """
    tools = _tools()
    with isolated_state():
        dialog = _FakeDialog()
        with _faked_unread(tools, [(dialog, None), (dialog, 7)]) as seen:
            payload = json.loads(_run(tools._tg_get_unread({"collection": "C"})))

        assert seen["collection"] == "C"
        whole_chat, thread = seen["fetch"][0], seen["fetch"][1]

        assert whole_chat.get("reply_to") is None, "a whole-chat member reads openly"
        assert whole_chat.get("min_id") == 5, "a chat member is floored at the chat read pointer"

        assert thread["reply_to"] == 7, "a thread member reads within its topic"
        assert "min_id" not in thread, (
            "a thread must not be floored by the chat-wide read pointer: that pointer can "
            "sit above messages the topic never delivered, and the thread would then report "
            "itself empty while its badge is still lit"
        )

        assert [entry.get("thread_id") for entry in payload["chats"]] == [None, "7"]


def test_unread_of_a_folder_still_reads_whole_dialogs():
    """The folder path must keep its old shape: no scopes, no thread ids."""
    tools = _tools()
    with isolated_state():
        dialog = _FakeDialog()

        @contextlib.asynccontextmanager
        async def fake_tool_client():
            yield object()

        async def fake_select_dialogs(_client, _token):
            return None, [dialog]

        originals = {n: getattr(tools, n) for n in ("tool_client", "_select_dialogs",
                                                    "_read_messages", "dialog_summary",
                                                    "dialog_waiting", "dialog_is_muted")}
        fetched = []
        tools.tool_client = fake_tool_client
        tools._select_dialogs = fake_select_dialogs
        tools.dialog_summary = lambda _d: {"id": "111"}
        tools.dialog_waiting = lambda _d: True
        tools.dialog_is_muted = lambda _d: False

        async def fake_read(_client, _entity, *, limit, since=None, until=None, **kwargs):
            fetched.append(dict(kwargs))
            return [{"id": 1, "out": False}]

        tools._read_messages = fake_read
        try:
            payload = json.loads(_run(tools._tg_get_unread({})))
        finally:
            for name, value in originals.items():
                setattr(tools, name, value)

        assert fetched[0].get("reply_to") is None
        assert "thread_id" not in payload["chats"][0]
    tools = _tools()
    with isolated_state():
        with _faked_marking(tools) as seen:
            for bad in (0, -3, "later"):
                payload = json.loads(_run(tools._tg_mark_summarized({"chat": "c", "up_to": bad})))
                assert "up_to must be" in payload["error"]
        assert seen["acks"] == []


def _digest_payload(chats: int, per_chat: int) -> dict[str, Any]:
    """The shape ``tg_read_folder`` returns, sized like the production one."""
    return {
        "folder": {"id": 3, "title": "it/ai"},
        "chat_count": chats,
        "read_receipts_sent": False,
        "since_last_digest": True,
        "chats": [
            {
                "id": f"-100{index}",
                "name": f"chat {index}",
                "unread": index,
                "messages": [
                    {"id": number, "date": "2026-09-30T00:00:00+00:00", "text": "x" * 700}
                    for number in range(per_chat)
                ],
            }
            for index in range(chats)
        ],
    }


def test_an_oversized_digest_is_trimmed_instead_of_spilled():
    """A folder-wide digest has to fit in one turn.

    41 chats x 50 messages is what ``tg_read_folder`` actually returned: 299 KB,
    past Hermes' spillover threshold, which hands the model a stub instead of the
    data — it then writes code to read the spilled file and spends the turn
    recovering what it had already asked for. Trimming here is the difference
    between one call and that hunt.
    """
    payload = _digest_payload(chats=41, per_chat=50)
    assert len(json.dumps(payload)) > 300_000, "the fixture must reproduce the real size"

    tools = _tools()
    with isolated_state():
        text = tools._json(payload)
        parsed = json.loads(text)
        budget = tools._RESULT_BUDGET_CHARS

    assert len(text) <= budget, f"still {len(text)} chars, over the {budget} budget"
    assert parsed["result_budget"]["trimmed"] is True
    assert parsed["folder"] == {"id": 3, "title": "it/ai"}
    assert parsed["chat_count"] == 41, "the summary is what the caller came for"

    first = parsed["chats"][0]
    assert first["name"] == "chat 0"
    assert first["unread"] == 0
    assert len(first["messages"]) == tools._TRIMMED_MESSAGES_PER_CHAT
    assert first["messages_omitted"] == 50 - tools._TRIMMED_MESSAGES_PER_CHAT
    assert first["messages"][-1]["id"] == 49, "the newest messages are the ones kept"


def test_a_result_that_fits_is_returned_untouched():
    """The budget is a floor on what survives, not a rewrite of every answer."""
    payload = {"chat": "it/ai", "messages": [{"id": 1, "text": "hello"}]}
    tools = _tools()
    with isolated_state():
        assert json.loads(tools._json(payload)) == payload


def test_a_payload_the_trimmer_cannot_shrink_reports_instead_of_mangling():
    """Last resort: a valid error beats invalid JSON or a silent cut."""
    payload = {"odd_shape": ["x" * 300] * 400}
    tools = _tools()
    with isolated_state():
        text = tools._json(payload)
        parsed = json.loads(text)

    assert len(text) <= tools._RESULT_BUDGET_CHARS
    assert "error" in parsed
    assert "hint" in parsed


def test_summary_only_folder_read_never_fetches_messages():
    """``messages_per_chat=0`` answers "what is in this folder" without the chats.

    Without it the only folder-wide call dragged every message along — 41 chats,
    299 KB — and the turn received a spillover stub instead of a chat list.
    """
    tools = _tools()
    with isolated_state():
        # More chats than the message-oriented default of 30: the summary-only form
        # has to report the folder, not the first 30 of it.
        dialogs = [_FakeDialog() for _ in range(41)]
        originals = {
            name: getattr(tools, name)
            for name in ("tool_client", "_select_dialogs", "_read_messages", "dialog_summary")
        }

        @contextlib.asynccontextmanager
        async def fake_tool_client():
            yield object()

        class _Folder:
            id = 3
            title = "it/ai"

        async def fake_select_dialogs(_client, _token):
            return _Folder(), dialogs

        async def explode(*_args, **_kwargs):
            raise AssertionError("messages_per_chat=0 must not read any messages")

        tools.tool_client = fake_tool_client
        tools._select_dialogs = fake_select_dialogs
        tools._read_messages = explode
        tools.dialog_summary = lambda dialog: {"id": str(id(dialog)), "name": "chat"}
        try:
            payload = json.loads(
                _run(tools._tg_read_folder({"folder": "it/ai", "messages_per_chat": 0}))
            )
        finally:
            for name, value in originals.items():
                setattr(tools, name, value)

    assert len(payload["chats"]) == 41, "the whole folder, not the first 30 of it"
    assert all(chat["messages"] == [] for chat in payload["chats"])
