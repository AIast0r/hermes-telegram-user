"""Digest watermarks: monotonic marks and gap-aware advancement.

The invariant under test is the one that matters: a mark may only move forward,
and a fetch cut short may record a hole but must never advance past it — an
over-eager advance silently skips messages forever.
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _plugin_support import isolated_state, plugin_module  # noqa: E402

PEER = "123456789"
OTHER = "987654321"


def _marks():
    return plugin_module("core.state.watermarks")


def test_unknown_peer_has_no_mark():
    with isolated_state():
        marks = _marks()
        assert marks.get_mark(PEER) is None
        assert marks.list_marks() == []
        bounds = marks.resume_bounds(PEER)
        assert bounds["min_id"] == 0
        assert bounds["max_id"] is None
        assert bounds["has_hole"] is False


def test_a_completed_walk_advances_the_mark():
    with isolated_state():
        marks = _marks()
        record = marks.advance(PEER, contiguous=100)
        assert record["contiguous"] == 100
        assert record["has_hole"] is False
        assert marks.get_mark(PEER)["contiguous"] == 100
        assert marks.resume_bounds(PEER)["min_id"] == 100


def test_a_lower_report_cannot_lower_the_mark():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=100)
        lowered = marks.advance(PEER, contiguous=50)
        assert lowered["contiguous"] == 100
        assert marks.get_mark(PEER)["contiguous"] == 100


def test_a_truncated_walk_records_a_hole_without_advancing():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=100)
        record = marks.advance(PEER, contiguous=200, top=500)

        assert record["contiguous"] == 100, "a cut-short walk must not advance the mark"
        assert record["has_hole"] is True
        assert record["pending_from_id"] == 200
        assert record["pending_top_id"] == 500

        bounds = marks.resume_bounds(PEER)
        assert bounds["min_id"] == 100
        assert bounds["max_id"] == 200, "the next fetch must close the hole first"
        assert bounds["has_hole"] is True


def test_a_smaller_page_cannot_shrink_an_open_hole():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=100)
        marks.advance(PEER, contiguous=200, top=500)
        marks.advance(PEER, contiguous=300, top=400)

        record = marks.get_mark(PEER)
        assert record["pending_from_id"] == 200, "a hole only ever grows"
        assert record["pending_top_id"] == 500


def test_closing_the_hole_advances_and_clears_it():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=100)
        marks.advance(PEER, contiguous=200, top=500)
        closed = marks.advance(PEER, contiguous=600)

        assert closed["contiguous"] == 600
        assert closed["has_hole"] is False
        assert closed["pending_from_id"] is None
        bounds = marks.resume_bounds(PEER)
        assert bounds["has_hole"] is False
        assert bounds["max_id"] is None


def test_marks_are_isolated_per_peer():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=10)
        marks.advance(OTHER, contiguous=20)

        assert marks.get_mark(PEER)["contiguous"] == 10
        assert marks.get_mark(OTHER)["contiguous"] == 20
        assert {row["peer_id"] for row in marks.list_marks()} == {PEER, OTHER}

        assert marks.forget_mark(PEER) is True
        assert marks.get_mark(PEER) is None
        assert marks.get_mark(OTHER)["contiguous"] == 20


def test_forget_all_clears_every_mark():
    with isolated_state():
        marks = _marks()
        marks.advance(PEER, contiguous=1)
        marks.advance(OTHER, contiguous=2)
        assert marks.forget_all() == 2
        assert marks.list_marks() == []
        assert marks.forget_mark(PEER) is False


def test_state_is_persisted_and_reloaded():
    with isolated_state() as root:
        marks = _marks()
        marks.advance(PEER, contiguous=42)

        path = root / "digest_watermarks.json"
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert payload["marks"][PEER]["contiguous"] == 42

        # simulate a fresh process so the file is read back rather than the cache
        importlib.reload(marks)
        assert marks.get_mark(PEER)["contiguous"] == 42


def test_a_corrupt_file_degrades_to_no_marks_instead_of_raising():
    with isolated_state() as root:
        marks = _marks()
        (root / "digest_watermarks.json").write_text("{not json", encoding="utf-8")
        importlib.reload(marks)

        assert marks.get_mark(PEER) is None
        assert marks.list_marks() == []
        # and the store still works afterwards
        marks.advance(PEER, contiguous=5)
        assert marks.get_mark(PEER)["contiguous"] == 5


def test_unusable_peer_ids_and_bounds_are_refused():
    with isolated_state():
        marks = _marks()
        for bad in ("not-a-name", "", None, 1.5, "@channel"):
            try:
                marks.advance(bad, contiguous=1)
            except ValueError:
                pass
            else:
                raise AssertionError(f"expected ValueError for peer id {bad!r}")

        try:
            marks.advance(PEER, contiguous=10, top=1)
        except ValueError:
            pass
        else:
            raise AssertionError("top below contiguous must be refused")


def test_the_lock_serialises_two_writers():
    with isolated_state():
        marks = _marks()
        state = {"inside": 0, "peak": 0}

        async def worker():
            async with marks.watermark_lock(PEER):
                state["inside"] += 1
                state["peak"] = max(state["peak"], state["inside"])
                await asyncio.sleep(0.02)
                state["inside"] -= 1

        async def run():
            await asyncio.gather(*(worker() for _ in range(4)))

        asyncio.run(run())
        assert state["peak"] == 1, "the per-peer lock must not admit two writers at once"
