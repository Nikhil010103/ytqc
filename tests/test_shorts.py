"""Shorts detection — the QC output's "Shorts (Yes/No)" column.

Rule the QC team asked for: Yes only when a CHANNEL is Shorts-only. Shorts never
appear in the /videos grid, so "Shorts tab present AND no long-form uploads" is
the signature; the /shorts tab is then scraped so the channel still has titles
and thumbnails to be classified from. For a VIDEO item, Yes means the video is
itself a Short.
"""
from __future__ import annotations

import pytest

from ytqc.browser.channel_page import extract_channel
from ytqc.models import VideoExtract
from ytqc.pipeline.video_flow import run_video_flow

from tests.fakes import FakeKimiClient, FakeLLMClient, good_content_output, good_vision_evidence
from tests.fixtures import yt_payloads as P


@pytest.fixture(autouse=True)
def _no_grid_settle_sleep(monkeypatch):
    """The grid-screenshot scroll settles with time.sleep(0.8) per shot — real
    seconds this hermetic test has no use for."""
    import ytqc.browser.channel_page as channel_page
    monkeypatch.setattr(channel_page.time, "sleep", lambda *a, **k: None)


def _kimi(**routes):
    base = {"channel_about": P.CHANNEL_ABOUT_OK}
    base.update(routes)
    return FakeKimiClient(base, default={"ok": False}, screenshot=P.TINY_JPEG_B64)


# ── channel-level ──────────────────────────────────────────────────────────
def test_shorts_only_channel_is_flagged_and_its_shorts_are_scraped():
    kimi = _kimi(channel_tabs=P.CHANNEL_TABS_SHORTS_ONLY,
                 channel_videos=P.CHANNEL_VIDEOS_EMPTY,
                 channel_shorts=P.CHANNEL_SHORTS_OK)
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")

    assert ex.has_shorts_tab is True
    assert ex.long_form_count == 0
    assert ex.shorts_count == 4
    assert ex.is_shorts_only is True
    assert ex.ok is True
    # the /shorts tab was visited and its titles became the catalog
    assert any(u.endswith("/shorts") for u in kimi.navigated)
    assert [t.title for t in ex.recent_videos][0] == "60 second chain clean"
    assert all(t.is_short for t in ex.recent_videos)
    assert ex.provenance["shorts_grid"].endswith(":4")


def test_channel_with_long_form_is_not_shorts_only_and_shorts_tab_is_not_visited():
    kimi = _kimi(channel_tabs=P.CHANNEL_TABS_WITH_SHORTS,
                 channel_videos=P.CHANNEL_VIDEOS_OK,
                 channel_shorts=P.CHANNEL_SHORTS_OK)
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")

    assert ex.has_shorts_tab is True          # it does publish Shorts…
    assert ex.is_shorts_only is False         # …but it isn't Shorts-only
    assert ex.long_form_count == 6 and ex.shorts_count == 0
    assert not any(u.endswith("/shorts") for u in kimi.navigated)   # no extra page load


def test_channel_without_a_shorts_tab_never_visits_shorts():
    kimi = _kimi(channel_tabs=P.CHANNEL_TABS_NO_SHORTS,
                 channel_videos=P.CHANNEL_VIDEOS_EMPTY)
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")

    assert ex.has_shorts_tab is False
    assert ex.is_shorts_only is False
    assert not any(u.endswith("/shorts") for u in kimi.navigated)


def test_empty_shorts_tab_leaves_the_channel_unflagged():
    kimi = _kimi(channel_tabs=P.CHANNEL_TABS_SHORTS_ONLY,
                 channel_videos=P.CHANNEL_VIDEOS_EMPTY,
                 channel_shorts=P.CHANNEL_SHORTS_EMPTY)
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")

    assert ex.is_shorts_only is False
    assert ex.shorts_count == 0
    assert ex.provenance["shorts_grid"] == "none"


def test_missing_tab_probe_degrades_quietly():
    """An unrouted CHANNEL_TABS (older page shape / probe failure) must not
    break extraction — the channel is simply not treated as Shorts-only."""
    kimi = _kimi(channel_videos=P.CHANNEL_VIDEOS_OK)     # no channel_tabs route
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")
    assert ex.ok is True
    assert ex.tabs == [] and ex.has_shorts_tab is False and ex.is_shorts_only is False


def test_shorts_only_channel_record_sets_is_shorts():
    from ytqc.pipeline.channel_flow import run_channel_flow
    kimi = _kimi(channel_tabs=P.CHANNEL_TABS_SHORTS_ONLY,
                 channel_videos=P.CHANNEL_VIDEOS_EMPTY,
                 channel_shorts=P.CHANNEL_SHORTS_OK)
    ex = extract_channel(kimi, "UC1234567890abcdefABCD12")

    llm = FakeLLMClient(by_system={"visual content analyst": good_vision_evidence(),
                                   "CHANNEL-level QC brief": good_content_output()})
    rec = run_channel_flow(llm, ex, run_id="run-shorts")
    assert rec.is_shorts is True


def test_shorts_only_prompt_tells_the_model_the_catalog_is_shorts():
    from ytqc.agents.channel_synthesizer import synthesize_channel
    from ytqc.models import ChannelExtract
    llm = FakeLLMClient(by_system={"CHANNEL-level QC brief": good_content_output()})
    ex = ChannelExtract(channel_id="UCx", title="Shorts Co", is_shorts_only=True)
    synthesize_channel(llm, ex, "", ["a short title"])
    _system, user, _imgs = llm.history[0]
    assert "SHORTS ONLY" in user


# ── video-level ────────────────────────────────────────────────────────────
def _video_rec(duration_s: float, is_live: bool = False):
    llm = FakeLLMClient(by_system={"visual content analyst": good_vision_evidence(),
                                   "senior brand-safety": good_content_output()})
    ex = VideoExtract(video_id="v1", title="t", author="a", duration_s=duration_s,
                      is_live=is_live)
    return run_video_flow(llm, ex, run_id="run-v")


@pytest.mark.parametrize("duration_s,expected", [(45, True), (180, True), (181, False),
                                                 (814, False), (0, False)])
def test_video_is_shorts_by_duration(duration_s, expected):
    assert _video_rec(duration_s).is_shorts is expected


def test_a_short_livestream_is_not_a_short():
    assert _video_rec(60, is_live=True).is_shorts is False
