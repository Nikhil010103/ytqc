"""Transcript acquisition — panel scrape is the ONLY working path (2026):
the timedtext fetch and direct innertube get_transcript are PO-token/auth
gated (probe-confirmed dead). Caption-track metadata from the player response
still provides manual-vs-ASR provenance."""
from __future__ import annotations

import logging
import time

from ytqc.browser import youtube_js as J
from ytqc.browser.webbridge import KimiClient
from ytqc.models import TranscriptResult
from ytqc.sampling import TimedSegment, format_excerpts, plan_sampling
from ytqc.utils.parsing import parse_timestamp_to_seconds

log = logging.getLogger("ytqc.transcript")


def fetch_transcript(
    kimi: KimiClient,
    duration_s: float,
    tracks: list[dict],
    target_min: float = 60.0,
    target_max: float = 120.0,
    pct: float = 0.25,
    n_windows: int = 5,
) -> tuple[TranscriptResult, list[float]]:
    """Open the transcript panel, scrape segments, sample windows.
    Returns (TranscriptResult, frame_timestamps)."""
    result = TranscriptResult()
    if tracks:
        # provenance only — prefer reporting a manual track if one exists
        manual = [t for t in tracks if t.get("kind") != "asr"]
        chosen = (manual or tracks)[0]
        result.track_kind = "manual" if manual else "asr"
        result.track_lang = chosen.get("lang")

    segments: list[TimedSegment] = []
    state = kimi.js(J.TRANSCRIPT_OPEN)
    if isinstance(state, dict) and state.get("state") in ("clicked", "open"):
        deadline = time.time() + 6.0
        while time.time() < deadline:
            out = kimi.js(J.TRANSCRIPT_SCRAPE)
            if isinstance(out, dict) and out.get("n", 0) > 0:
                segments = [
                    TimedSegment(parse_timestamp_to_seconds(s["t"]), s["text"])
                    for s in out["segs"]
                ]
                break
            time.sleep(0.5)

    windows = plan_sampling(
        duration_s, segments or None,
        target_min=target_min, target_max=target_max, pct=pct, n_windows=n_windows,
    )
    frame_ts = [w.frame_t for w in windows]

    if segments:
        result.source = "panel"
        result.segments = [{"start_s": s.start_s, "text": s.text} for s in segments]
        result.excerpt_block = format_excerpts(windows, duration_s)
    else:
        result.source = "none"
        log.info("no transcript available (state=%s)", state)
    return result, frame_ts
