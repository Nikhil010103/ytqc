"""Per-video flow: EXTRACT(ed bundle) → vision → content analyst → judge? →
validate/post-gate → QCRecord. Browser work happens in the orchestrator's
producer; this module is browser-free so it can run in analysis workers."""
from __future__ import annotations

import logging
import time

from ytqc.agents import judge as judge_mod
from ytqc.agents import safety_gate, validator
from ytqc.agents.content_analyst import analyze_video
from ytqc.agents.vidiq_insights import generate_vidiq_insight
from ytqc.agents.vision_analyst import analyze_frames, vision_digest
from ytqc.llm.client import LLMClient
from ytqc.models import QCRecord, VideoExtract, VidIQStats

log = logging.getLogger("ytqc.flow.video")


def run_video_flow(llm: LLMClient, extract: VideoExtract, run_id: str) -> QCRecord:
    rec = QCRecord(
        id=extract.video_id, type="video", name=extract.title,
        run_id=run_id, provider=llm.provider_name, model=llm.model,
        analyzed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        subscribers=0, total_views=extract.view_count,
        views_per_day=extract.views_per_day, likes=extract.likes,
        comments_count=extract.comments.count,
        publish_or_join_date=extract.publish_date,
        duration_s=extract.duration_s,
        transcript_source=extract.transcript.source,
    )
    if not extract.ok:
        rec.status = "ERROR"
        rec.error = extract.error or "extraction failed"
        rec.needs_review = True
        rec.confidence = 0.0
        return rec

    # 1. deterministic safety pre-gate
    transcript_text = " ".join(s.get("text", "") for s in extract.transcript.segments)
    hits = safety_gate.scan({
        "title": extract.title,
        "description": extract.description,
        "tags": " ".join(extract.keywords),
        "transcript": transcript_text[:20000],
    })

    # 2. vision analyst (failure-isolated)
    vision = analyze_frames(llm, extract.frames, extract.title, extract.author)

    # 3. content analyst (merged taxonomy+safety+audience call)
    tier_recovered = False
    judge_invoked = False
    try:
        out, tier_recovered = analyze_video(
            llm, extract, vision_digest(vision), safety_gate.hits_block(hits)
        )
    except validator.ValidationError as exc:
        rec.status = "ERROR"
        rec.error = f"validation: {exc}"
        rec.needs_review = True
        rec.confidence = 0.0
        return rec

    # 4. conditional judge
    conflicts = judge_mod.detect_conflicts(out, vision)
    if conflicts:
        judge_invoked = True
        out = judge_mod.adjudicate(llm, out, conflicts)

    # 5. deterministic post-gate + confidence
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
        transcript_source=extract.transcript.source,
        vision_ok=vision.ok,
        tier_recovered=tier_recovered,
        judge_invoked=judge_invoked,
    )
    # Partial extraction must always surface for review even if confidence is
    # high: a missing transcript or missing frames means a core signal is absent.
    if extract.transcript.source == "none" or extract.frames.method == "none":
        rec.needs_review = True
    return rec


def _fill_record(rec: QCRecord, out) -> None:
    rec.tier_1 = out.tier_1
    rec.tier_2 = out.tier_2
    rec.tier_classification_reasoning = out.tier_classification_reasoning
    rec.brand_safety_is_safe = out.brand_safety.is_safe
    rec.brand_safety_risk_level = out.brand_safety.risk_level
    rec.brand_safety_triggered_categories = out.brand_safety.triggered_categories
    rec.brand_safety_explanation = out.brand_safety.explanation
    rec.brand_unsafe_category = validator.derive_brand_unsafe_category(out.brand_safety)
    rec.language = out.language
    rec.targeted_region = out.targeted_region
    rec.kids_age_group = out.kids_age_group
    rec.audience_age_group = out.targeted_audience.age_group
    rec.audience_gender = out.targeted_audience.gender
    rec.audience_interests = out.targeted_audience.interests
    rec.keywords = out.keywords
    rec.content_themes = out.content_themes
    rec.topics = out.topics
    rec.sentiment = out.sentiment
    rec.summary = out.summary
    rec.suitable_age_group = out.suitable_age_group
    rec.is_premium_luxury = out.is_premium_luxury
    rec.comment = out.qc_notes


def fill_vidiq(rec: QCRecord, v: VidIQStats, llm: LLMClient) -> None:
    """Copy the verbatim VidIQ fields onto the record, then attach an AI insight.
    Both halves are no-ops when the panel was absent — VidIQ is auxiliary, so this
    never touches confidence/needs_review."""
    if not v.ok:
        return
    rec.vidiq_subscribers = v.subscribers_text
    rec.vidiq_video_count = v.video_count_text
    if v.scope == "channel":
        rec.vidiq_subscribers_growth = v.subscribers_growth_text
        rec.vidiq_views_gained_7d = v.views_gained_7d_text
        rec.vidiq_rank = v.rank_text
        rec.vidiq_est_monthly_earnings = v.est_monthly_earnings_text
        rec.vidiq_avg_video_length = v.avg_video_length_text
        rec.vidiq_upload_frequency = v.upload_frequency_text
        rec.vidiq_similar_channels = v.similar_channels
    else:
        rec.vidiq_total_views = v.total_views_text
        rec.vidiq_channel_age = v.channel_age_text
        rec.vidiq_seo_score = v.seo_score_text
    rec.vidiq_controversial_keywords = v.controversial_keywords
    rec.vidiq_controversial_locked = v.controversial_locked

    vq = generate_vidiq_insight(llm, v, rec.name, rec.type)
    rec.vidiq_insight = vq["insight"]
    rec.vidiq_signals = vq["signals"]
