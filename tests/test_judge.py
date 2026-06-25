"""Tests for ytqc.agents.judge — detect_conflicts (pure) + adjudicate (LLM).

detect_conflicts is exercised directly across every branch with real
AnalystOutput / VisionEvidence objects. adjudicate is driven by FakeLLMClient
so no network/Ollama is touched; an LLM exception is verified to leave the
record unchanged (exception isolation).
"""
from __future__ import annotations

import pytest

from ytqc.agents.judge import adjudicate, detect_conflicts
from ytqc.models import AnalystOutput, BrandSafety, VisionEvidence

from tests.fakes import FakeLLMClient, good_judge_output


# ──────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────
def _analyst(**ov) -> AnalystOutput:
    """A clean baseline AnalystOutput (Automobiles / en / risk none)."""
    base = dict(
        summary="Track-day review.",
        tier_1="Automobiles",
        tier_2="motorcycle reviews",
        tier_classification_reasoning="Tag ninja zx-4r -> Automobiles.",
        language="en",
        kids_age_group=None,
        qc_notes="",
        brand_safety=BrandSafety(is_safe=True, risk_level="none"),
    )
    base.update(ov)
    return AnalystOutput(**base)


def _vision(**ov) -> VisionEvidence:
    """A clean baseline VisionEvidence (ok, no kids/safety flags, en)."""
    base = dict(
        ok=True,
        visual_kids_signals={"present": False, "signals": []},
        visual_safety_flags=[],
        visible_language="en",
    )
    base.update(ov)
    return VisionEvidence(**base)


# ──────────────────────────────────────────────────────────────────────────
# detect_conflicts — clean / no-conflict
# ──────────────────────────────────────────────────────────────────────────
def test_no_conflict_clean_vision_no_draft():
    """Clean vision + matching tier_1/lang + no draft -> empty conflict list."""
    assert detect_conflicts(_analyst(), _vision(), None) == []


def test_no_conflict_when_vision_is_none():
    """vision=None skips all vision branches; clean -> no conflicts."""
    assert detect_conflicts(_analyst(), None, None) == []


def test_no_conflict_when_vision_not_ok():
    """vision.ok=False suppresses every vision-derived conflict."""
    vision = _vision(
        ok=False,
        visual_kids_signals={"present": True, "signals": ["nursery rhyme"]},
        visible_language="es",
    )
    # Despite kids signal + language mismatch, ok=False short-circuits.
    assert detect_conflicts(_analyst(), vision, None) == []


# ──────────────────────────────────────────────────────────────────────────
# detect_conflicts — kids-visual conflict
# ──────────────────────────────────────────────────────────────────────────
def test_kids_visual_conflict_raises_tier_1():
    """Vision says kids present but tier_1 != Kids -> tier_1 conflict."""
    vision = _vision(
        visual_kids_signals={"present": True,
                             "signals": ["bright cartoon", "nursery rhyme", "toys"]},
    )
    conflicts = detect_conflicts(_analyst(tier_1="Automobiles"), vision, None)
    tier_conf = [c for c in conflicts if c["field"] == "tier_1"]
    assert len(tier_conf) == 1
    c = tier_conf[0]
    assert c["content_analyst"] == "Automobiles"
    assert c["vision_analyst"].startswith("Kids (visual kids signals:")
    # only the first 3 signals are listed
    assert "toys" in c["vision_analyst"]


def test_kids_visual_no_conflict_when_already_kids():
    """If tier_1 is already Kids, the kids-visual branch does not fire."""
    vision = _vision(visual_kids_signals={"present": True, "signals": ["toys"]})
    conflicts = detect_conflicts(_analyst(tier_1="Kids"), vision, None)
    assert [c for c in conflicts if c["field"] == "tier_1"] == []


