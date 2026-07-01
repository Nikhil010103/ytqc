"""Video watch-page extraction: player-response metadata (primary, stable),
likes via aria-label, comments via deep scroll — all probe-validated."""
from __future__ import annotations

import logging
import time

from ytqc.browser import youtube_js as J
from ytqc.browser.frames import capture_frames
from ytqc.browser.transcript import fetch_transcript
from ytqc.browser.vidiq import scrape_vidiq
from ytqc.browser.webbridge import KimiClient
from ytqc.config import SamplingConfig
from ytqc.models import CommentData, VideoExtract
from ytqc.utils.parsing import parse_count

log = logging.getLogger("ytqc.video")


def extract_video(
    kimi: KimiClient,
    video_id: str,
    sampling: SamplingConfig,
    depth: str = "full",                  # "full" | "lite"
    with_comments: bool = True,
    overlap_comments: bool = False,       # pre-trigger comment load (poll-guarded harvest)
    with_vidiq: bool = False,             # scrape the VidIQ overlay (full depth only)
    vidiq_timeout_s: float = 8.0,
) -> VideoExtract:
    ex = VideoExtract(video_id=video_id)
    kimi.navigate(f"https://www.youtube.com/watch?v={video_id}", ready_js=J.WATCH_READY)

    pr = kimi.js(J.PLAYER_RESPONSE)
    if not (isinstance(pr, dict) and pr.get("ok")):
        ex.ok = False
        ex.error = "player response unavailable (page failed to load?)"
        ex.provenance["metadata"] = "none"
        return ex

    # Unavailable / deleted / private / age-gated: player response parsed but the
    # video is not playable — bail before hallucinating verdicts on empty details.
    if (pr.get("status") and pr["status"] != "OK") or not pr.get("hasVideoDetails", True):
        ex.ok = False
        ex.error = pr.get("reason") or pr.get("status") or "video unavailable"
        ex.provenance["metadata"] = "unavailable"
        return ex

    ex.title = pr.get("title", "")
    ex.author = pr.get("author", "")
    ex.channel_id = pr.get("channelId", "")
    ex.duration_s = float(pr.get("lengthSeconds") or 0)
    ex.view_count = int(pr.get("viewCount") or 0)
    ex.keywords = pr.get("keywords") or []
    ex.description = pr.get("shortDescription", "")
    ex.youtube_category = pr.get("category", "")
    raw_publish = pr.get("publishDate", "")    # ISO ts, e.g. 2026-05-12T20:50:47-07:00
    ex.is_family_safe = pr.get("isFamilySafe")
    ex.is_live = bool(pr.get("isLiveContent"))
    ex.provenance["metadata"] = "player_response"

    if raw_publish:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(raw_publish)   # full ts → precise age
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            ex.days_since_publish = max((now - dt).total_seconds() / 86400, 0.04)
        except ValueError:
            pass
    ex.publish_date = raw_publish.split("T")[0]        # store/display just YYYY-MM-DD
    ex.views_per_day = round(ex.view_count / max(ex.days_since_publish, 1.0), 1)

    likes = kimi.js(J.LIKES)
    if isinstance(likes, dict):
        ex.likes = parse_count(likes.get("likes_text", ""))
        ex.provenance["likes"] = "dom-aria" if likes.get("likes_text") else "none"

    lite = depth == "lite"
    # OPT-IN: kick off the comment lazy-load now so it fetches in the background
    # while we do transcript + frames; the harvest below still polls until the
    # count populates, so this can only be equal-or-faster, never lossy.
    primed = False
    if overlap_comments and not lite and with_comments:
        _prime_comments(kimi)
        primed = True

    # transcript + aligned frame timestamps
    transcript, frame_ts = fetch_transcript(
        kimi, ex.duration_s, pr.get("tracks") or [],
        target_min=30.0 if lite else sampling.transcript_s_min,
        target_max=60.0 if lite else sampling.transcript_s_max,
        pct=sampling.transcript_pct,
        n_windows=sampling.frames_lite if lite else sampling.frames_full,
    )
    ex.transcript = transcript
    ex.provenance["transcript"] = transcript.source

    n_frames = sampling.frames_lite if lite else sampling.frames_full
    ex.frames = capture_frames(kimi, video_id, frame_ts[:n_frames], is_live=ex.is_live)
    ex.provenance["frames"] = ex.frames.method

    if not lite and with_comments:
        ex.comments = _extract_comments(kimi, sampling.comments_top_n, primed=primed)
        ex.provenance["comments"] = "dom" if ex.comments.top_comments else "none"

    # VidIQ overlay (optional, failure-isolated). Scraped last so the extension's
    # async panel has had the whole extraction to render; lite samples skip it.
    if with_vidiq and not lite:
        ex.vidiq = scrape_vidiq(kimi, "video", timeout_s=vidiq_timeout_s)
        ex.provenance["vidiq"] = "panel" if ex.vidiq.ok else "none"
    return ex


def _prime_comments(kimi: KimiClient) -> None:
    """Scroll to trigger YouTube's lazy comment load, then return to the player so
    frame capture is unaffected. Best-effort — failures are swallowed (the harvest
    poll still works as before)."""
    try:
        for px in (1200, 1200, 800):
            kimi.scroll(px, settle=0.4)
        kimi.js("window.scrollTo(0, 0)")
    except Exception as exc:
        log.warning("comment prime failed (harmless, will harvest normally): %s", exc)


def _extract_comments(kimi: KimiClient, top_n: int, primed: bool = False) -> CommentData:
    cd = CommentData()
    try:
        if not primed:                       # not pre-triggered → scroll to load now
            for px in (1200, 1200, 800):
                kimi.scroll(px, settle=1.0)
        # the comment-count number populates a beat AFTER the threads render, so
        # poll briefly for header text that actually contains a digit instead of
        # grabbing the placeholder "Comments" label.
        out = {}
        deadline = time.time() + 4.0
        while time.time() < deadline:
            out = kimi.js(J.COMMENTS.replace("__TOP_N__", str(top_n)))
            if isinstance(out, dict) and any(ch.isdigit() for ch in out.get("count_text", "")):
                break
            time.sleep(0.5)
        if isinstance(out, dict):
            cd.count_text = out.get("count_text", "")
            cd.count = parse_count(cd.count_text)      # "3,496 Comments" -> 3496
            cd.top_comments = out.get("comments") or []
        kimi.js("window.scrollTo(0, 0)")
    except Exception as exc:
        log.warning("comments extraction failed: %s", exc)
    return cd
