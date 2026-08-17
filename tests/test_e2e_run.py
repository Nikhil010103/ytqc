"""End-to-end integration: items → lanes → workers → channel_flow → qc_output.csv,
with a mid-run interruption and a resume.

Everything below the browser and the LLM is REAL — the orchestrator, both flows,
the validator, RunState checkpointing and the QC sink. Only the two external
edges are faked (extraction bundles, LLM responses). This is the test that would
catch the wiring bugs unit tests miss: a field that never reaches the file, a
resume that re-does finished work, a duplicated row.
"""
from __future__ import annotations

import csv

import pytest

from ytqc.config import DEFAULT_CONFIG
from ytqc.models import ChannelExtract, ChannelVideoTile, InputItem
from ytqc.pipeline.orchestrator import Orchestrator
from ytqc.pipeline.state import RunState, fingerprint_items
from ytqc.sinks.qc_sink import COLUMNS, FILENAME, QcCsvSink

from tests.fakes import FakeLLMClient, good_content_output, good_vision_evidence


class _StubKimi:
    def __init__(self, cfg, rate_bucket=None, halt=None): pass
    def navigate(self, *a, **k): pass
    def item_pause(self): pass
    def close(self): pass


def _cfg():
    c = DEFAULT_CONFIG.model_copy(deep=True)
    c.pipeline.browser_lanes = 2
    c.pipeline.analysis_workers = 3
    c.pipeline.lane_stagger_s = 0.0
    return c


def _extract(item: InputItem) -> ChannelExtract:
    """A believable channel bundle; every third channel is Shorts-only."""
    n = int(item.id[2:])
    shorts_only = n % 3 == 0
    return ChannelExtract(
        channel_id=item.id, ok=True, title=f"Channel {n}",
        subscribers=1000 * (n + 1), country="India",
        joined_date="Mar 3, 2015", video_count=10,
        recent_videos=[ChannelVideoTile(video_id=f"v{n}{i}", title=f"title {i}",
                                        is_short=shorts_only) for i in range(8)],
        grid_screenshots_b64=["fakeb64"],
        is_shorts_only=shorts_only,
        shorts_count=8 if shorts_only else 0,
        long_form_count=0 if shorts_only else 8,
    )


def _llm():
    return FakeLLMClient(by_system={
        "visual content analyst": good_vision_evidence(),
        "CHANNEL-level QC brief": good_content_output(
            tier_1="Gaming", tier_2="fps gameplay", language="en",
            suitable_age_group="all ages"),
    })


def _run(items, out_dir, monkeypatch, state, halt_after_extractions=None,
         on_extract=None):
    """Drive a real Orchestrator over `items`.

    `halt_after_extractions` interrupts the run the way a captcha halt or Ctrl-C
    does — by halting the LANES mid-extraction. (Halting after N items are sunk
    wouldn't interrupt anything: extraction of a fast list finishes long before
    analysis does, and the orchestrator deliberately still analyses everything
    already extracted rather than wasting the scrape.)"""
    monkeypatch.setattr("ytqc.pipeline.orchestrator.KimiClient", _StubKimi)
    from rich.console import Console

    sink = QcCsvSink()
    sink.open(state.run_id, out_dir)
    orch = Orchestrator(_cfg(), items, [sink], state, console=Console(quiet=True))
    orch.llm = _llm()                       # real flows, faked model
    seen: list[str] = []

    def _extract_fn(kimi, item):
        seen.append(item.id)
        if on_extract is not None:
            on_extract(item.id)
        if halt_after_extractions is not None and len(seen) >= halt_after_extractions:
            orch._halt.set()
        return _extract(item)

    orch._restore_or_extract = _extract_fn
    try:
        orch.run()
    finally:
        sink.close()
    return orch, seen


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_full_run_writes_one_correct_qc_file(tmp_path, monkeypatch):
    out = str(tmp_path)
    items = [InputItem(id=f"UC{i}", type="channel") for i in range(6)]
    state = RunState(out)
    state.write_manifest(fingerprint=fingerprint_items(items), total_items=len(items))

    _run(items, out, monkeypatch, state)

    run_dir = tmp_path / state.run_id
    # exactly ONE csv deliverable
    assert [p.name for p in run_dir.iterdir() if p.suffix == ".csv"] == [FILENAME]
    rows = _rows(run_dir / FILENAME)
    assert len(rows) == 6
    assert list(rows[0].keys()) == COLUMNS
    assert sorted(r["Channel ID"] for r in rows) == [f"UC{i}" for i in range(6)]

    by_id = {r["Channel ID"]: r for r in rows}
    # classification + stats made it all the way through
    assert by_id["UC1"]["Tier 1"] == "Gaming"
    assert by_id["UC1"]["Tier 2"] == "fps gameplay"
    assert by_id["UC1"]["Subscribers"] == "2000"
    assert by_id["UC1"]["Channel Country"] == "India"
    assert by_id["UC1"]["Brand Safety Status"] == "Safe"
    assert by_id["UC1"]["Age Marking"] == "All Ages"
    assert by_id["UC1"]["Language"] == "en"
    # Shorts-only channels (0, 3) flagged; the rest not
    assert by_id["UC0"]["Shorts"] == "Yes" and by_id["UC3"]["Shorts"] == "Yes"
    assert by_id["UC1"]["Shorts"] == "No" and by_id["UC2"]["Shorts"] == "No"
    # every item checkpointed, and the full record kept alongside the slim csv
    assert state.done_count() == 6
    assert state.load_artifact("UC1", "sunk.json")["tier_1"] == "Gaming"


