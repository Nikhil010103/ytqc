"""Hermetic tests for ytqc.pipeline.video_flow.run_video_flow.

These PIN the hardened per-video flow behavior:
  * happy-path QCRecord shape, verbatim stats, confidence 1.0
  * extract.ok=False short-circuit (no LLM calls)
  * deterministic post-gate flooring of brand_safety.risk_level
  * is_safe always re-derived from risk_level, never trusted from the LLM dict
  * needs_review forced when a core signal (transcript/frames) is absent
  * the conditional Judge fires only on a real content-vs-vision conflict

All I/O is faked: FakeLLMClient drives the LLM calls (routed by system-prompt
substring), and VideoExtract objects are built directly. No network, browser,
Ollama, or real sleep.
"""
from __future__ import annotations

import pytest

from tests.fakes import (
    FakeLLMClient,
    good_content_output,
    good_judge_output,
    good_vision_evidence,
)
from ytqc.models import CommentData, FrameSet, TranscriptResult, VideoExtract
from ytqc.pipeline.video_flow import run_video_flow

# System-prompt routing substrings (verified present & unique enough).
VISION = "visual content analyst"
CONTENT = "senior brand-safety and media-planning analyst at a programmatic"
JUDGE = "final reconciliation judge"

# TINY_JPEG_B64 — any non-empty b64 string suffices to make the vision call run.
_FRAME_B64 = "ZmFrZS1mcmFtZS1ieXRlcw=="


def _clean_extract(**overrides) -> VideoExtract:
    """A fully-populated, clean VideoExtract: panel transcript + canvas frames.

    This is the 'all signals present' baseline that should yield confidence 1.0
    and no forced needs_review.
    """
    base = dict(
        video_id="vid_happy01",
        ok=True,
        title="Kawasaki Ninja ZX-4R Track Day Review",
        author="MotoGarage",
        channel_id="UC1234567890abcdefABCD12",
        duration_s=814.0,
        view_count=100711,
        likes=12000,
        keywords=["kawasaki", "ninja zx-4r", "track day"],
        description="Full track-day review of the Kawasaki Ninja ZX-4R.",
        youtube_category="Autos & Vehicles",
        publish_date="2026-05-12T20:50:47-07:00",
        is_family_safe=True,
        is_live=False,
        views_per_day=275.9,
        transcript=TranscriptResult(
            source="panel",
            track_kind="asr",
            track_lang="en",
            segments=[{"start_s": 3.0, "text": "Welcome back, today we ride the ZX-4R."}],
            excerpt_block="[0:03] Welcome back, today we ride the ZX-4R.",
        ),
        frames=FrameSet(
            thumbnail_b64=_FRAME_B64,
            frames_b64=[_FRAME_B64, _FRAME_B64],
            frame_timestamps=[10.0, 400.0],
            method="canvas",
        ),
        comments=CommentData(
            count=1204,                       # parse_count("1,204 Comments") at extract time
            count_text="1,204 Comments",
            top_comments=[{"author": "@riderdan", "text": "Insane lap time!", "likes": "320"}],
        ),
    )
    base.update(overrides)
    return VideoExtract(**base)


# ──────────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────────
def test_happy_path_ok_status_and_tier_and_confidence_1():
    extract = _clean_extract()
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),       # no kids signals, no conflict
        CONTENT: good_content_output(),        # tier_1=Automobiles, risk none
    })

    rec = run_video_flow(llm, extract, run_id="run-happy")

    assert rec.status == "OK"
    assert rec.error == ""
    assert rec.tier_1 == "Automobiles"
    assert rec.judge_invoked is False
    # transcript=panel + frames(canvas, vision ok) + no tier-recovery + no judge.
    assert rec.confidence == 1.0
    assert rec.needs_review is False
    # is_safe re-derived; canned dict had risk none → safe.
    assert rec.brand_safety_risk_level == "none"
    assert rec.brand_safety_is_safe is True


def test_happy_path_stats_copied_verbatim_from_extract():
    extract = _clean_extract(
        view_count=100711, likes=12000, views_per_day=275.9, duration_s=814.0,
    )
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),
        CONTENT: good_content_output(),
    })

    rec = run_video_flow(llm, extract, run_id="run-stats")

    # Deterministic, extraction-owned stats must pass through untouched.
    assert rec.total_views == 100711
    assert rec.likes == 12000
    assert rec.views_per_day == 275.9
    assert rec.duration_s == 814.0
    assert rec.transcript_source == "panel"
    assert rec.comments_count == 1204         # parsed numeric count, copied from extract.comments.count
    assert rec.provider == "fake"
    assert rec.model == "fake-model"
    assert rec.run_id == "run-stats"


# ──────────────────────────────────────────────────────────────────────────
# extract.ok == False short-circuit
# ──────────────────────────────────────────────────────────────────────────
def test_extract_not_ok_short_circuits_with_no_llm_calls():
    extract = _clean_extract(ok=False, error="video unavailable")
    # No routing configured: if the flow ever called the LLM, chat_json would
    # raise AssertionError. The short-circuit must happen before any call.
    llm = FakeLLMClient(by_system={CONTENT: good_content_output()})

    rec = run_video_flow(llm, extract, run_id="run-err")

    assert rec.status == "ERROR"
    assert rec.error == "video unavailable"
    assert rec.confidence == 0.0
    assert rec.needs_review is True
    assert llm.calls == 0


def test_extract_not_ok_uses_default_error_message_when_none():
    extract = _clean_extract(ok=False, error=None)
    llm = FakeLLMClient(by_system={CONTENT: good_content_output()})

    rec = run_video_flow(llm, extract, run_id="run-err2")

    assert rec.status == "ERROR"
    assert rec.error == "extraction failed"
    assert llm.calls == 0


