"""Agent tools: path resolution, dispatch, run_qc (stubbed orchestrator),
inspect_input, show_results, list_runs. Hermetic — no browser, no LLM."""
import csv

import pytest
from rich.console import Console

from ytqc.agent.tools import (
    AgentContext,
    ToolRegistry,
    _looks_like_file_path,
    _resolve_path,
)
from ytqc.config import load_config
from ytqc.pipeline.orchestrator import RunStats


def _ctx(tmp_path):
    cfg = load_config()
    cfg.output_dir = str(tmp_path / "runs")
    return AgentContext(cfg=cfg, console=Console(quiet=True), output_dir=cfg.output_dir)


def _write_csv(p, rows):
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "type"])
        w.writeheader()
        w.writerows(rows)


# ── path resolution ───────────────────────────────────────────────────────

def test_resolve_path_expanduser(tmp_path, monkeypatch):
    f = tmp_path / "c.csv"
    f.write_text("id,type\nx,channel\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _resolve_path("~/c.csv") == str(f)


def test_resolve_path_missing_raises():
    with pytest.raises(FileNotFoundError):
        _resolve_path("/nope/missing-12345.csv")


# ── dispatch + error isolation ──────────────────────────────────────────────

def test_dispatch_unknown_tool_returns_error(tmp_path):
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("frobnicate", {})
    assert "error" in out and "unknown tool" in out["error"]


def test_dispatch_aliases_and_coerces(tmp_path, monkeypatch):
    # inspect_input via the alias "file" and a real file
    f = tmp_path / "in.csv"
    _write_csv(f, [{"id": "a", "type": "channel"}, {"id": "b", "type": "video"}])
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("inspect_input", {"file": str(f)})
    assert out["total"] == 2 and out["channels"] == 1 and out["videos"] == 1


# ── run_qc with a stubbed Orchestrator ──────────────────────────────────────

def _stub_orch(monkeypatch, *, sink_ids, stats):
    """Install a stub Orchestrator that checkpoints `sink_ids` (as a real run
    does) and returns `stats`. Checkpointing matters: run_qc derives how much of
    the user's list is finished from RunState, not from the returned counts."""
    class StubOrch:
        def __init__(self, cfg, items, sinks, state, **k):
            self.state = state
        def run(self):
            for i in sink_ids:
                self.state.mark(i, "SUNK")
            return stats
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", StubOrch)


def test_run_qc_runs_and_summarizes(tmp_path, monkeypatch):
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}, {"id": "v2", "type": "video"}])
    _stub_orch(monkeypatch, sink_ids=["v1", "v2"],
               stats=RunStats(done=2, errors=0, unsafe=1, needs_review=1,
                              tier_counts={"Music": 2}))

    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    out_dir = str(tmp_path / "out")
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": "2", "output_dir": out_dir})
    assert out["items"] == 2 and out["unsafe"] == 1
    assert out["tier_distribution"] == {"Music": 2}
    assert out["run_id"] and ctx.last_run_id == out["run_id"]   # last_run_id updated
    # the chosen folder is reported back so the chat reply can state it
    assert out["output_dir"] == out_dir
    assert out["results_path"].startswith(out_dir)
    # everything processed → the assistant may report it as finished
    assert out["status"] == "complete"
    assert "remaining" not in out and "warning" not in out


def test_run_qc_reports_a_stopped_run_as_incomplete(tmp_path, monkeypatch):
    """The field bug: a run halted partway returned a result that read exactly
    like a finished one, and the assistant replied "All set!" while half the
    channels had never been QC'd."""
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": f"v{i}", "type": "video"} for i in range(49)])
    _stub_orch(monkeypatch, sink_ids=[f"v{i}" for i in range(24)],
               stats=RunStats(done=24, errors=1, unsafe=6, needs_review=24,
                              tier_counts={"Music": 8}, remaining=25,
                              stopped_reason="bot-check/stress halt"))

    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": 2,
                                  "output_dir": str(tmp_path / "out")})
    assert out["status"] == "incomplete"
    assert out["remaining"] == 25
    assert out["completed_total"] == 24 and out["total_in_file"] == 49
    assert "bot-check" in out["stopped_reason"]
    assert "DO NOT tell the user the run finished" in out["warning"]
    assert "run_qc again" in out["how_to_resume"]