# ──────────────────────────────────────────────────────────────────────────
# detect_conflicts — visual safety vs none
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("severity", ["medium", "high"])
def test_visual_safety_vs_none_conflict(severity):
    """Vision safety flag medium/high but risk_level none -> conflict."""
    vision = _vision(visual_safety_flags=[{"category": "violence", "severity": severity}])
    out = _analyst(brand_safety=BrandSafety(is_safe=True, risk_level="none"))
    conflicts = detect_conflicts(out, vision, None)
    safety = [c for c in conflicts if c["field"] == "brand_safety.risk_level"]
    assert len(safety) == 1
    assert safety[0]["content_analyst"] == "none"
    assert severity in safety[0]["vision_analyst"]


def test_visual_safety_low_severity_no_conflict():
    """A 'low' severity visual flag does not trip the safety conflict."""
    vision = _vision(visual_safety_flags=[{"category": "mild", "severity": "low"}])
    out = _analyst(brand_safety=BrandSafety(is_safe=True, risk_level="none"))
    conflicts = detect_conflicts(out, vision, None)
    assert [c for c in conflicts if c["field"] == "brand_safety.risk_level"] == []


def test_visual_safety_no_conflict_when_risk_already_elevated():
    """If risk_level is already not 'none', no safety conflict is raised."""
    vision = _vision(visual_safety_flags=[{"category": "violence", "severity": "high"}])
    out = _analyst(brand_safety=BrandSafety(is_safe=False, risk_level="medium"))
    conflicts = detect_conflicts(out, vision, None)
    assert [c for c in conflicts if c["field"] == "brand_safety.risk_level"] == []


# ──────────────────────────────────────────────────────────────────────────
# detect_conflicts — language mismatch
# ──────────────────────────────────────────────────────────────────────────
def test_language_mismatch_conflict():
    """Visible language differs from analyst language -> language conflict."""
    vision = _vision(visible_language="es")
    out = _analyst(language="en")
    conflicts = detect_conflicts(out, vision, None)
    lang = [c for c in conflicts if c["field"] == "language"]
    assert len(lang) == 1
    assert lang[0]["content_analyst"] == "en"
    assert lang[0]["vision_analyst"] == "es"


def test_language_no_conflict_when_visible_language_missing():
    """If vision.visible_language is falsy, no language conflict."""
    vision = _vision(visible_language=None)
    conflicts = detect_conflicts(_analyst(language="en"), vision, None)
    assert [c for c in conflicts if c["field"] == "language"] == []


# ──────────────────────────────────────────────────────────────────────────
# detect_conflicts — draft / vote disputes
# ──────────────────────────────────────────────────────────────────────────
def test_draft_majority_dispute_conflict():
    """tier_1 != draft tier_1 with vote_share >= 0.5 -> majority dispute."""
    draft = {"tier_1": "Gaming", "tier_1_vote_share": 0.6, "tier_votes": {"Gaming": 3, "Automobiles": 2}}
    out = _analyst(tier_1="Automobiles", qc_notes="synth note")
    conflicts = detect_conflicts(out, None, draft)
    disputes = [c for c in conflicts if c["field"] == "tier_1" and "draft_aggregate" in c]
    assert len(disputes) == 1
    c = disputes[0]
    assert c["synthesizer"] == "Automobiles"
    assert "Gaming" in c["draft_aggregate"]
    assert "0.6" in c["draft_aggregate"]
    assert c["synthesizer_notes"] == "synth note"


def test_draft_no_majority_branch_conflict():
    """vote_share < 0.5 -> 'no majority' conflict descriptor."""
    draft = {"tier_1": "Gaming", "tier_1_vote_share": 0.4, "tier_votes": {"Gaming": 2, "Automobiles": 2, "Music": 1}}
    out = _analyst(tier_1="Gaming")  # matches draft, so the majority branch won't fire
    conflicts = detect_conflicts(out, None, draft)
    no_maj = [c for c in conflicts if c.get("issue") == "no majority among sampled-video briefs"]
    assert len(no_maj) == 1
    assert no_maj[0]["votes"] == {"Gaming": 2, "Automobiles": 2, "Music": 1}
    assert no_maj[0]["synthesizer"] == "Gaming"
    # The majority-dispute branch must NOT have fired (tier_1 == draft tier_1).
    assert not [c for c in conflicts if "draft_aggregate" in c]


