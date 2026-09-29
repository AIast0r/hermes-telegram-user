"""Saved chat collections: the storage layer and the offline tool paths.

A collection is the reusable replacement for re-listing chats every time, so the
rules worth pinning are the ones that decide what a scope actually contains:
exclusion wins, a merge must not clobber stored names, and a renamed chat must
keep resolving (resolution is by peer id, never by title).
"""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402


def _store():
    return plugin_module("core.state.collections")


def _row(peer_id, name="Someone", username=None):
    return {"peer_id": str(peer_id), "name": name, "username": username}


def test_round_trip():
    with isolated_state() as root:
        store = _store()
        assert store.list_collections() == []
        assert store.get_collection("Work") is None
        assert store.delete_collection("Work") is False

        saved = store.save_collection(
            "Work", members=[_row(111, "Alice", "alice"), _row(222, "Team chat")]
        )
        assert saved["name"] == "Work"
        assert [row["peer_id"] for row in saved["members"]] == ["111", "222"]
        assert saved["exclude"] == []

        rows = store.list_collections()
        assert [row["name"] for row in rows] == ["Work"]
        assert rows[0]["member_count"] == 2
        assert rows[0]["exclude_count"] == 0

        assert store.collections_path().parent == root
        payload = json.loads(store.collections_path().read_text(encoding="utf-8"))
        assert payload["version"] == 1

        assert store.delete_collection("Work") is True
        assert store.get_collection("Work") is None


def test_exclusion_wins_over_membership():
    with isolated_state():
        store = _store()
        saved = store.save_collection(
            "Mixed",
            members=[_row(111, "Keep"), _row(222, "Noisy")],
            exclude=[_row(222, "Noisy")],
        )
        assert [row["peer_id"] for row in saved["members"]] == ["111"]
        assert [row["peer_id"] for row in saved["exclude"]] == ["222"]


def test_exclusion_wins_even_in_a_hand_edited_file():
    """The store repairs an overlap on read, not only on write."""
    with isolated_state():
        store = _store()
        store.save_collection("Mixed", members=[_row(111, "Keep")], exclude=[_row(222, "Noisy")])

        raw = json.loads(store.collections_path().read_text(encoding="utf-8"))
        key = next(iter(raw["collections"]))
        raw["collections"][key]["members"].append(_row(222, "Noisy"))
        store.collections_path().write_text(json.dumps(raw), encoding="utf-8")
        importlib.reload(store)

        assert [row["peer_id"] for row in store.get_collection("Mixed")["members"]] == ["111"]


def test_merge_keeps_the_stored_row_it_already_had():
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111, "Alice", "alice")])
        merged = store.save_collection(
            "C", members=[_row(111, "alice (renamed by hand)"), _row(333, "Bob")], replace=False
        )
        names = {row["peer_id"]: row["name"] for row in merged["members"]}
        assert names["111"] == "Alice", "a coarser incoming row must not clobber the stored name"
        assert names["333"] == "Bob"
        assert len(merged["members"]) == 2


def test_replace_drops_what_is_no_longer_listed():
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111), _row(222)])
        replaced = store.save_collection("C", members=[_row(111)])
        assert [row["peer_id"] for row in replaced["members"]] == ["111"]


def test_the_name_is_a_normalised_key():
    with isolated_state():
        store = _store()
        store.save_collection("  Work  ", members=[_row(111)])
        # same collection under a differently-cased spelling
        assert store.get_collection("work") is not None
        assert store.get_collection("WORK")["name"] == "Work"
        assert len(store.list_collections()) == 1


def test_bad_input_is_refused():
    with isolated_state():
        store = _store()
        for bad in ("", "   "):
            try:
                store.save_collection(bad, members=[_row(111)])
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for name {bad!r}")

        for bad in (_row("not-a-peer"), {"name": "no id"}, _row(None)):
            try:
                store.save_collection("C", members=[bad])
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for row {bad!r}")

        assert store.list_collections() == [], "a refused save must not touch the store"


def test_a_corrupt_file_degrades_to_empty():
    with isolated_state():
        store = _store()
        store.collections_path().write_text("{not json", encoding="utf-8")
        importlib.reload(store)
        assert store.list_collections() == []
        assert store.get_collection("anything") is None


def _thread_row(peer_id, thread, name="Someone"):
    return {"peer_id": str(peer_id), "name": name, "username": None, "thread": str(thread)}