def test_run_qc_resume_completes_and_clears_the_incomplete_flag(tmp_path, monkeypatch):
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": f"v{i}", "type": "video"} for i in range(5)])
    out_dir = str(tmp_path / "out")
    reg = ToolRegistry(_ctx(tmp_path))

    _stub_orch(monkeypatch, sink_ids=["v0", "v1"], stats=RunStats(done=2, remaining=3))
    first = reg.dispatch("run_qc", {"path": str(f), "output_dir": out_dir})
    assert first["status"] == "incomplete" and first["remaining"] == 3

    _stub_orch(monkeypatch, sink_ids=["v2", "v3", "v4"], stats=RunStats(done=3))
    second = reg.dispatch("run_qc", {"path": str(f), "output_dir": out_dir})
    assert second["run_id"] == first["run_id"]          # continued the same run
    assert second["status"] == "complete"
    assert second["resumed"] is True
    assert second["already_done_before_this_run"] == 2
    assert second["completed_total"] == 5


def test_run_qc_points_later_tools_at_the_folder_it_used(tmp_path, monkeypatch):
    """A run saved to a custom folder must still be findable by list_runs /
    show_results / resume_run — they read ctx.output_dir, which otherwise stays
    on the config default and reports "no runs found" for the run just made."""
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}])
    _stub_orch(monkeypatch, sink_ids=["v1"], stats=RunStats(done=1))
    ctx = _ctx(tmp_path)
    default_dir = ctx.output_dir
    chosen = str(tmp_path / "somewhere" / "else")

    reg = ToolRegistry(ctx)
    out = reg.dispatch("run_qc", {"path": str(f), "output_dir": chosen})
    assert out["output_dir"] == chosen
    assert ctx.output_dir == chosen != default_dir
    # and the follow-up tools now actually see it
    assert reg.dispatch("list_runs", {})["count"] == 1


def test_resume_run_recovers_pasted_ids_without_the_original_input(tmp_path, monkeypatch):
    """A run started from pasted ids can still be resumed later: the ids were
    recorded in its manifest, so no file path is needed."""
    ids = "\n".join(f"UC{i:022d}" for i in range(6))
    _stub_orch(monkeypatch, sink_ids=[f"UC{i:022d}" for i in range(2)],
               stats=RunStats(done=2, remaining=4))
    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    first = reg.dispatch("run_qc", {"ids": ids, "output_dir": ctx.output_dir})
    assert first["status"] == "incomplete"

    _stub_orch(monkeypatch, sink_ids=[f"UC{i:022d}" for i in range(2, 6)],
               stats=RunStats(done=4))
    out = reg.dispatch("resume_run", {"run_id": first["run_id"]})   # no path!
    assert out["status"] == "complete"
    assert out["total_in_file"] == 6 and out["completed_total"] == 6


def test_resume_run_without_a_list_asks_for_the_input(tmp_path):
    """A run whose manifest has no item list can't be reconstructed — say so
    instead of silently resuming nothing."""
    from ytqc.pipeline.state import RunState
    ctx = _ctx(tmp_path)
    st = RunState(ctx.output_dir, run_id="20260101-000000-abcdef")
    st.write_manifest(fingerprint="fp", total_items=5)          # no items stored
    out = ToolRegistry(ctx).dispatch("resume_run", {"run_id": st.run_id})
    assert "error" in out and "original input" in out["error"]


def test_list_runs_reports_what_is_left_for_the_how_many_left_question(tmp_path, monkeypatch):
    """`how many are left?` must be answerable from a tool, including after a
    Ctrl-C where no run_qc result ever came back."""
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": f"v{i}", "type": "video"} for i in range(49)])
    _stub_orch(monkeypatch, sink_ids=[f"v{i}" for i in range(24)],
               stats=RunStats(done=24, remaining=25))
    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    reg.dispatch("run_qc", {"path": str(f), "output_dir": ctx.output_dir})

    runs = reg.dispatch("list_runs", {})["runs"]
    unfinished = [r for r in runs if r.get("unfinished")]
    assert len(unfinished) == 1
    assert unfinished[0]["remaining"] == 25
    assert unfinished[0]["total_items"] == 49
    assert unfinished[0]["input_path"] == str(f)       # so it can offer to resume


def test_run_qc_without_input_errors(tmp_path):
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("run_qc", {})
    assert "error" in out


def test_run_qc_without_output_dir_asks_and_does_not_run(tmp_path, monkeypatch):
    # The run must NOT start until the user says where to save: run_qc returns a
    # need_output_dir prompt and never constructs the Orchestrator.
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}])

    class BoomOrch:
        def __init__(self, *a, **k):
            raise AssertionError("Orchestrator must not run without an output_dir")
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", BoomOrch)

    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": 2})   # no output_dir
    assert out.get("need_output_dir") is True
    assert "ask" in out
    assert ctx.last_run_id is None                                # nothing ran


