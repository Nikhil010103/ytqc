"""VidIQ Insights — one small LLM call that turns the raw VidIQ overlay stats
into a user-understandable summary + a few structured signal bullets.

Failure-isolated like the Vision Analyst: returns a safe empty result on any
error or when the panel was absent, so it can never break the flow."""
from __future__ import annotations

import json
import logging

from ytqc.llm.client import LLMClient
from ytqc.llm.prompts import VIDIQ_INSIGHTS_SYSTEM
from ytqc.models import VidIQStats

log = logging.getLogger("ytqc.vidiq.insights")


def generate_vidiq_insight(llm: LLMClient, vidiq: VidIQStats, name: str, scope: str) -> dict:
    """Returns {"insight": str, "signals": list[str]}; empty when the panel was
    absent/failed or the LLM call errors."""
    if not (vidiq.ok and vidiq.present):
        return {"insight": "", "signals": []}

    # only the populated fields (raw_text excluded — it's noisy; the named fields
    # plus the locked flag carry the signal the prompt needs)
    facts = vidiq.model_dump(
        exclude={"ok", "present", "scope", "error", "raw_text"},
        exclude_defaults=True,
    )
    if not facts:
        return {"insight": "", "signals": []}

    user = (
        f"{scope.title()}: {name!r}\n"
        f"VidIQ panel stats (verbatim JSON):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
        "Summarize per the schema."
    )
    try:
        raw = llm.chat_json(VIDIQ_INSIGHTS_SYSTEM, user, temperature=0.1)
        return {
            "insight": str(raw.get("insight", ""))[:600],
            "signals": [str(s)[:120] for s in (raw.get("signals") or [])][:5],
        }
    except Exception as exc:
        log.warning("vidiq insight failed (harmless): %s", exc)
        return {"insight": "", "signals": []}