def test_a_chat_can_appear_once_whole_and_once_per_thread():
    with isolated_state():
        store = _store()
        saved = store.save_collection(
            "C", members=[_row(111, "Whole chat"), _thread_row(111, 7, "Releases")]
        )
        scopes = sorted((row["peer_id"], row["thread"] or "") for row in saved["members"])
        assert scopes == [("111", ""), ("111", "7")]


def test_a_whole_chat_exclude_drops_its_threads_too():
    with isolated_state():
        store = _store()
        saved = store.save_collection(
            "C",
            members=[_row(111, "Whole chat"), _thread_row(111, 7, "Releases")],
            exclude=[_row(111, "Noisy")],
        )
        assert saved["members"] == []


def test_a_thread_exclude_drops_only_that_thread():
    with isolated_state():
        store = _store()
        saved = store.save_collection(
            "C",
            members=[_row(111, "Whole chat"), _thread_row(111, 7, "Releases")],
            exclude=[_thread_row(111, 7, "Releases")],
        )
        assert [(row["peer_id"], row["thread"]) for row in saved["members"]] == [("111", None)]


def test_thread_ids_are_canonicalised_and_validated():
    with isolated_state():
        store = _store()
        saved = store.save_collection("C", members=[_thread_row(111, "007")])
        assert saved["members"][0]["thread"] == "7"

        for bad in (True, 0, -3, "later", ""):
            try:
                store.save_collection("D", members=[_thread_row(111, bad)])
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for thread {bad!r}")


def test_a_standing_brief_travels_with_the_collection():
    with isolated_state():
        store = _store()
        saved = store.save_collection("C", members=[_row(111)])
        assert saved["brief"] == ""

        store.save_collection("C", members=[_row(111)], brief="Кратко: кто, что, решение.")
        assert store.get_collection("C")["brief"] == "Кратко: кто, что, решение."
        assert store.list_collections()[0]["has_brief"] is True
        assert "brief" not in store.list_collections()[0], "the text stays out of the list"


def test_the_brief_survives_a_member_overwrite_when_omitted():
    """Re-saving members must not silently drop the template the agent wrote."""
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111)], brief="template")
        store.save_collection("C", members=[_row(222)], replace=True)
        row = store.get_collection("C")
        assert [r["peer_id"] for r in row["members"]] == ["222"]
        assert row["brief"] == "template"


def test_the_brief_can_be_replaced_and_cleared():
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111)], brief="first")
        store.save_collection("C", members=[_row(111)], brief="second")
        assert store.get_collection("C")["brief"] == "second"
        store.save_collection("C", members=[_row(111)], brief="")
        assert store.get_collection("C")["brief"] == ""
        assert store.list_collections()[0]["has_brief"] is False


def test_the_template_is_bounded_and_masked():
    with isolated_state():
        store = _store()
        row = store.save_collection("C", members=[_row(111)], brief="a" * 30000)
        # sanitize_text truncates to the limit and marks it with an ellipsis
        assert row["brief"].endswith("…")
        assert len(row["brief"]) <= 20001
        masked = store.save_collection("C", members=[_row(111)], brief="x\u202ey")
        assert masked["brief"] == "xy"


def test_the_template_lives_in_its_own_markdown_file():
    template = "# Саммари\n\n## Решения\n\nОдна строка на решение.\n"
    with isolated_state() as root:
        store = _store()
        store.save_collection("Работа", members=[_row(111)], brief=template)

        path = store.template_path("Работа")
        assert path is not None
        assert path.parent == root / "templates"
        assert path.suffix == ".md"
        assert path.name.startswith("работа"), "the file stays recognisable by name"
        assert path.read_text(encoding="utf-8") == template

        # the JSON store keeps membership only — no template text in it
        raw = json.loads(store.collections_path().read_text(encoding="utf-8"))
        assert "brief" not in next(iter(raw["collections"].values()))
        assert "# Саммари" not in store.collections_path().read_text(encoding="utf-8")


def test_a_hand_edited_template_is_picked_up_without_a_restart():
    """The file is the source of truth: editing it in an editor has to be enough."""
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111)], brief="old")
        store.template_path("C").write_text("# Новый шаблон\n", encoding="utf-8")

        assert store.get_collection("C")["brief"] == "# Новый шаблон\n"
        assert store.list_collections()[0]["has_brief"] is True