def test_draft_agreement_no_conflict():
    """Matching tier_1 and a clear majority -> no draft conflict."""
    draft = {"tier_1": "Automobiles", "tier_1_vote_share": 0.8, "tier_votes": {"Automobiles": 4}}
    conflicts = detect_conflicts(_analyst(tier_1="Automobiles"), None, draft)
    assert conflicts == []


def test_draft_missing_vote_share_defaults_no_majority_branch():
    """When tier_1_vote_share is absent it defaults: majority branch uses 0
    (so a mismatch does NOT fire that branch), and the no-majority branch uses
    1.0 (so it also does not fire). Confirms the two distinct defaults."""
    draft = {"tier_1": "Gaming"}  # no vote share at all
    out = _analyst(tier_1="Automobiles")
    conflicts = detect_conflicts(out, None, draft)
    # majority branch needs vote_share >= 0.5; default .get(...,0) -> skip
    assert not [c for c in conflicts if "draft_aggregate" in c]
    # no-majority branch needs vote_share < 0.5; default .get(...,1.0) -> skip
    assert not [c for c in conflicts if c.get("issue")]


def test_multiple_conflicts_accumulate():
    """Vision kids + safety + language + draft majority all stack up."""
    vision = _vision(
        visual_kids_signals={"present": True, "signals": ["toys"]},
        visual_safety_flags=[{"category": "violence", "severity": "high"}],
        visible_language="es",
    )
    draft = {"tier_1": "Gaming", "tier_1_vote_share": 0.7}
    out = _analyst(
        tier_1="Automobiles",
        language="en",
        brand_safety=BrandSafety(is_safe=True, risk_level="none"),
    )
    conflicts = detect_conflicts(out, vision, draft)
    fields = [c["field"] for c in conflicts]
    # two tier_1 conflicts (vision-kids + draft majority), safety, language
    assert fields.count("tier_1") == 2
    assert "brand_safety.risk_level" in fields
    assert "language" in fields


# ──────────────────────────────────────────────────────────────────────────
# adjudicate — applies resolved fields
# ──────────────────────────────────────────────────────────────────────────
def test_adjudicate_applies_resolved_fields_and_rederives_is_safe():
    """Judge resolution updates tier_1/tier_2/language/risk and re-derives
    is_safe; kids_age_group is set; notes appended to qc_notes."""
    resolved = good_judge_output(
        resolved_fields={
            "tier_1": "Kids",
            "tier_2": "Nursery Rhymes",          # judge may send mixed case
            "language": "Spanish",                # truncated+lowered to 'sp'
            "brand_safety.risk_level": "HIGH",    # uppercase -> 'high'
            "kids_age_group": "3-5 years",
        },
        judge_notes="Visual kids signals + nursery rhymes confirm Kids.",
    )
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})

    out = _analyst(
        tier_1="Automobiles", tier_2="motorcycle reviews", language="en",
        kids_age_group=None, qc_notes="initial",
        brand_safety=BrandSafety(is_safe=True, risk_level="none"),
    )
    result = adjudicate(llm, out, conflicts=[{"field": "tier_1"}])

    assert result.tier_1 == "Kids"
    assert result.tier_2 == "nursery rhymes"          # lowercased
    assert result.language == "sp"                    # first 2 chars, lowered
    assert result.brand_safety.risk_level == "high"
    assert result.brand_safety.is_safe is False       # re-derived: high not safe
    assert result.kids_age_group == "3-5 years"
    assert result.qc_notes.startswith("initial | Judge:")
    assert "nursery rhymes confirm Kids" in result.qc_notes
    assert llm.calls == 1


