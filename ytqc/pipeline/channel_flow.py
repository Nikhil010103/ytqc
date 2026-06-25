"""Per-channel flow (catalog-based): extracted channel bundle (about + scrolled
video titles + grid thumbnail screenshots) → safety pre-gate → vision over the
grid screenshots → synthesizer (titles + about + visual evidence) → judge? →
post-gate → QCRecord. Browser-free (runs in analysis workers).

Replaces the old deep-sample-3-videos approach: classification is now driven by
the breadth of the channel's catalog (titles + thumbnails), not per-video transcripts."""
from __future__ import annotations

import logging
import time

from ytqc.agents import judge as judge_mod
from ytqc.agents import safety_gate, validator
from ytqc.agents.channel_synthesizer import synthesize_channel
from ytqc.agents.vision_analyst import analyze_frames, vision_digest
from ytqc.llm.client import LLMClient
from ytqc.models import ChannelExtract, FrameSet, QCRecord
from ytqc.pipeline.video_flow import _fill_record, fill_vidiq

log = logging.getLogger("ytqc.flow.channel")


def run_channel_flow(
    llm: LLMClient,
    extract: ChannelExtract,
    run_id: str,
    analysis_workers: int = 2,        # kept for signature compat (no inner pool now)
) -> QCRecord:
    rec = QCRecord(
        id=extract.channel_id, type="channel", name=extract.title,
        run_id=run_id, provider=llm.provider_name, model=llm.model,
        analyzed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        subscribers=extract.subscribers, total_views=extract.total_views,
        video_count=extract.video_count, velocity_score=extract.velocity_score,
        publish_or_join_date=extract.joined_date, country=extract.country,
        transcript_source="none",     # catalog QC has no transcripts by design
    )
    if not extract.ok:
        rec.status = "ERROR"
        rec.error = extract.error or "extraction failed"
        rec.needs_review = True
        rec.confidence = 0.0
        return rec

    titles = [t.title for t in extract.recent_videos if t.title]

    # 1. deterministic safety pre-gate over channel text + all scraped titles
    hits = safety_gate.scan({
        "title": extract.title,
        "description": extract.description,
        "tags": extract.channel_keywords,
        "recent_titles": " ".join(titles),
    })

    # 2. vision over the /videos grid thumbnails (failure-isolated). The grid shots
    #    show the videos' thumbnails; the home screenshot rides along as the lead image.
    fs = FrameSet(
        thumbnail_b64=extract.home_screenshot_b64,
        frames_b64=extract.grid_screenshots_b64,
        method="screenshot" if (extract.grid_screenshots_b64 or extract.home_screenshot_b64) else "none",
    )
    labels = [f"videos-grid screenshot {i + 1}" for i in range(len(extract.grid_screenshots_b64))]
    vision = analyze_frames(llm, fs, extract.title, extract.title, frame_labels=labels)

    # 3. synthesize channel verdict from about + titles + visual evidence
    tier_recovered = False
    judge_invoked = False
    try:
        out, tier_recovered = synthesize_channel(llm, extract, vision_digest(vision), titles)
    except validator.ValidationError as exc:
        rec.status = "ERROR"
        rec.error = f"validation: {exc}"
        rec.needs_review = True
        rec.confidence = 0.0
        return rec

    # 4. conditional judge (reconcile synthesizer vs visual evidence)
    conflicts = judge_mod.detect_conflicts(out, vision)
    if conflicts:
        judge_invoked = True
        out = judge_mod.adjudicate(llm, out, conflicts)

    # 5. deterministic post-gate (floor risk to any rule-gate hits)
    floored, cats = safety_gate.enforce_floor(
        out.brand_safety.risk_level, out.brand_safety.triggered_categories, hits
    )
    out.brand_safety.risk_level = floored
    out.brand_safety.triggered_categories = cats
    out.brand_safety.is_safe = floored in ("none", "low")

    _fill_record(rec, out)
    fill_vidiq(rec, extract.vidiq, llm)
    rec.judge_invoked = judge_invoked
    rec.confidence = validator.compute_confidence(
        transcript_source="none",
        vision_ok=vision.ok,
        tier_recovered=tier_recovered,
        judge_invoked=judge_invoked,
        expect_transcript=False,       # no transcript expected for catalog QC
    )
    # A thin catalog or a failed vision pass means a core channel signal is missing.
    if not vision.ok or len(titles) < 5 or not extract.grid_screenshots_b64:
        rec.needs_review = True
    return rec
