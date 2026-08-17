"""Auto-resume: re-submitting the same list continues its unfinished run.

Covers the fingerprint (what makes two submissions "the same work"), the
manifest RunState writes per run, find_resumable's selection rules, and the
_open_run_state seam both the CLI and the chat agent's run_qc go through.
"""
from __future__ import annotations

import json

import pytest

from ytqc.models import InputItem
from ytqc.pipeline.state import MANIFEST, RunState, fingerprint_items


def _items(*ids, type_="channel"):
    return [InputItem(id=i, type=type_) for i in ids]


# ── fingerprint ────────────────────────────────────────────────────────────
def test_fingerprint_is_order_and_duplicate_insensitive():
    a = fingerprint_items(_items("UC1", "UC2", "UC3"))
    b = fingerprint_items(_items("UC3", "UC1", "UC2", "UC1"))
    assert a == b


def test_fingerprint_changes_when_the_list_changes():
    base = fingerprint_items(_items("UC1", "UC2"))
    assert fingerprint_items(_items("UC1", "UC2", "UC3")) != base
    assert fingerprint_items(_items("UC1")) != base


def test_fingerprint_separates_video_and_channel_ids():
    assert (fingerprint_items(_items("X1", type_="video"))
            != fingerprint_items(_items("X1", type_="channel")))


# ── manifest ───────────────────────────────────────────────────────────────
def test_manifest_round_trips_and_keeps_created_at(tmp_path):
    st = RunState(str(tmp_path), run_id="run-man")
    st.write_manifest(fingerprint="abc", total_items=10, input_path="/tmp/list.csv")
    created = st.read_manifest()["created_at"]

    st.write_manifest(fingerprint="abc", total_items=10, input_path="/tmp/list.csv")
    man = st.read_manifest()
    assert man["fingerprint"] == "abc" and man["total_items"] == 10
    assert man["input_path"] == "/tmp/list.csv"
    assert man["created_at"] == created           # created_at is preserved
    assert man["updated_at"] >= created


def test_manifest_stores_the_work_list_for_self_contained_resume(tmp_path):
    """A run started from PASTED ids has no file to re-read, so the ids must
    live in the manifest — otherwise the unprocessed remainder exists nowhere
    on disk (state.jsonl only records what finished)."""
    items = _items("UC1", "UC2") + _items("vid1", type_="video")
    st = RunState(str(tmp_path), run_id="run-items")
    st.write_manifest(fingerprint=fingerprint_items(items), total_items=3, items=items)

    recovered = RunState(str(tmp_path), run_id="run-items").manifest_items()
    assert [(i.id, i.type) for i in recovered] == [
        ("UC1", "channel"), ("UC2", "channel"), ("vid1", "video")]


def test_pending_items_is_the_unfinished_remainder(tmp_path):
    items = _items("UC1", "UC2", "UC3")
    st = RunState(str(tmp_path), run_id="run-pending")
    st.write_manifest(fingerprint=fingerprint_items(items), total_items=3, items=items)
    st.mark("UC1", "SUNK")
    assert [i.id for i in st.pending_items()] == ["UC2", "UC3"]


def test_rewriting_a_manifest_never_drops_a_stored_list(tmp_path):
    """Resume rewrites the manifest; a caller that omits `items` (an older code
    path) must not wipe the only copy of the work list."""
    items = _items("UC1", "UC2")
    st = RunState(str(tmp_path), run_id="run-keep")
    st.write_manifest(fingerprint="fp", total_items=2, items=items)
    st.write_manifest(fingerprint="fp", total_items=2)          # no items passed
    assert [i.id for i in st.manifest_items()] == ["UC1", "UC2"]


def test_manifest_items_empty_when_none_were_stored(tmp_path):
    st = RunState(str(tmp_path), run_id="run-nolist")
    st.write_manifest(fingerprint="fp", total_items=2)
    assert st.manifest_items() == [] and st.pending_items() == []


def test_unreadable_manifest_reads_as_none(tmp_path):
    st = RunState(str(tmp_path), run_id="run-junk")
    (st.root / MANIFEST).write_text("{not json")
    assert st.read_manifest() is None


# ── done_count / find_resumable ────────────────────────────────────────────
def test_done_count_counts_only_sunk(tmp_path):
    st = RunState(str(tmp_path), run_id="run-count")
    st.mark("a", "EXTRACTED")
    st.mark("a", "SUNK")
    st.mark("b", "EXTRACTED")
    assert st.done_count() == 1


def test_find_resumable_returns_the_unfinished_matching_run(tmp_path):
    items = _items("UC1", "UC2", "UC3")
    fp = fingerprint_items(items)
    st = RunState(str(tmp_path), run_id="run-a")
    st.write_manifest(fingerprint=fp, total_items=len(items))
    st.mark("UC1", "SUNK")

    assert RunState.find_resumable(str(tmp_path), fp) == "run-a"


def test_find_resumable_ignores_a_completed_run(tmp_path):
    items = _items("UC1", "UC2")
    fp = fingerprint_items(items)
    st = RunState(str(tmp_path), run_id="run-done")
    st.write_manifest(fingerprint=fp, total_items=2)
    st.mark("UC1", "SUNK")
    st.mark("UC2", "SUNK")

    assert RunState.find_resumable(str(tmp_path), fp) is None