def test_adjudicate_risk_via_bare_risk_level_key():
    """A bare 'risk_level' key (not dotted) is also honored; low -> is_safe True."""
    resolved = good_judge_output(resolved_fields={"risk_level": "low"})
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})
    out = _analyst(brand_safety=BrandSafety(is_safe=False, risk_level="high"))
    result = adjudicate(llm, out, conflicts=[{"field": "brand_safety.risk_level"}])
    assert result.brand_safety.risk_level == "low"
    assert result.brand_safety.is_safe is True


def test_adjudicate_invalid_risk_level_ignored():
    """An out-of-vocab risk_level is rejected, leaving the original intact."""
    resolved = good_judge_output(resolved_fields={"risk_level": "catastrophic"})
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})
    out = _analyst(brand_safety=BrandSafety(is_safe=False, risk_level="medium"))
    result = adjudicate(llm, out, conflicts=[{"field": "brand_safety.risk_level"}])
    assert result.brand_safety.risk_level == "medium"   # unchanged
    assert result.brand_safety.is_safe is False


def test_adjudicate_empty_resolved_fields_only_appends_notes():
    """No resolved_fields -> classification untouched; judge_notes appended."""
    resolved = good_judge_output(resolved_fields={}, judge_notes="Analyst upheld.")
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})
    out = _analyst(tier_1="Automobiles", language="en", qc_notes="")
    result = adjudicate(llm, out, conflicts=[{"field": "tier_1"}])
    assert result.tier_1 == "Automobiles"
    assert result.language == "en"
    assert result.qc_notes == "Judge: Analyst upheld."  # leading ' |' stripped


def test_adjudicate_qc_notes_truncated_to_400_chars():
    """Appended qc_notes is capped at 400 characters."""
    resolved = good_judge_output(resolved_fields={}, judge_notes="x" * 500)
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})
    out = _analyst(qc_notes="seed")
    result = adjudicate(llm, out, conflicts=[{"field": "tier_1"}])
    assert len(result.qc_notes) == 400


def test_adjudicate_sends_judge_system_prompt_and_conflict_report():
    """The user message carries the conflict report + closed tier list; the
    routed system prompt is the JUDGE_SYSTEM (verified via by_system match)."""
    resolved = good_judge_output(resolved_fields={})
    llm = FakeLLMClient(by_system={"final reconciliation judge": resolved})
    conflicts = [{"field": "tier_1", "content_analyst": "Automobiles"}]
    adjudicate(llm, _analyst(), conflicts)
    assert llm.calls == 1
    system, user, images = llm.history[0]
    assert "final reconciliation judge" in system
    assert "CONFLICT REPORT" in user
    assert "CLOSED TIER_1 LIST" in user
    assert "CURRENT RECORD" in user
    assert images is None  # judge is text-only


# ──────────────────────────────────────────────────────────────────────────
# adjudicate — exception isolation
# ──────────────────────────────────────────────────────────────────────────
def test_adjudicate_llm_exception_leaves_output_unchanged():
    """If chat_json raises, the record is returned unmodified (no crash)."""
    def boom(system, user, images_b64):
        raise RuntimeError("ollama down")

    llm = FakeLLMClient(router=boom)
    out = _analyst(
        tier_1="Automobiles", tier_2="motorcycle reviews", language="en",
        qc_notes="original notes",
        brand_safety=BrandSafety(is_safe=True, risk_level="none"),
    )
    result = adjudicate(llm, out, conflicts=[{"field": "tier_1"}])
    assert result is out
    assert result.tier_1 == "Automobiles"
    assert result.tier_2 == "motorcycle reviews"
    assert result.language == "en"
    assert result.qc_notes == "original notes"
    assert result.brand_safety.risk_level == "none"
    assert result.brand_safety.is_safe is True
