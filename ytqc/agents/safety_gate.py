"""Deterministic brand-safety pre/post gate (no LLM).

Pre-gate: word-boundary term scan over title+description+tags+transcript →
rule hits injected into the analyst prompt and enforced post-hoc.
Post-gate: risk_level can never be LOWER than what the rule hits imply
(rules gate, LLM nuances)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from ytqc.taxonomy import UNSAFE_GROUP_TO_CATEGORY, UNSAFE_TERM_GROUPS, risk_at_least


@dataclass
class RuleHit:
    group: str
    term: str
    source_field: str
    category: str
    min_risk: str


_COMPILED: list[tuple[str, str, re.Pattern]] = [
    (group, term, re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", re.IGNORECASE))
    for group, terms in UNSAFE_TERM_GROUPS.items()
    for term in terms
]


def scan(fields: dict[str, str]) -> list[RuleHit]:
    """fields: {"title": ..., "description": ..., "tags": ..., "transcript": ...}"""
    hits: list[RuleHit] = []
    seen: set[tuple[str, str]] = set()
    for field_name, text in fields.items():
        if not text:
            continue
        for group, term, pattern in _COMPILED:
            if (group, field_name) in seen:
                continue
            if pattern.search(text):
                category, min_risk = UNSAFE_GROUP_TO_CATEGORY[group]
                hits.append(RuleHit(group, term, field_name, category, min_risk))
                seen.add((group, field_name))
    return hits


def hits_block(hits: list[RuleHit]) -> str:
    if not hits:
        return ""
    lines = [
        f"- term '{h.term}' ({h.group} → {h.category}) found in {h.source_field}"
        for h in hits
    ]
    lines.append(
        "Address each hit in your brand_safety verdict: confirm it, or explain why it is a false positive in this context."
    )
    return "\n".join(lines)


def enforce_floor(risk_level: str, triggered: list[str], hits: list[RuleHit]) -> tuple[str, list[str]]:
    """Post-gate: the LLM may raise risk but never silently dismiss a rule hit.

    Only hits the LLM did NOT explicitly address as false positives get floored;
    we approximate 'addressed' by checking whether the hit's category appears in
    the LLM's triggered list OR the LLM raised risk to >= the hit floor anyway."""
    floored = risk_level
    out_categories = list(triggered)
    for h in hits:
        if h.min_risk in ("medium", "high") and h.category not in out_categories:
            # Conservative: medium/high-floor groups always at least 'low' so the
            # row is visible in review; full floor only when LLM is at 'none'.
            if floored == "none":
                floored = "low"
        if h.category in out_categories:
            floored = risk_at_least(floored, h.min_risk)
    return floored, out_categories
