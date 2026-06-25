"""Tests for ytqc.pipeline.channel_flow.run_channel_flow (catalog-based flow).

Hermetic: the channel flow is browser-free; it needs a FakeLLMClient routing the
three agent calls (vision analyst / channel synthesizer / judge) to canned dicts.
Channel QC now classifies from the scrolled catalog — video titles + the /videos
grid thumbnail screenshots (run through vision) + about — with no per-video briefs.
We pin the flow: stats copy-through, ERROR/needs_review surfacing, the rule-gate
risk floor, no-transcript confidence, and synthesizer ValidationError.
"""
from __future__ import annotations

import pytest

from ytqc.models import ChannelExtract, ChannelVideoTile
from ytqc.pipeline.channel_flow import run_channel_flow
from tests.fakes import (
    FakeLLMClient,
    good_channel_synth,
    good_judge_output,
    good_vision_evidence,
)
from tests.fixtures import yt_payloads as P

# by_system routing substrings (unique per agent).
VISION = "visual content analyst"
SYNTH = "CHANNEL-level QC brief"
JUDGE = "final reconciliation judge"

_TITLES = [
    "ZX-4R lap record", "CBR650R vs ZX-6R", "Cheap Mods That Work",
    "Chain Maintenance 101", "Trackside Vlog: Round 3", "Best Beginner Bikes 2026",
]


def _channel(*, ok: bool = True, titles=None, home: str | None = P.TINY_JPEG_B64,
             grid=None, **overrides) -> ChannelExtract:
    tiles = [ChannelVideoTile(video_id=f"v{i}", title=t, views=100_000)
             for i, t in enumerate(titles if titles is not None else _TITLES)]
    base = dict(
        channel_id="UC_test_channel",
        ok=ok,
        title="Moto Channel",
        description="A motorcycle review channel.",
        subscribers=1_234_567,
        total_views=98_765_432,
        video_count=420,
        country="United States",
        joined_date="2015-03-01",
        velocity_score=1.42,
        channel_keywords="motorcycle, track, review",
        recent_videos=tiles,
        home_screenshot_b64=home,
        grid_screenshots_b64=grid if grid is not None else [P.TINY_JPEG_B64, P.TINY_JPEG_B64],
    )
    base.update(overrides)
    return ChannelExtract(**base)


def _llm(*, vision=None, synth=None, judge=None) -> FakeLLMClient:
    return FakeLLMClient(by_system={
        VISION: vision if vision is not None else good_vision_evidence(),
        SYNTH: synth if synth is not None else good_channel_synth(),
        JUDGE: judge if judge is not None else good_judge_output(),
    })


# ── happy path: stats copy-through + classification ──────────────────────────
def test_happy_path_copies_channel_stats_and_classifies():
    rec = run_channel_flow(_llm(), _channel(), run_id="run-1")

    assert rec.status == "OK"
    assert rec.type == "channel"
    assert rec.id == "UC_test_channel"
    assert rec.name == "Moto Channel"
    assert rec.subscribers == 1_234_567
    assert rec.total_views == 98_765_432
    assert rec.video_count == 420
    assert rec.velocity_score == 1.42
    assert rec.country == "United States"
    assert rec.publish_or_join_date == "2015-03-01"
    assert rec.tier_1 == "Automobiles"
    assert rec.provider == "fake"
    assert rec.model == "fake-model"
    assert rec.run_id == "run-1"


def test_happy_path_full_signals_not_flagged_for_review():
    # vision ok + >=5 titles + grid screenshots present → clean, no transcript penalty
    rec = run_channel_flow(_llm(), _channel(), run_id="r")

    assert rec.transcript_source == "none"     # catalog QC: no transcript by design
    assert rec.needs_review is False
    assert rec.confidence == pytest.approx(1.0)  # expect_transcript=False → no -0.15


# ── extraction failure → ERROR row ───────────────────────────────────────────
def test_extract_not_ok_is_error_row():
    ex = _channel(ok=False, error="channel page never loaded")
    llm = _llm()
    rec = run_channel_flow(llm, ex, run_id="r")

    assert rec.status == "ERROR"
    assert rec.error == "channel page never loaded"
    assert rec.needs_review is True
    assert rec.confidence == 0.0
    assert rec.subscribers == 1_234_567
    assert rec.country == "United States"
    assert llm.calls == 0


def test_extract_not_ok_default_error_message():
    ex = _channel(ok=False, error=None)
    rec = run_channel_flow(_llm(), ex, run_id="r")
    assert rec.status == "ERROR"
    assert rec.error == "extraction failed"


# ── needs_review forced by missing core catalog signals ──────────────────────
def test_needs_review_when_no_grid_screenshots():
    # vision still "ok" on the home screenshot alone, but no grid shots → review
    rec = run_channel_flow(_llm(), _channel(grid=[]), run_id="r")
    assert rec.status == "OK"
    assert rec.needs_review is True


def test_needs_review_when_thin_catalog():
    # fewer than 5 titles scraped → forced review
    rec = run_channel_flow(_llm(), _channel(titles=["only one"]), run_id="r")
    assert rec.status == "OK"
    assert rec.needs_review is True


def test_needs_review_when_vision_fails():
    # no images at all (no home + no grid) → vision returns ok=False → review + penalty
    ex = _channel(home=None, grid=[])
    rec = run_channel_flow(_llm(), ex, run_id="r")
    assert rec.status == "OK"
    assert rec.needs_review is True
    assert rec.confidence == pytest.approx(0.90)  # vision_ok=False → -0.10 only


# ── post-gate: synthesizer risk flows through; floor never lowers ─────────────
def test_synth_risk_flows_through():
    synth = good_channel_synth(brand_safety={
        "is_safe": False, "risk_level": "medium",
        "triggered_categories": ["Violent Content"],
        "explanation": "Crash compilations across several titles.",
    })
    rec = run_channel_flow(_llm(synth=synth), _channel(), run_id="r")
    assert rec.brand_safety_risk_level == "medium"
    assert "Violent Content" in rec.brand_safety_triggered_categories
    assert rec.brand_safety_is_safe is False


# ── synthesizer ValidationError → ERROR row ──────────────────────────────────
def test_synthesizer_validation_error_is_error_row():
    bad_synth = good_channel_synth(tier_1="Not A Real Category")
    rec = run_channel_flow(_llm(synth=bad_synth), _channel(), run_id="r")

    assert rec.status == "ERROR"
    assert rec.error.startswith("validation:")
    assert rec.needs_review is True
    assert rec.confidence == 0.0
    assert rec.subscribers == 1_234_567
