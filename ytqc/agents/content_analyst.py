"""Content Analyst — the merged taxonomy + brand-safety + audience call
(mirrors' proven single-call design; splitting it triples latency on a 31B
model for no accuracy gain)."""
from __future__ import annotations

import logging

from ytqc.agents import validator
from ytqc.llm.client import LLMClient
from ytqc.llm.prompts import (
    CONTENT_ANALYST_SYSTEM,
    build_comments_block,
    build_video_user_message,
)
from ytqc.models import AnalystOutput, VideoExtract

log = logging.getLogger("ytqc.analyst")


def analyze_video(
    llm: LLMClient,
    extract: VideoExtract,
    vision_digest_str: str,
    rule_hits_block: str,
) -> tuple[AnalystOutput, bool]:
    """Returns (validated output, tier_recovered_flag).
    Raises after exhausting retries (caller may route to judge/error row)."""
    comments_block = build_comments_block(
        extract.comments.top_comments, extract.comments.count_text
    )
    user = build_video_user_message(extract, vision_digest_str, rule_hits_block, comments_block)

    tier_recovered = False
    last_exc: Exception | None = None
    for temp in (0.1, 0.3, 0.6):
        raw = llm.chat_json(CONTENT_ANALYST_SYSTEM, user, temperature=temp, escalate=False)
        try:
            out = validator.normalize(raw, extract.video_id)
            return out, tier_recovered
        except validator.ValidationError as exc:
            last_exc = exc
            tier_recovered = True
            log.warning("validation failed at temp %.1f: %s — escalating", temp, exc)
    raise validator.ValidationError(
        f"content analyst failed validation at all temperatures: {last_exc}"
    )