def test_looks_like_file_path():
    assert _looks_like_file_path("~/x/out.xlsx") == ".xlsx"
    assert _looks_like_file_path("~/Desktop/results.CSV") == ".csv"   # case-insensitive
    assert _looks_like_file_path("~/x/qc-results") is None            # plain folder
    assert _looks_like_file_path("~/v1.2/runs") is None              # dotted folder, no file suffix
    assert _looks_like_file_path("") is None


def test_run_qc_output_dir_with_extension_asks_and_does_not_run(tmp_path, monkeypatch):
    # A file-looking output path (e.g. results.csv) must NOT become a folder:
    # run_qc bounces with need_output_dir and never runs.
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}])

    class BoomOrch:
        def __init__(self, *a, **k):
            raise AssertionError("must not run with a file-looking output_dir")
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", BoomOrch)

    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    bad = str(tmp_path / "Desktop" / "results.csv")
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": 2, "output_dir": bad})
    assert out.get("need_output_dir") is True
    assert "ask" in out
    assert not (tmp_path / "Desktop" / "results.csv").exists()   # no weird folder
    assert ctx.last_run_id is None                               # nothing ran


def test_run_qc_creates_and_honors_output_dir(tmp_path, monkeypatch):
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}])

    seen = {}

    class StubOrch:
        def __init__(self, cfg, *a, **k):
            seen["output_dir"] = cfg.output_dir
        def run(self):
            return RunStats(done=1)
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", StubOrch)

    target = tmp_path / "nested" / "qc-results"               # does not exist yet
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": 2, "output_dir": str(target)})
    assert target.is_dir()                                     # created on demand
    assert seen["output_dir"] == str(target)                  # run wrote there
    assert out["output_dir"] == str(target)


class _CaptureOrch:
    """Stub Orchestrator that records what items it was constructed with."""
    captured = {}
    def __init__(self, cfg, items, *a, **k):
        _CaptureOrch.captured = {
            "n": len(items),
            "types": {i.type for i in items},
            "ids": [i.id for i in items],
        }
    def run(self):
        return RunStats(done=len(_CaptureOrch.captured["ids"]))


# three real-shaped (UC + 22 chars) channel ids
_UC1 = "UCECWJfpmSWeaZ2fbb0rlq_g"
_UC2 = "UC7trU46U_9XPDtMnDbiDPUQ"
_UC3 = "UCLbdVvreihwZRL6kwuEUYsA"


def test_run_qc_from_ids(tmp_path, monkeypatch):
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", _CaptureOrch)
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("run_qc", {"ids": f"{_UC1}, {_UC2}, {_UC3}", "item_type": "channel",
                                  "output_dir": str(tmp_path / "out")})
    assert _CaptureOrch.captured["n"] == 3
    assert _CaptureOrch.captured["types"] == {"channel"}
    assert out["items"] == 3


def test_run_qc_from_pasted_csv_blob(tmp_path, monkeypatch):
    # the exact bug: a pasted id,type,COUNTRY-Name block must yield 5 channels,
    # NOT ~17 garbage tokens like "channel"/"US"/"Noodah05".
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", _CaptureOrch)
    reg = ToolRegistry(_ctx(tmp_path))
    blob = (f"{_UC1},channel,US - Noodah05\n"
            f"{_UC2},channel,US - JEV\n"
            f"{_UC3},channel,IN - Think Music India\n"
            "UCugG6-k5QGbq_iDEPAnG4NQ,channel,IN - KRAFTON INDIA ESPORTS\n"
            "UCdPsNbQIs6U36fyMdkzOvbQ,channel,IN - Navaan Sandhu")
    out = reg.dispatch("run_qc", {"ids": blob, "item_type": "channel",
                                  "output_dir": str(tmp_path / "out")})
    assert _CaptureOrch.captured["n"] == 5
    assert _CaptureOrch.captured["types"] == {"channel"}
    assert all(i.startswith("UC") for i in _CaptureOrch.captured["ids"])
    assert out["items"] == 5
    assert out["parsed"]["channels"] == 5 and out["parsed"]["deduped"] == 0
    assert out["parsed"]["unrecognized"] == []


def test_inspect_input_accepts_pasted_ids(tmp_path):
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("inspect_input", {"ids": f"{_UC1},channel,US - Noodah05\nnot-an-id-line"})
    assert out["total"] == 1 and out["channels"] == 1
    assert out["unrecognized"] == ["not-an-id-line"]


# ── inspect / show_results / list_runs ──────────────────────────────────────

def test_inspect_input_missing_file_is_clean_error(tmp_path):
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("inspect_input", {"path": "~/definitely-not-here-999.csv"})
    assert "error" in out