# ──────────────────────────────────────────────────────────────────────────
# Post-gate flooring — the core safety guarantee
# ──────────────────────────────────────────────────────────────────────────
def test_post_gate_floors_risk_up_on_deterministic_safety_term():
    # Title contains "casino" → gambling group (min_risk medium). The LLM claims
    # risk none but acknowledges the Gambling category; the post-gate must floor
    # the risk up to at least medium and recompute is_safe=False.
    extract = _clean_extract(
        title="I Won Big at the Online Casino — Betting Strategy Review",
        description="My casino betting strategy that actually works.",
    )
    content = good_content_output(
        brand_safety={
            "is_safe": True,          # LLM lies; must be ignored / recomputed
            "risk_level": "none",     # must be floored up
            "triggered_categories": ["Gambling"],
            "explanation": "Discusses casino betting.",
        },
    )
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),
        CONTENT: content,
    })

    rec = run_video_flow(llm, extract, run_id="run-floor")

    assert rec.brand_safety_risk_level == "medium"
    assert rec.brand_safety_is_safe is False
    assert "Gambling" in rec.brand_safety_triggered_categories


def test_post_gate_floors_none_to_low_when_category_not_acknowledged():
    # Same gambling term, but the LLM did NOT list Gambling. The conservative
    # branch still raises 'none' → 'low' so the row surfaces in review.
    extract = _clean_extract(
        title="Casino Night Vlog",
        description="A relaxed evening with friends.",
    )
    content = good_content_output(
        brand_safety={
            "is_safe": True,
            "risk_level": "none",
            "triggered_categories": [],
            "explanation": "Mostly social content.",
        },
    )
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),
        CONTENT: content,
    })

    rec = run_video_flow(llm, extract, run_id="run-floor-low")

    assert rec.brand_safety_risk_level == "low"
    # 'low' is still treated as safe-enough by the derive rule.
    assert rec.brand_safety_is_safe is True


def test_is_safe_always_rederived_from_risk_level_never_trusted_from_llm():
    # Clean extract (no rule hits) but the LLM asserts is_safe=True at HIGH risk.
    # is_safe must be recomputed to False from risk_level alone.
    extract = _clean_extract()
    content = good_content_output(
        brand_safety={
            "is_safe": True,          # contradictory: trust must NOT propagate
            "risk_level": "high",
            "triggered_categories": ["Adult Content"],
            "explanation": "High-risk content per analyst.",
        },
    )
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),
        CONTENT: content,
    })

    rec = run_video_flow(llm, extract, run_id="run-rederive")

    assert rec.brand_safety_risk_level == "high"
    assert rec.brand_safety_is_safe is False


# ──────────────────────────────────────────────────────────────────────────
# needs_review forced when a core signal is absent
# ──────────────────────────────────────────────────────────────────────────
def test_needs_review_forced_when_transcript_source_none():
    extract = _clean_extract(
        transcript=TranscriptResult(source="none"),
    )
    llm = FakeLLMClient(by_system={
        VISION: good_vision_evidence(),
        CONTENT: good_content_output(),
    })

    rec = run_video_flow(llm, extract, run_id="run-no-tx")

    # Even though everything else is clean, the missing transcript forces review.
    assert rec.needs_review is True
    assert rec.transcript_source == "none"
    # confidence loses the transcript bonus but is still well above 0.6.
    assert rec.confidence >= 0.6


def test_needs_review_forced_when_frames_method_none():
    # No frames captured → vision falls back to ok=False, AND frames.method=="none"
    # forces review regardless of the (still-high) confidence.
    extract = _clean_extract(
        frames=FrameSet(method="none"),
    )
    # Vision won't be called (no images) so only CONTENT routing is needed.
    llm = FakeLLMClient(by_system={CONTENT: good_content_output()})

    rec = run_video_flow(llm, extract, run_id="run-no-frames")

    assert rec.needs_review is True


# ──────────────────────────────────────────────────────────────────────────
# Conditional Judge — fires only on a real conflict
# ──────────────────────────────────────────────────────────────────────────
def test_judge_fires_on_kids_visual_vs_nonkids_tier_conflict():
    extract = _clean_extract()
    vision = good_vision_evidence(
        visual_kids_signals={"present": True, "signals": ["cartoon characters", "nursery rhyme"]},
    )
    content = good_content_output(tier_1="Automobiles")  # NOT Kids → conflict
    llm = FakeLLMClient(by_system={
        VISION: vision,
        CONTENT: content,
        JUDGE: good_judge_output(),    # judge upholds analyst (no resolved_fields)
    })

    rec = run_video_flow(llm, extract, run_id="run-judge")

    assert rec.judge_invoked is True
    # A judge system-prompt call was actually made.
    assert any(JUDGE in sys for sys, _user, _imgs in llm.history)
    # confidence carries the judge penalty (1.0 - 0.20).
    assert rec.confidence == pytest.approx(0.80)


def test_judge_not_called_when_no_conflict():
    extract = _clean_extract()
    vision = good_vision_evidence(
        visual_kids_signals={"present": False, "signals": []},
    )
    content = good_content_output(tier_1="Automobiles")
    # Judge intentionally NOT routed: if adjudicate were called, chat_json with a
    # judge system prompt would raise AssertionError (no by_system key matches).
    llm = FakeLLMClient(by_system={
        VISION: vision,
        CONTENT: content,
    })

    rec = run_video_flow(llm, extract, run_id="run-nojudge")

    assert rec.judge_invoked is False
    assert not any(JUDGE in sys for sys, _user, _imgs in llm.history)