def test_find_resumable_ignores_a_different_list(tmp_path):
    st = RunState(str(tmp_path), run_id="run-other")
    st.write_manifest(fingerprint=fingerprint_items(_items("UC9")), total_items=1)
    assert RunState.find_resumable(str(tmp_path), fingerprint_items(_items("UC1"))) is None


def test_find_resumable_survives_foreign_and_corrupt_dirs(tmp_path):
    (tmp_path / "not-a-run").mkdir()
    bad = tmp_path / "run-bad"
    bad.mkdir()
    (bad / MANIFEST).write_text("{{{")
    fp = fingerprint_items(_items("UC1"))
    st = RunState(str(tmp_path), run_id="run-good")
    st.write_manifest(fingerprint=fp, total_items=1)
    assert RunState.find_resumable(str(tmp_path), fp) == "run-good"


def test_find_resumable_picks_the_most_recent_of_several(tmp_path):
    fp = fingerprint_items(_items("UC1", "UC2"))
    older = RunState(str(tmp_path), run_id="run-old")
    older.write_manifest(fingerprint=fp, total_items=2)
    newer = RunState(str(tmp_path), run_id="run-new")
    newer.write_manifest(fingerprint=fp, total_items=2)
    # force a clearly older timestamp on the first
    man = json.loads((older.root / MANIFEST).read_text())
    man["updated_at"] = 1.0
    (older.root / MANIFEST).write_text(json.dumps(man))

    assert RunState.find_resumable(str(tmp_path), fp) == "run-new"


def test_find_resumable_on_missing_dir_is_none(tmp_path):
    assert RunState.find_resumable(str(tmp_path / "nope"), "abc") is None


def test_find_completed_reports_a_finished_run(tmp_path):
    """Re-running a finished list is allowed, but the caller must be able to
    warn — silently redoing thousands of channels is hours of browser time."""
    items = _items("UC1", "UC2")
    fp = fingerprint_items(items)
    st = RunState(str(tmp_path), run_id="run-fin")
    st.write_manifest(fingerprint=fp, total_items=2)
    st.mark("UC1", "SUNK")
    assert RunState.find_completed(str(tmp_path), fp) is None    # not done yet
    st.mark("UC2", "SUNK")
    assert RunState.find_completed(str(tmp_path), fp) == "run-fin"
    assert RunState.find_resumable(str(tmp_path), fp) is None    # and not resumable


# ── _open_run_state (what run/run_qc actually call) ────────────────────────
def test_open_run_state_resumes_the_same_list(tmp_path):
    from ytqc.cli import _open_run_state
    items = _items("UC1", "UC2", "UC3")

    first = _open_run_state(str(tmp_path), items, input_path="list.csv")
    first.mark("UC1", "SUNK")

    second = _open_run_state(str(tmp_path), items, input_path="list-copy.csv")
    assert second.run_id == first.run_id
    assert second.is_done("UC1") is True
    assert second.done_count() == 1


def test_open_run_state_starts_fresh_when_asked(tmp_path):
    from ytqc.cli import _open_run_state
    items = _items("UC1", "UC2")
    first = _open_run_state(str(tmp_path), items)
    first.mark("UC1", "SUNK")

    second = _open_run_state(str(tmp_path), items, fresh=True)
    assert second.run_id != first.run_id
    assert second.done_count() == 0


def test_open_run_state_starts_fresh_for_a_different_list(tmp_path):
    from ytqc.cli import _open_run_state
    first = _open_run_state(str(tmp_path), _items("UC1", "UC2"))
    first.mark("UC1", "SUNK")
    second = _open_run_state(str(tmp_path), _items("UC1", "UC2", "UC3"))
    assert second.run_id != first.run_id


def test_open_run_state_writes_a_manifest_for_a_new_run(tmp_path):
    from ytqc.cli import _open_run_state
    items = _items("UC1")
    st = _open_run_state(str(tmp_path), items, input_path="/tmp/x.csv")
    man = st.read_manifest()
    assert man["fingerprint"] == fingerprint_items(items)
    assert man["total_items"] == 1 and man["input_path"] == "/tmp/x.csv"


def test_orchestrator_skips_done_items_on_resume(tmp_path):
    """End-to-end intent: a resumed run only works the unfinished remainder.
    (The orchestrator's own skip path is exercised live in
    tests/test_parallel_orchestrator.py; this pins the state contract it uses.)"""
    from ytqc.config import DEFAULT_CONFIG
    from ytqc.pipeline.orchestrator import Orchestrator

    st = RunState(str(tmp_path), run_id="run-skip")
    for i in ("UC1", "UC2"):
        st.mark(i, "SUNK")
    items = _items("UC1", "UC2", "UC3")
    orch = Orchestrator(DEFAULT_CONFIG.model_copy(deep=True), items, [], st)
    todo = [i for i in orch.items if not orch.state.is_done(i.id)]
    assert [i.id for i in todo] == ["UC3"]