def test_show_results_reads_and_filters(tmp_path):
    ctx = _ctx(tmp_path)
    run_dir = tmp_path / "runs" / "20260613-120000-abcdef"
    run_dir.mkdir(parents=True)
    with open(run_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "name", "tier_1", "brand_safety_is_safe",
                                          "needs_review", "status", "confidence"])
        w.writeheader()
        w.writerow({"id": "v1", "name": "A", "tier_1": "Music", "brand_safety_is_safe": "True",
                    "needs_review": "False", "status": "OK", "confidence": "1.0"})
        w.writerow({"id": "v2", "name": "B", "tier_1": "News", "brand_safety_is_safe": "False",
                    "needs_review": "True", "status": "OK", "confidence": "0.5"})
    reg = ToolRegistry(ctx)
    allr = reg.dispatch("show_results", {"run_id": "20260613-120000-abcdef"})
    assert allr["total"] == 2 and allr["unsafe"] == 1 and allr["needs_review"] == 1
    only = reg.dispatch("show_results", {"run_id": "20260613-120000-abcdef", "only": "unsafe"})
    assert only["matched"] == 1 and only["rows"][0]["id"] == "v2"


def test_list_runs_finds_run_dirs(tmp_path):
    ctx = _ctx(tmp_path)
    for rid in ("20260613-120000-aaaaaa", "20260613-130000-bbbbbb"):
        d = tmp_path / "runs" / rid
        d.mkdir(parents=True)
        with open(d / "results.csv", "w") as f:
            f.write("id,brand_safety_is_safe\nx,True\n")
    out = ToolRegistry(ctx).dispatch("list_runs", {})
    assert out["count"] == 2
    assert out["runs"][0]["run_id"] == "20260613-130000-bbbbbb"   # newest first


def test_show_taxonomy(tmp_path):
    out = ToolRegistry(_ctx(tmp_path)).dispatch("show_taxonomy", {})
    assert len(out["tier_1"]) == 29 and len(out["kids_age_groups"]) == 5
    # served in the QC team's mapping order, not sorted
    assert out["tier_1"][0] == "Home Decor" and out["tier_1"][-1] == "Podcasts"


def test_dispatch_propagates_keyboard_interrupt(tmp_path):
    # a tool raising KeyboardInterrupt (e.g. Ctrl-C during run_qc) must propagate,
    # not be swallowed into {"error": ...}, so the REPL can catch + cancel cleanly.
    reg = ToolRegistry(_ctx(tmp_path))
    def boom(ctx):
        raise KeyboardInterrupt
    reg._fns["run_qc"] = boom
    with pytest.raises(KeyboardInterrupt):
        reg.dispatch("run_qc", {})


def test_run_qc_defaults_to_two_lanes(tmp_path, monkeypatch):
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}])
    captured = {}

    class StubOrch:
        def __init__(self, cfg, *a, **k):
            captured["lanes"] = cfg.pipeline.browser_lanes
        def run(self):
            return RunStats(done=1)
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", StubOrch)

    reg = ToolRegistry(_ctx(tmp_path))
    out_dir = str(tmp_path / "out")
    reg.dispatch("run_qc", {"path": str(f), "output_dir": out_dir})   # no lanes given
    assert captured["lanes"] == 2                            # default = 2
    reg.dispatch("run_qc", {"path": str(f), "lanes": 5, "output_dir": out_dir})  # explicit honored
    assert captured["lanes"] == 5


def _write_rows(p, header, rows):
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_inspect_input_autodetects_non_id_column(tmp_path):
    # A file whose id column is 'Channel URL' (no literal 'id') still works, and
    # the report names the auto-detected column.
    f = tmp_path / "channels.csv"
    _write_rows(f, ["name", "channel url"], [
        ["Noodah05", "https://www.youtube.com/channel/UCECWJfpmSWeaZ2fbb0rlq_g"],
        ["JEV", "https://www.youtube.com/channel/UC7trU46U_9XPDtMnDbiDPUQ"],
    ])
    out = ToolRegistry(_ctx(tmp_path)).dispatch("inspect_input", {"path": str(f)})
    assert out["total"] == 2 and out["channels"] == 2
    assert out["detected_column"] == "channel url"


def test_inspect_input_no_id_column_gives_helpful_error(tmp_path):
    # No id-like column → a helpful error naming the columns, not a crash.
    f = tmp_path / "notes.csv"
    _write_rows(f, ["name", "notes"], [["Noodah05", "cool channel"]])
    out = ToolRegistry(_ctx(tmp_path)).dispatch("inspect_input", {"path": str(f)})
    assert "error" in out
    assert "column" in out["error"].lower()
