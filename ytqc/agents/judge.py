"""Conditional Judge — fires only on detected conflicts (~15-20% of items)."""
from __future__ import annotations

import json
import logging

from ytqc.llm.client import LLMClient
from ytqc.llm.prompts import JUDGE_SYSTEM, TIER_1_LIST_BLOCK
from ytqc.models import AnalystOutput, VisionEvidence

log = logging.getLogger("ytqc.judge")


def detect_conflicts(
    out: AnalystOutput,
    vision: VisionEvidence | None,
    draft: dict | None = None,
) -> list[dict]:
    """Returns a list of conflict descriptors; empty = no judge needed."""
    conflicts: list[dict] = []
    if vision and vision.ok:
        kids_visual = bool(vision.visual_kids_signals.get("present"))
        if kids_visual and out.tier_1 != "Kids":
            conflicts.append({
                "field": "tier_1",
                "content_analyst": out.tier_1,
                "vision_analyst": "Kids (visual kids signals: "
                                  + ", ".join(vision.visual_kids_signals.get("signals", [])[:3]) + ")",
                "reasoning": out.tier_classification_reasoning,
            })
        high_visual = [f for f in vision.visual_safety_flags
                       if f.get("severity") in ("medium", "high")]
        if high_visual and out.brand_safety.risk_level == "none":
            conflicts.append({
                "field": "brand_safety.risk_level",
                "content_analyst": "none",
                "vision_analyst": json.dumps(high_visual[:3]),
            })
        if (vision.visible_language and out.language
                and vision.visible_language != out.language):
            conflicts.append({
                "field": "language",
                "content_analyst": out.language,
                "vision_analyst": vision.visible_language,
            })
    if draft:
        if draft.get("tier_1") and out.tier_1 != draft["tier_1"] \
                and draft.get("tier_1_vote_share", 0) >= 0.5:
            conflicts.append({
                "field": "tier_1",
                "synthesizer": out.tier_1,
                "draft_aggregate": f"{draft['tier_1']} (vote share {draft['tier_1_vote_share']})",
                "synthesizer_notes": out.qc_notes,
            })
        if draft.get("tier_1_vote_share", 1.0) < 0.5:
            conflicts.append({
                "field": "tier_1",
                "issue": "no majority among sampled-video briefs",
                "votes": draft.get("tier_votes"),
                "synthesizer": out.tier_1,
            })
    return conflicts


def adjudicate(llm: LLMClient, out: AnalystOutput, conflicts: list[dict]) -> AnalystOutput:
    user = (
        "## CONFLICT REPORT\n"
        + json.dumps(conflicts, ensure_ascii=False, indent=1)
        + "\n\n## CLOSED TIER_1 LIST\n" + TIER_1_LIST_BLOCK
        + "\n\n## CURRENT RECORD (fields under dispute may be revised)\n"
        + json.dumps({
            "tier_1": out.tier_1, "tier_2": out.tier_2,
            "language": out.language,
            "risk_level": out.brand_safety.risk_level,
            "kids_age_group": out.kids_age_group,
        }, ensure_ascii=False)
    )
    try:
        raw = llm.chat_json(JUDGE_SYSTEM, user, temperature=0.1)
        resolved = raw.get("resolved_fields") or {}
        notes = (raw.get("judge_notes") or "").strip()
        if "tier_1" in resolved:
            out.tier_1 = resolved["tier_1"]
        if "tier_2" in resolved:
            out.tier_2 = str(resolved["tier_2"]).lower()
        if "language" in resolved:
            out.language = str(resolved["language"])[:2].lower()
        if "brand_safety.risk_level" in resolved or "risk_level" in resolved:
            lvl = str(resolved.get("brand_safety.risk_level") or resolved.get("risk_level")).lower()
            if lvl in ("none", "low", "medium", "high"):
                out.brand_safety.risk_level = lvl
                out.brand_safety.is_safe = lvl in ("none", "low")
        if "kids_age_group" in resolved:
            out.kids_age_group = resolved["kids_age_group"]
        if notes:
            out.qc_notes = (out.qc_notes + " | Judge: " + notes).strip(" |")[:400]
    except Exception as exc:
        log.warning("judge failed (keeping analyst output): %s", exc)
    return out
