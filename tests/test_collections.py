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


def test_selection_matches_by_peer_id_not_by_title():
    """A renamed chat must keep resolving; a title is never the key."""
    import asyncio

    with isolated_state():
        store = _store()
        store.save_collection("C", members=[_row(111, "Old title")])
        collections = plugin_module("core.collections")

        class Dialog:
            def __init__(self, entity, name, peer_id):
                self.entity = entity
                self.name = name
                self.id = peer_id
                self.dialog = None

        class Client:
            def __init__(self, dialogs):
                self._dialogs = dialogs
                self.sweeps = 0

            async def iter_dialogs(self):
                self.sweeps += 1
                for dialog in self._dialogs:
                    yield dialog

        try:
            from telethon.tl import types
            from telethon import utils
        except ImportError:
            # Without telethon the module falls back to each dialog's own id, so an
            # empty sweep is simply an empty selection — never a crash.
            empty = Client([])
            _, dialogs = asyncio.run(collections.select_dialogs(empty, "C"))
            assert dialogs == []
            return

        renamed = types.PeerUser(user_id=111)
        other = types.PeerUser(user_id=999)
        client = Client(
            [
                Dialog(renamed, "Renamed since", utils.get_peer_id(renamed)),
                Dialog(other, "Something else", utils.get_peer_id(other)),
            ]
        )
        _, dialogs = asyncio.run(collections.select_dialogs(client, "C"))
        assert [dialog.name for dialog in dialogs] == ["Renamed since"]
        assert client.sweeps == 1, "selection must sweep the dialog list exactly once"


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
            (tools._tg_delete_collection, "name is required"),
            (tools._tg_read_collection, "collection is required"),
        ):
            payload = json.loads(asyncio.run(handler({})))
            assert needle in payload["error"]
