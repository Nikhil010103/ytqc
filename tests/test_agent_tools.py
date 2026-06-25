"""Agent tools: path resolution, dispatch, run_qc (stubbed orchestrator),
inspect_input, show_results, list_runs. Hermetic — no browser, no LLM."""
import csv

import pytest
from rich.console import Console

from ytqc.agent.tools import AgentContext, ToolRegistry, _resolve_path
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

def test_run_qc_runs_and_summarizes(tmp_path, monkeypatch):
    f = tmp_path / "items.csv"
    _write_csv(f, [{"id": "v1", "type": "video"}, {"id": "v2", "type": "video"}])

    class StubOrch:
        def __init__(self, *a, **k): pass
        def run(self):
            return RunStats(done=2, errors=0, unsafe=1, needs_review=1,
                            tier_counts={"Music": 2})
    monkeypatch.setattr("ytqc.pipeline.orchestrator.Orchestrator", StubOrch)

    ctx = _ctx(tmp_path)
    reg = ToolRegistry(ctx)
    out = reg.dispatch("run_qc", {"path": str(f), "lanes": "2"})
    assert out["items"] == 2 and out["unsafe"] == 1
    assert out["tier_distribution"] == {"Music": 2}
    assert out["run_id"] and ctx.last_run_id == out["run_id"]   # last_run_id updated


def test_run_qc_without_input_errors(tmp_path):
    reg = ToolRegistry(_ctx(tmp_path))
    out = reg.dispatch("run_qc", {})
    assert "error" in out


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
    out = reg.dispatch("run_qc", {"ids": f"{_UC1}, {_UC2}, {_UC3}", "item_type": "channel"})
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
    out = reg.dispatch("run_qc", {"ids": blob, "item_type": "channel"})
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
    assert len(out["tier_1"]) == 35 and len(out["kids_age_groups"]) == 5


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
    reg.dispatch("run_qc", {"path": str(f)})                 # no lanes given
    assert captured["lanes"] == 2                            # default = 2
    reg.dispatch("run_qc", {"path": str(f), "lanes": 5})     # explicit honored
    assert captured["lanes"] == 5
