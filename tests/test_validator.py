import pytest

from ytqc.agents.validator import (ValidationError, compute_confidence,
                                   derive_brand_unsafe_category, normalize)
from ytqc.models import BrandSafety


def base_result(**over):
    r = {
        "tier_1": "Gaming",
        "tier_2": "Esports Highlights",
        "tier_classification_reasoning": "Tag 'esports' → Gaming." + "x" * 300,
        "keywords": ["Esports", " GAMING ", "", "a", "b", "c", "d", "e", "f", "g"],
        "language": "English",
        "targeted_region": "India",
        "brand_safety": {"risk_level": "none", "triggered_categories": [], "explanation": "clean"},
        "targeted_audience": {"age_group": "18-24", "gender": "MALE", "interests": ["esports fans"]},
        "kids_age_group": None,
        "sentiment": "positive",
        "is_premium_luxury": False,
    }
    r.update(over)
    return r


def test_normalize_happy_path():
    out = normalize(base_result(), "vid1")
    assert out.tier_1 == "Gaming"
    assert out.tier_2 == "esports highlights"
    assert len(out.tier_classification_reasoning) <= 200
    assert len(out.keywords) <= 8 and all(k == k.lower() for k in out.keywords)
    assert out.language == "en"          # clamped to ISO 639-1
    assert out.targeted_audience.gender == "male"


def test_tier1_case_insensitive_recovery():
    out = normalize(base_result(tier_1="gaming"), "vid1")
    assert out.tier_1 == "Gaming"


def test_tier1_unrecoverable_raises():
    with pytest.raises(ValidationError):
        normalize(base_result(tier_1="Videogames"), "vid1")


def test_xor_kids_nulls_adult_band():
    out = normalize(base_result(
        tier_1="Kids", kids_age_group="3-5 years",
        targeted_audience={"age_group": "18-24", "gender": "any", "interests": []},
    ), "vid1")
    assert out.kids_age_group == "3-5 years"
    assert out.targeted_audience.age_group is None


def test_xor_non_kids_nulls_kids_band():
    out = normalize(base_result(kids_age_group="3-5 years"), "vid1")
    assert out.kids_age_group is None
    assert out.targeted_audience.age_group == "18-24"


def test_invalid_kids_band_dropped():
    out = normalize(base_result(tier_1="Kids", kids_age_group="toddlers"), "vid1")
    assert out.kids_age_group is None      # logged, not invented


def test_is_safe_derived_not_trusted():
    out = normalize(base_result(brand_safety={
        "is_safe": True, "risk_level": "high",
        "triggered_categories": ["Gambling / betting"], "explanation": "casino promo",
    }), "vid1")
    assert out.brand_safety.is_safe is False
    assert "Gambling" in out.brand_safety.triggered_categories


def test_unparseable_risk_is_conservative():
    out = normalize(base_result(brand_safety={"risk_level": "extreme"}), "vid1")
    assert out.brand_safety.risk_level == "medium"
    assert out.brand_safety.is_safe is False


def test_news_tier1_forced_brand_unsafe():
    # News is always brand-unsafe by policy even when the LLM claims it's clean.
    out = normalize(base_result(
        tier_1="News", tier_2="breaking news",
        brand_safety={"risk_level": "none", "triggered_categories": [], "explanation": "clean"},
    ), "vid1")
    assert out.brand_safety.risk_level == "medium"
    assert out.brand_safety.is_safe is False
    assert "Political Content" in out.brand_safety.triggered_categories


def test_religion_tier1_forced_brand_unsafe():
    out = normalize(base_result(
        tier_1="Religion", tier_2="sermons",
        brand_safety={"risk_level": "none", "triggered_categories": [], "explanation": "clean"},
    ), "vid1")
    assert out.brand_safety.risk_level == "medium"
    assert out.brand_safety.is_safe is False
    assert "Controversial Social Issues" in out.brand_safety.triggered_categories


def test_policy_floor_never_lowers_higher_llm_risk():
    # The floor may only RAISE risk — a News video the LLM flagged 'high' stays high.
    out = normalize(base_result(
        tier_1="News",
        brand_safety={"risk_level": "high", "triggered_categories": ["Hate Speech"],
                      "explanation": "slur in title"},
    ), "vid1")
    assert out.brand_safety.risk_level == "high"
    assert "Political Content" in out.brand_safety.triggered_categories
    assert "Hate Speech" in out.brand_safety.triggered_categories


def test_non_policy_tier1_not_floored():
    out = normalize(base_result(tier_1="Gaming"), "vid1")
    assert out.brand_safety.risk_level == "none"
    assert out.brand_safety.is_safe is True


def test_lookalike_keywords_normalized():
    out = normalize(base_result(
        lookalike_keywords=["World News", " GEOPOLITICS ", "", "a", "b", "c", "d", "e", "f", "g"],
    ), "vid1")
    assert out.lookalike_keywords[:2] == ["world news", "geopolitics"]
    assert len(out.lookalike_keywords) <= 8
    assert all(k == k.lower() for k in out.lookalike_keywords)


def test_brand_unsafe_category_priority():
    bs = BrandSafety(is_safe=False, risk_level="high",
                     triggered_categories=["Gambling", "Adult Content"])
    assert derive_brand_unsafe_category(bs) == "Adult Content"
    assert derive_brand_unsafe_category(BrandSafety()) is None


def test_confidence_penalties_stack():
    assert compute_confidence(transcript_source="panel", vision_ok=True,
                              tier_recovered=False, judge_invoked=False) == 1.0
    low = compute_confidence(transcript_source="none", vision_ok=False,
                             tier_recovered=True, judge_invoked=True,
                             vote_unanimous=False, language_consistent=False)
    assert low == pytest.approx(0.25)
