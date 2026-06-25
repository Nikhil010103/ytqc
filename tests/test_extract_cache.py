"""Cross-run extraction cache — pure unit tests + the orchestrator skip path."""
import threading

import ytqc.browser.extract_cache as ec_mod
import ytqc.pipeline.orchestrator as orch_mod
from ytqc.browser.extract_cache import ExtractCache
from ytqc.config import DEFAULT_CONFIG
from ytqc.models import InputItem, VideoExtract
from ytqc.pipeline.orchestrator import Orchestrator
from ytqc.pipeline.state import RunState
from rich.console import Console


def _quiet():
    return Console(quiet=True)


# ── pure cache ───────────────────────────────────────────────────────────────

def test_round_trip_is_byte_identical(tmp_path):
    c = ExtractCache(path=tmp_path / "ec.sqlite")
    ve = VideoExtract(video_id="vid1", ok=True, title="Hello")
    key = ExtractCache.make_key("vid1", "video")
    c.put(key, "video", ve.model_dump())
    got = c.get(key, "video")
    assert got is not None
    assert VideoExtract.model_validate(got).title == "Hello"
    assert c.hits == 1


def test_key_distinguishes_id_and_type():
    assert ExtractCache.make_key("a", "video") != ExtractCache.make_key("b", "video")
    assert ExtractCache.make_key("a", "video") != ExtractCache.make_key("a", "channel")
    assert ExtractCache.make_key("a", "video") == ExtractCache.make_key("a", "video")


def test_ttl_expiry_by_kind(tmp_path, monkeypatch):
    # channel TTL is short; advance the clock past it → miss + row deleted
    c = ExtractCache(path=tmp_path / "ec.sqlite", channel_ttl_days=1)
    key = ExtractCache.make_key("UCx", "channel")
    fake = {"t": 1000.0}
    monkeypatch.setattr(ec_mod.time, "time", lambda: fake["t"])
    c.put(key, "channel", {"channel_id": "UCx"})
    assert c.get(key, "channel") is not None        # fresh
    fake["t"] += 2 * 86400                           # +2 days > 1-day TTL
    assert c.get(key, "channel") is None             # expired → miss


def test_disabled_is_noop(tmp_path):
    c = ExtractCache(path=tmp_path / "ec.sqlite", enabled=False)
    key = ExtractCache.make_key("vid1", "video")
    c.put(key, "video", {"video_id": "vid1"})
    assert c.get(key, "video") is None


# ── orchestrator integration: a warm cache skips the extractor ────────────────

class _FakeKimi:
    def navigate(self, *a, **k): pass
    def item_pause(self): pass
    def close(self): pass


def _orch(tmp_path):
    cfg = DEFAULT_CONFIG.model_copy(deep=True)
    cfg.pipeline.lane_stagger_s = 0.0
    state = RunState(str(tmp_path / "runs"))
    o = Orchestrator(cfg, [], [], state, extract_only=True, console=_quiet())
    o._extract_cache = ExtractCache(path=tmp_path / "ec.sqlite")   # isolated, not ~/.ytqc
    return o


def test_cross_run_cache_hit_skips_extractor(tmp_path, monkeypatch):
    o = _orch(tmp_path)
    ve = VideoExtract(video_id="vid1", ok=True, title="Cached")
    o._extract_cache.put(ExtractCache.make_key("vid1", "video"), "video", ve.model_dump())

    def boom(*a, **k):
        raise AssertionError("extractor must NOT run on a cache hit")
    monkeypatch.setattr(orch_mod, "extract_video", boom)

    bundle = o._restore_or_extract(_FakeKimi(), InputItem(id="vid1", type="video"))
    assert bundle.video_id == "vid1" and bundle.title == "Cached"
    # and the per-run artifact was written so resume still works
    assert o.state.load_artifact("vid1", "extracted.json") is not None


def test_cache_miss_extracts_then_populates(tmp_path, monkeypatch):
    o = _orch(tmp_path)
    calls = {"n": 0}

    def fake_extract(kimi, vid, sampling, depth="full", with_comments=True, **kw):
        calls["n"] += 1
        return VideoExtract(video_id=vid, ok=True, title="Fresh")
    monkeypatch.setattr(orch_mod, "extract_video", fake_extract)

    item = InputItem(id="vid2", type="video")
    b1 = o._restore_or_extract(_FakeKimi(), item)
    assert b1.title == "Fresh" and calls["n"] == 1
    # cached now → a fresh orchestrator (new run, same cache file) reuses it
    o2 = _orch(tmp_path)
    o2._extract_cache = o._extract_cache
    monkeypatch.setattr(orch_mod, "extract_video",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should hit cache")))
    b2 = o2._restore_or_extract(_FakeKimi(), item)
    assert b2.title == "Fresh"          # served from cross-run cache, extractor not called