def test_halted_run_reports_the_shortfall_instead_of_looking_finished(tmp_path, monkeypatch):
    """A captcha/bridge halt lets run() return NORMALLY with items still queued.
    Without an explicit signal that reads as a finished run, and the unprocessed
    channels get forgotten — which is exactly what happened in the field."""
    out = str(tmp_path)
    items = [InputItem(id=f"UC{i}", type="channel") for i in range(20)]
    state = RunState(out)
    state.write_manifest(fingerprint=fingerprint_items(items), total_items=len(items))

    orch, _seen = _run(items, out, monkeypatch, state, halt_after_extractions=4)

    assert orch.stats.remaining > 0
    assert orch.stats.complete is False
    assert orch.stats.stopped_reason              # a plain-language cause
    assert orch.stats.done + orch.stats.remaining == 20


def test_completed_run_reports_no_shortfall(tmp_path, monkeypatch):
    out = str(tmp_path)
    items = [InputItem(id=f"UC{i}", type="channel") for i in range(6)]
    state = RunState(out)
    state.write_manifest(fingerprint=fingerprint_items(items), total_items=len(items))

    orch, _ = _run(items, out, monkeypatch, state)

    assert orch.stats.remaining == 0
    assert orch.stats.complete is True
    assert orch.stats.stopped_reason == ""


def test_resumed_run_counts_only_its_own_remainder(tmp_path, monkeypatch):
    """`remaining` is about the items THIS run was handed (the unfinished tail),
    not the whole file — otherwise a resume always looks short."""
    out = str(tmp_path)
    items = [InputItem(id=f"UC{i}", type="channel") for i in range(10)]
    state = RunState(out)
    state.write_manifest(fingerprint=fingerprint_items(items), total_items=len(items))
    _run(items, out, monkeypatch, state, halt_after_extractions=3)

    from ytqc.cli import _open_run_state
    resumed = _open_run_state(out, items)
    orch, _ = _run(items, out, monkeypatch, resumed)

    assert orch.stats.complete is True
    assert resumed.done_count() == 10


def test_handle_ids_survive_the_whole_pipeline(tmp_path, monkeypatch):
    """An @handle channel must reach both the csv and its JSON checkpoint
    unescaped — it's the join key against the QC team's other sheets."""
    out = str(tmp_path)
    items = [InputItem(id="@mrbeast", type="channel")]
    state = RunState(out)
    state.write_manifest(fingerprint=fingerprint_items(items), total_items=1)

    _run(items, out, monkeypatch, state)

    rows = _rows(tmp_path / state.run_id / FILENAME)
    assert rows[0]["Channel ID"] == "@mrbeast"
    assert state.load_artifact("@mrbeast", "sunk.json")["id"] == "@mrbeast"


def test_interrupted_run_resumes_without_redoing_or_duplicating(tmp_path, monkeypatch):
    out = str(tmp_path)
    items = [InputItem(id=f"UC{i}", type="channel") for i in range(20)]

    # ── first attempt: interrupted mid-extraction ─────────────────────────
    first = RunState(out)
    first.write_manifest(fingerprint=fingerprint_items(items), total_items=len(items))
    _run(items, out, monkeypatch, first, halt_after_extractions=5)

    done_after_halt = first.done_count()
    assert 0 < done_after_halt < 20, "the halt should leave real work unfinished"
    finished_ids = {i.id for i in items if first.is_done(i.id)}
    # rows written before the interruption survived it
    assert len(_rows(tmp_path / first.run_id / FILENAME)) == done_after_halt

    # ── re-running the SAME list finds and continues that run ─────────────
    from ytqc.cli import _open_run_state
    resumed = _open_run_state(out, items)
    assert resumed.run_id == first.run_id
    assert resumed.done_count() == done_after_halt

    _orch, extracted = _run(items, out, monkeypatch, resumed)

    # finished channels were never re-extracted
    assert not (set(extracted) & finished_ids), "resume re-did already-finished work"
    assert sorted(extracted) == sorted({i.id for i in items} - finished_ids)

    # one file, one row per channel, no duplicates
    rows = _rows(tmp_path / first.run_id / FILENAME)
    ids = [r["Channel ID"] for r in rows]
    assert len(ids) == 20 and len(set(ids)) == 20
    assert resumed.done_count() == 20
    # and the completed run is no longer offered for resume
    assert RunState.find_resumable(out, fingerprint_items(items)) is None
