"""Closed vocabularies and safety taxonomy.

TIER_1_CATEGORIES, KIDS_AGE_GROUPS, adult bands and the 12-value brand-safety
category list are the QC team's source of truth and must not drift.
"""
from __future__ import annotations

TIER_1_CATEGORIES = {
    "Alcohol", "Lifestyle", "News", "Travel", "Technology", "Sports",
    "Education", "Gaming", "Health & Fitness", "Fashion", "Music", "Vlogs",
    "Comedy", "Beauty & Makeup", "Food & Cooking", "Science", "History",
    "Movies & Entertainment", "Animation", "Kids", "NFL", "Podcast",
    "Global Important Days", "Global Festivals", "Automobiles", "Pets",
    "Business & Finance", "Gifting", "Climate And Planet", "Race & Culture",
    "Gender And Identity", "Rights And Democracy", "Mental Health",
    "Generational Cohorts", "Religion",
}

KIDS_AGE_GROUPS = ("0-2 years", "3-5 years", "6-8 years", "9-12 years", "Teens")

ADULT_AGE_BANDS = ("13-17", "18-24", "25-34", "35-44", "45-54", "55+", "general adult")

GENDER_VALUES = {"male", "female", "mixed", "any"}

RISK_LEVELS = ("none", "low", "medium", "high")

# The 12 brand-safety categories used across channel/video analysis.
SAFETY_CATEGORIES = (
    "Adult Content", "Violent Content", "Hate Speech",
    "Profanity & Offensive Language", "Drugs & Tobacco", "Alcohol",
    "Gambling", "Political Content", "Misinformation",
    "Controversial Social Issues", "Dangerous Activities",
    "Sensational & Shocking Content",
)

# tier_1 categories that are ALWAYS brand-unsafe regardless of the LLM verdict.
# News/politics and religious content are sensitive placements most advertisers
# exclude by policy, so the validator floors their risk deterministically. Maps
# each such tier_1 → (min risk_level to enforce, brand-safety category to record).
HARDCODED_UNSAFE_TIER1: dict[str, tuple[str, str]] = {
    "News": ("medium", "Political Content"),
    "Religion": ("medium", "Controversial Social Issues"),
}

# The video-analysis prompt (lifted verbatim) emits its own bolded trigger
# labels; the validator normalizes them onto SAFETY_CATEGORIES via this map.
PROMPT_TRIGGER_TO_CATEGORY = {
    "sexual / nudity": "Adult Content",
    "vulgarity / profanity": "Profanity & Offensive Language",
    "gambling / betting": "Gambling",
    "violence / weapons": "Violent Content",
    "illegal substances": "Drugs & Tobacco",
    "hate / extremism": "Hate Speech",
    "self-harm / dangerous acts": "Dangerous Activities",
    "misinformation": "Misinformation",
}

# ── Deterministic safety pre-gate term lists ─────────────────────────────
# Deterministic unsafe / campaign-sensitive term groups feeding the safety pre-gate.
# Scanned (case-insensitive, word-boundary) over title+description+tags+transcript.
UNSAFE_TERM_GROUPS: dict[str, tuple[str, ...]] = {
    "alcohol": ("alcohol", "beer", "wine", "whiskey", "vodka", "liquor", "brewery", "spirits"),
    "gambling": ("gambling", "casino", "betting", "sportsbook", "poker", "lottery",
                 "jackpot", "free spins", "satta"),
    "adult": ("onlyfans", "nsfw", "porn", "xxx", "stripper", "thirst trap", "fetish"),
    "violence": ("gore", "graphic violence", "beheading", "massacre", "brutal fight"),
    "weapons": ("firearms", "gun sale", "ammo", "explosives", "how to make a bomb"),
    "drugs": ("cocaine", "heroin", "meth", "cannabis", "marijuana", "weed", "ganja",
              "drug deal", "vape juice"),
    "hate": ("hate speech", "ethnic cleansing", "white power", "kill all"),
    "self_harm": ("suicide method", "self harm", "pro ana", "kill myself"),
}

# group → (brand_unsafe_category label, minimum risk_level the post-gate enforces)
UNSAFE_GROUP_TO_CATEGORY: dict[str, tuple[str, str]] = {
    "alcohol": ("Alcohol", "low"),
    "gambling": ("Gambling", "medium"),
    "adult": ("Adult Content", "high"),
    "violence": ("Violent Content", "medium"),
    "weapons": ("Violent Content", "medium"),
    "drugs": ("Drugs & Tobacco", "medium"),
    "hate": ("Hate Speech", "high"),
    "self_harm": ("Dangerous Activities", "high"),
}


def risk_at_least(level: str, floor: str) -> str:
    """Return the higher of two risk levels."""
    order = {lvl: i for i, lvl in enumerate(RISK_LEVELS)}
    return level if order.get(level, 0) >= order.get(floor, 0) else floor
