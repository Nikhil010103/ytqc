"""Hermetic tests for the N-lane / M-worker orchestrator plumbing.

The browser (KimiClient) and the extraction are stubbed so these tests exercise
ONLY the concurrency machinery: work-stealing, exactly-once processing, the
producer-barrier shutdown (no hang), sentinel fan-out, error rows, and the
global captcha halt. No network, no real LLM; extract_only avoids the LLM path.
lane_stagger_s is forced to 0 so nothing sleeps for real.
"""
import threading

import pytest

from ytqc.browser.webbridge import CaptchaInterstitial
from ytqc.config import DEFAULT_CONFIG
from ytqc.models import InputItem, QCRecord, VideoExtract
from ytqc.pipeline.orchestrator import Orchestrator
from ytqc.pipeline.state import RunState


class _StubKimi:
    """Stands in for KimiClient inside _lane — construction + navigate/close only."""
    def __init__(self, cfg, rate_bucket=None, halt=None):
        self.cfg = cfg
    def navigate(self, *a, **k): pass
    def item_pause(self): pass
    def close(self): pass


class ListSink:
    def __init__(self):
        self.records = []
        self._lock = threading.Lock()
    def open(self, run_id, output_dir): pass
    def write(self, rec):
        with self._lock:
            self.records.append(rec)
    def close(self): pass


def _cfg(lanes, workers, degrade=True):
    c = DEFAULT_CONFIG.model_copy(deep=True)
    c.pipeline.browser_lanes = lanes
    c.pipeline.analysis_workers = workers
    c.pipeline.lane_stagger_s = 0.0       # no real sleeping
    c.pipeline.degrade_on_captcha = degrade
    c.pipeline.min_lane_count = 1
    return c


def _make(tmp_path, items, lanes, workers, extract_fn, degrade=True, monkeypatch=None):
    monkeypatch.setattr("ytqc.pipeline.orchestrator.KimiClient", _StubKimi)
    sink = ListSink()
    state = RunState(str(tmp_path))
    orch = Orchestrator(_cfg(lanes, workers, degrade), items, [sink], state,
                        extract_only=True, console=_quiet_console())
    orch._restore_or_extract = extract_fn       # bypass the real browser
    return orch, sink, state


def _quiet_console():
    from rich.console import Console
    return Console(quiet=True)


def _items(n):
    return [InputItem(id=f"v{i}", type="video") for i in range(n)]


def _ok_extract(kimi, item):
    return VideoExtract(video_id=item.id, ok=True, title=f"T-{item.id}")


# ── core plumbing ─────────────────────────────────────────────────────────

def test_all_items_processed_exactly_once(tmp_path, monkeypatch):
    items = _items(20)
    orch, sink, state = _make(tmp_path, items, lanes=5, workers=3, extract_fn=_ok_extract,
                              monkeypatch=monkeypatch)
    orch.run()
    ids = [r.id for r in sink.records]
    assert sorted(ids) == sorted(i.id for i in items)     # every item
    assert len(ids) == len(set(ids))                      # exactly once (no dupes)
    assert orch.stats.done == 20
    # progress-clarity counters: every item extracted, browser phase marked done
    assert orch.stats.extracted == 20
    assert orch._browser_done.is_set()


def test_more_lanes_than_items_no_hang(tmp_path, monkeypatch):
    items = _items(2)
    orch, sink, state = _make(tmp_path, items, lanes=10, workers=4, extract_fn=_ok_extract,
                              monkeypatch=monkeypatch)
    orch.run()                                            # must return (no deadlock)
    assert orch.stats.done == 2


def test_single_lane_single_worker(tmp_path, monkeypatch):
    items = _items(5)
    orch, sink, _ = _make(tmp_path, items, lanes=1, workers=1, extract_fn=_ok_extract,
                          monkeypatch=monkeypatch)
    orch.run()
    assert orch.stats.done == 5


def test_extraction_exception_becomes_error_row(tmp_path, monkeypatch):
    def extract(kimi, item):
        if item.id == "v3":
            raise RuntimeError("boom")
        return _ok_extract(kimi, item)
    items = _items(8)
    orch, sink, _ = _make(tmp_path, items, lanes=4, workers=2, extract_fn=extract,
                          monkeypatch=monkeypatch)
    orch.run()
    assert orch.stats.done == 8                           # nothing dropped
    err = [r for r in sink.records if r.status == "ERROR"]
    assert [r.id for r in err] == ["v3"]
    assert orch.stats.errors == 1


def test_extract_only_marks_failed_bundle_as_error(tmp_path, monkeypatch):
    def extract(kimi, item):
        if item.id == "v1":
            return VideoExtract(video_id="v1", ok=False, error="unavailable")
        return _ok_extract(kimi, item)
    items = _items(4)
    orch, sink, _ = _make(tmp_path, items, lanes=2, workers=2, extract_fn=extract,
                          monkeypatch=monkeypatch)
    orch.run()
    rec = next(r for r in sink.records if r.id == "v1")
    assert rec.status == "ERROR" and rec.needs_review and rec.confidence == 0.0


def test_state_marks_all_sunk(tmp_path, monkeypatch):
    items = _items(10)
    orch, sink, state = _make(tmp_path, items, lanes=4, workers=2, extract_fn=_ok_extract,
                              monkeypatch=monkeypatch)
    orch.run()
    assert all(state.is_done(i.id) for i in items)        # checkpointed


