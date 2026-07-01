"""Deterministic validator — final authority on vocabulary and structure.

The normalization/XOR block enforces structure deterministically — never trust
the LLM's output blindly. Also computes the deterministic confidence score and
the needs_review flag."""
from __future__ import annotations

import logging

from ytqc.models import AnalystOutput, BrandSafety, TargetedAudience
from ytqc.taxonomy import (
    ADULT_AGE_BANDS,
    GENDER_VALUES,
    KIDS_AGE_GROUPS,
    PROMPT_TRIGGER_TO_CATEGORY,
    RISK_LEVELS,
    SAFETY_CATEGORIES,
    TIER_1_CATEGORIES,
)

log = logging.getLogger("ytqc.validator")

_SUITABLE_AGE_VALUES = {"all ages", "13+", "16+", "18+"}


class ValidationError(Exception):
    """Raised when tier_1 is unrecoverable — triggers analyst retry/judge."""


def normalize(llm_result: dict, item_id: str = "?") -> AnalystOutput:
    # ── tier_1 / tier_2 / keywords / language / targeted_region ───────────
    tier_1_raw = (llm_result.get("tier_1") or "").strip()
    tier_1 = tier_1_raw if tier_1_raw in TIER_1_CATEGORIES else None
    if tier_1_raw and tier_1 is None:
        # Case-insensitive recovery before giving up — LLM sometimes lowercases.
        lower_map = {c.lower(): c for c in TIER_1_CATEGORIES}
        tier_1 = lower_map.get(tier_1_raw.lower())
    if tier_1 is None:
        raise ValidationError(f"unrecoverable tier_1 {tier_1_raw!r} for {item_id}")

    tier_2 = (llm_result.get("tier_2") or "").strip().lower() or None
    tier_reasoning = (llm_result.get("tier_classification_reasoning") or "").strip()[:200] or None
    keywords = [
        str(k).strip().lower()
        for k in (llm_result.get("keywords") or [])
        if str(k).strip()
    ][:8]
    language = (llm_result.get("language") or "").strip() or None
    if language and language.lower() == "unknown":
        language = None
    if language and len(language) > 2:
        language = language[:2].lower()         # clamp to ISO 639-1
    targeted_region = (llm_result.get("targeted_region") or "").strip() or None

    # ── kids_age_group / targeted_audience.age_group (XOR by tier_1) ──────
    ta_raw = llm_result.get("targeted_audience") or {}
    gender = (ta_raw.get("gender") or "").strip().lower()
    if gender not in GENDER_VALUES:
        gender = "any"
    interests = [
        str(i).strip()
        for i in (ta_raw.get("interests") or [])
        if str(i).strip()
    ][:5]

    is_kids = tier_1 == "Kids"
    kids_age_group_raw = (llm_result.get("kids_age_group") or "").strip()
    kids_age_group = kids_age_group_raw if kids_age_group_raw in KIDS_AGE_GROUPS else None
    adult_age_group = (ta_raw.get("age_group") or "").strip() or None
    if adult_age_group and adult_age_group not in ADULT_AGE_BANDS:
        adult_age_group = "general adult"

    if is_kids:
        # Enforce XOR — kids_age_group required, adult age_group nulled out.
        if kids_age_group is None:
            log.warning("tier_1=Kids but invalid kids_age_group %r for %s",
                        kids_age_group_raw, item_id)
        adult_age_group = None
    else:
        kids_age_group = None
        if adult_age_group is None:
            adult_age_group = "general adult"

    # ── brand safety ───────────────────────────────────────────────────────
    bs_raw = llm_result.get("brand_safety") or {}
    risk_level = (bs_raw.get("risk_level") or "none").strip().lower()
    if risk_level not in RISK_LEVELS:
        risk_level = "medium"                   # unparseable risk → conservative
    triggered = []
    for cat in bs_raw.get("triggered_categories") or []:
        cat_s = str(cat).strip()
        mapped = PROMPT_TRIGGER_TO_CATEGORY.get(cat_s.lower(), cat_s)
        if mapped in SAFETY_CATEGORIES and mapped not in triggered:
            triggered.append(mapped)
    is_safe = risk_level in ("none", "low")     # derived, never trusted from LLM
    brand_safety = BrandSafety(
        is_safe=is_safe,
        risk_level=risk_level,
        triggered_categories=triggered,
        explanation=(bs_raw.get("explanation") or "").strip()[:400],
    )

    suitable = (llm_result.get("suitable_age_group") or "").strip().lower()
    if suitable not in _SUITABLE_AGE_VALUES:
        suitable = "all ages" if brand_safety.is_safe else "18+"

    sentiment = (llm_result.get("sentiment") or "neutral").strip().lower()
    if sentiment not in {"positive", "neutral", "mixed", "negative"}:
        sentiment = "neutral"

    return AnalystOutput(
        summary=(llm_result.get("summary") or "").strip(),
        hook=(llm_result.get("hook") or "").strip(),
        content_themes=[str(t).strip() for t in (llm_result.get("content_themes") or [])][:5],
        topics=[str(t).strip() for t in (llm_result.get("topics") or [])][:10],
        sentiment=sentiment,
        comment_sentiment=llm_result.get("comment_sentiment") or {},
        primary_audience=(llm_result.get("primary_audience") or "").strip(),
        target_industries=[str(t).strip() for t in (llm_result.get("target_industries") or [])][:6],
        brand_safety=brand_safety,
        tier_1=tier_1,
        tier_2=tier_2,
        tier_classification_reasoning=tier_reasoning,
        keywords=keywords,
        language=language,
        targeted_region=targeted_region,
        kids_age_group=kids_age_group,
        targeted_audience=TargetedAudience(
            age_group=adult_age_group, gender=gender, interests=interests
        ),
        suitable_age_group=suitable,
        is_premium_luxury=bool(llm_result.get("is_premium_luxury", False)),
        qc_notes=(llm_result.get("qc_notes") or "").strip()[:400],
    )


def derive_brand_unsafe_category(brand_safety: BrandSafety) -> str | None:
    """The single QC column when unsafe: first (highest-priority) triggered
    category; priority = order in SAFETY_CATEGORIES."""
    if brand_safety.is_safe or not brand_safety.triggered_categories:
        return None
    order = {c: i for i, c in enumerate(SAFETY_CATEGORIES)}
    return min(brand_safety.triggered_categories, key=lambda c: order.get(c, 99))


def compute_confidence(
    *,
    transcript_source: str,
    vision_ok: bool,
    tier_recovered: bool,
    judge_invoked: bool,
    vote_unanimous: bool = True,
    language_consistent: bool = True,
    expect_transcript: bool = True,
) -> float:
    conf = 1.0
    # Channel catalog QC has no transcript by design — don't penalize its absence.
    if expect_transcript and transcript_source == "none":
        conf -= 0.15
    if not vision_ok:
        conf -= 0.10
    if tier_recovered:
        conf -= 0.10
    if judge_invoked:
        conf -= 0.20
    if not vote_unanimous:
        conf -= 0.10
    if not language_consistent:
        conf -= 0.10
    return max(round(conf, 2), 0.0)