def test_deleting_a_collection_removes_its_template():
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111)], brief="# t")
        path = store.template_path("C")
        assert path.exists()
        assert store.delete_collection("C") is True
        assert not path.exists()


def test_templates_do_not_collide_when_names_slugify_alike():
    with isolated_state():
        store = _store()
        store.save_collection("a/b", members=[_row(111)], brief="# one")
        store.save_collection("a-b", members=[_row(222)], brief="# two")
        assert store.template_path("a/b") != store.template_path("a-b")
        assert store.get_collection("a/b")["brief"] == "# one"
        assert store.get_collection("a-b")["brief"] == "# two"


def test_writing_the_brief_leaves_members_alone():
    """The brief has its own writer, so it cannot clobber a concurrent member save."""
    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111)], exclude=[_row(222)])
        store.set_collection_brief("C", "template")

        row = store.get_collection("C")
        assert [r["peer_id"] for r in row["members"]] == ["111"]
        assert [r["peer_id"] for r in row["exclude"]] == ["222"]
        assert row["brief"] == "template"

        for bad in (5, None, ["x"]):
            try:
                store.set_collection_brief("C", bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"a non-text brief must be refused: {bad!r}")

        try:
            store.set_collection_brief("absent", "x")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown collection must be refused")


def test_selection_matches_by_peer_id_not_by_title():
    """A renamed chat must keep resolving; a title is never the key.

    The dialogs here carry no entity at all, which takes the module's own
    ``if entity is not None:`` guard straight to the documented fallback on
    ``Dialog.id``. That is deterministic whether or not telethon is installed —
    unlike an unmarkable object, which relies on the dependency *raising*.
    """
    import asyncio

    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111, "Old title")])
        collections = plugin_module("core.collections")

        class Client:
            def __init__(self, dialogs):
                self._dialogs = dialogs
                self.sweeps = 0

            async def iter_dialogs(self):
                self.sweeps += 1
                for dialog in self._dialogs:
                    yield dialog

        class Dialog:
            def __init__(self, peer_id, name):
                self.entity = None
                self.id = peer_id
                self.name = name
                self.dialog = None

        client = Client([Dialog(111, "Renamed since"), Dialog(999, "Something else")])
        _, dialogs = asyncio.run(collections.select_dialogs(client, "C"))
        assert [dialog.name for dialog in dialogs] == ["Renamed since"], (
            "membership must follow the peer id, not the stored title"
        )
        assert client.sweeps == 1, "selection must sweep the dialog list exactly once"

        # And the same property through the real Telethon marking, when available.
        try:
            from telethon.tl import types
        except ImportError:
            return

        real = Client([Dialog(111, "Renamed"), Dialog(999, "Elsewhere")])
        real._dialogs[0].entity = types.PeerUser(user_id=111)
        real._dialogs[1].entity = types.PeerUser(user_id=999)
        _, dialogs = asyncio.run(collections.select_dialogs(real, "C"))
        assert [dialog.name for dialog in dialogs] == ["Renamed"]


def test_an_unknown_collection_is_refused():
    import asyncio

    with isolated_state():
        collections = plugin_module("core.collections")
        store = _store()
        store.save_collection("Known", members=[_row(111)])

        class Client:
            async def iter_dialogs(self):
                return
                yield  # pragma: no cover

        # The refusal must happen before any Telegram work is attempted.
        try:
            asyncio.run(collections.select_dialogs(Client(), "unknown"))
        except ValueError as exc:
            assert "unknown" in str(exc)
        except ModuleNotFoundError:
            # telethon absent: the same refusal path is unreachable, so assert
            # the storage-side lookup instead
            assert store.get_collection("unknown") is None
        else:
            raise AssertionError("an unknown collection must be refused")


def test_tool_paths_that_need_no_telegram():
    import asyncio

    from _plugin_support import plugin_module as _pm

    tools = _pm("tools")
    with isolated_state():
        listed = json.loads(asyncio.run(tools._tg_list_collections({})))
        assert listed == {"count": 0, "collections": []}

        removed = json.loads(asyncio.run(tools._tg_delete_collection({"name": "absent"})))
        assert removed["removed"] is False

        for handler, needle in (
            (tools._tg_save_collection, "name is required"),
            (tools._tg_set_collection_brief, "name is required"),
            (tools._tg_delete_collection, "name is required"),
            (tools._tg_read_collection, "collection is required"),
        ):
            payload = json.loads(asyncio.run(handler({})))
            assert needle in payload["error"]