def test_resume_skips_done_items(tmp_path, monkeypatch):
    items = _items(6)
    # first run completes 6
    orch, sink, state = _make(tmp_path, items, lanes=3, workers=2, extract_fn=_ok_extract,
                              monkeypatch=monkeypatch)
    orch.run()
    run_id = state.run_id
    # resume the SAME run with the same input → nothing left to do
    monkeypatch.setattr("ytqc.pipeline.orchestrator.KimiClient", _StubKimi)
    state2 = RunState.resume(str(tmp_path), run_id)
    sink2 = ListSink()
    orch2 = Orchestrator(_cfg(3, 2), items, [sink2], state2, extract_only=True,
                         console=_quiet_console())
    calls = []
    orch2._restore_or_extract = lambda kimi, item: calls.append(item.id) or _ok_extract(kimi, item)
    orch2.run()
    assert calls == []                                    # all skipped, no re-extraction
    assert sink2.records == []


# ── global halt ───────────────────────────────────────────────────────────

def test_captcha_triggers_global_halt(tmp_path, monkeypatch):
    # degrade off → first captcha halts the whole run deterministically
    def extract(kimi, item):
        if item.id == "v0":
            raise CaptchaInterstitial("bot-check")
        return _ok_extract(kimi, item)
    items = _items(30)
    orch, sink, state = _make(tmp_path, items, lanes=4, workers=2, extract_fn=extract,
                              degrade=False, monkeypatch=monkeypatch)
    orch.run()                                            # must return (no hang)
    assert orch._halt.is_set()
    assert orch.stats.done < 30                           # halted before finishing all
    # halted items remain not-done → resume would pick them up
    assert not state.is_done("v0")


def test_handle_captcha_retires_lane_when_degrading(tmp_path, monkeypatch):
    items = _items(3)
    orch, _, _ = _make(tmp_path, items, lanes=10, workers=2, extract_fn=_ok_extract,
                       degrade=True, monkeypatch=monkeypatch)
    # first captcha while degrading and above the floor → retire (True), no global halt
    stop = orch._handle_captcha(9, CaptchaInterstitial("x"))
    assert stop is True
    assert not orch._halt.is_set()                        # graceful, siblings keep running


def test_handle_captcha_halts_at_floor(tmp_path, monkeypatch):
    items = _items(3)
    orch, _, _ = _make(tmp_path, items, lanes=2, workers=2, extract_fn=_ok_extract,
                       degrade=True, monkeypatch=monkeypatch)
    # already at min_lane_count=1 region → halts
    orch._breaker.record_stress(); orch._breaker.record_stress()   # drive toward floor
    orch._handle_captcha(0, CaptchaInterstitial("x"))
    assert orch._halt.is_set()


# ── graceful Ctrl-C shutdown ────────────────────────────────────────────────

def test_shutdown_lanes_closes_each_session(tmp_path, monkeypatch):
    import httpx
    posts = []
    monkeypatch.setattr(httpx, "post",
                        lambda url, json, timeout: posts.append(json) or None)
    orch, _, _ = _make(tmp_path, _items(1), lanes=3, workers=1, extract_fn=_ok_extract,
                       monkeypatch=monkeypatch)
    orch._shutdown_lanes(3)
    base = orch.cfg.browser.session
    assert [p["session"] for p in posts] == [f"{base}-lane0", f"{base}-lane1", f"{base}-lane2"]
    assert all(p["action"] == "close_session" for p in posts)


def test_keyboard_interrupt_sets_halt_closes_tabs_and_reraises(tmp_path, monkeypatch):
    import threading
    orch, _, _ = _make(tmp_path, _items(3), lanes=2, workers=1, extract_fn=_ok_extract,
                       monkeypatch=monkeypatch)
    closed = {}
    monkeypatch.setattr(orch, "_shutdown_lanes", lambda n: closed.update(n=n))
    # simulate Ctrl-C landing on the main thread during the first lane join
    real_join = threading.Thread.join
    flag = {"raised": False}

    def fake_join(self, timeout=None):
        if not flag["raised"]:
            flag["raised"] = True
            raise KeyboardInterrupt
        return real_join(self, timeout)
    monkeypatch.setattr(threading.Thread, "join", fake_join)

    with pytest.raises(KeyboardInterrupt):
        orch.run()
    assert orch._halt.is_set()            # halt signalled so lanes stop
    assert closed.get("n") == 2           # browser tabs torn down for both lanes


# ── browser-not-connected setup failure ─────────────────────────────────────

def test_bridge_not_connected_halts_cleanly_no_traceback(tmp_path, monkeypatch):
    from ytqc.browser.webbridge import BridgeNotConnected

    class DeadKimi:
        def __init__(self, cfg, rate_bucket=None, halt=None):
            self.cfg = cfg
        def navigate(self, *a, **k):
            raise BridgeNotConnected("no extension connected")
        def item_pause(self): pass
        def close(self): pass
    monkeypatch.setattr("ytqc.pipeline.orchestrator.KimiClient", DeadKimi)

    sink = ListSink()
    state = RunState(str(tmp_path))
    orch = Orchestrator(_cfg(2, 2), _items(5), [sink], state,
                        extract_only=True, console=_quiet_console())
    orch.run()
    assert orch._setup_error and "extension not connected" in orch._setup_error
    assert orch._halt.is_set()
    assert orch.stats.done == 0           # nothing processed
    assert sink.records == []             # no rows written
