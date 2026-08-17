"""Closed vocabularies and safety taxonomy.

TIER_1_CATEGORIES, KIDS_AGE_GROUPS, adult bands and the 12-value brand-safety
category list are the QC team's source of truth and must not drift.
"""
from __future__ import annotations

# The QC team's tier-1 mapping (29 values) — kept verbatim, in their order.
# Order is preserved (tuple + set) because the prompt lists them to the model in
# this order; classification drifts if the vocabulary is reworded or re-sorted.
TIER_1_ORDERED = (
    "Home Decor",
    "Business & Finance",
    "Food & Beverage",
    "Home Appliances",
    "Automobile",
    "Agriculture & Farming",
    "Kids",
    "Alcohol",
    "Education",
    "Career & Jobs",
    "Health & Wellness",
    "Movies",
    "Entertainment",
    "Style & Fashion",
    "Fitness",
    "Travel",
    "Science & Technology",
    "Gadgets",
    "Music",
    "Sports",
    "Extreme Sports",
    "Pets & Animals",
    "Gaming",
    "Real Estate",
    "Shopping",
    "Arts & Crafts",
    "Beauty",
    "Motivation",
    "Podcasts",
)

TIER_1_CATEGORIES = set(TIER_1_ORDERED)

# Fallback when no artefact in the input grounds a category — the broadest
# bucket in the vocabulary. Referenced by the prompts, so it must stay valid.
TIER_1_FALLBACK = "Entertainment"

# Near-miss recovery for tier_1. Models are heavily primed toward the common
# YouTube-ish category names (and toward the pre-2026 ytqc vocabulary), so a
# valid-in-spirit answer like "Automobiles" or "Food & Cooking" would otherwise
# fail validation and burn a retry/judge call. Applied ONLY after an exact and a
# case-insensitive match both fail; keys are lowercase.
TIER_1_ALIASES: dict[str, str] = {
    # plural / wording drift within the new vocabulary
    "automobiles": "Automobile",
    "auto": "Automobile",
    "cars": "Automobile",
    "podcast": "Podcasts",
    "pets": "Pets & Animals",
    "animals": "Pets & Animals",
    "movie": "Movies",
    "film": "Movies",
    "films": "Movies",
    "gadget": "Gadgets",
    "art & craft": "Arts & Crafts",
    "arts and crafts": "Arts & Crafts",
    "diy": "Arts & Crafts",
    "real estate & property": "Real Estate",
    "property": "Real Estate",
    "career": "Career & Jobs",
    "careers": "Career & Jobs",
    "jobs": "Career & Jobs",
    "agriculture": "Agriculture & Farming",
    "farming": "Agriculture & Farming",
    "home decor & interiors": "Home Decor",
    "interior design": "Home Decor",
    "appliances": "Home Appliances",
    "finance": "Business & Finance",
    "business": "Business & Finance",
    # pre-2026 ytqc vocabulary → nearest value in the QC team's mapping
    "movies & entertainment": "Entertainment",
    "vlogs": "Entertainment",
    "comedy": "Entertainment",
    "animation": "Entertainment",
    "lifestyle": "Entertainment",
    "news": "Entertainment",
    "global important days": "Entertainment",
    "global festivals": "Entertainment",
    "generational cohorts": "Entertainment",
    "religion": "Entertainment",
    "race & culture": "Entertainment",
    "gender and identity": "Entertainment",
    "rights and democracy": "Entertainment",
    "history": "Education",
    "science": "Science & Technology",
    "technology": "Science & Technology",
    "tech": "Science & Technology",
    "climate and planet": "Science & Technology",
    "food & cooking": "Food & Beverage",
    "food": "Food & Beverage",
    "cooking": "Food & Beverage",
    "beauty & makeup": "Beauty",
    "makeup": "Beauty",
    "fashion": "Style & Fashion",
    "style": "Style & Fashion",
    "health & fitness": "Health & Wellness",
    "health": "Health & Wellness",
    "wellness": "Health & Wellness",
    "mental health": "Health & Wellness",
    "gym": "Fitness",
    "nfl": "Sports",
    "gifting": "Shopping",
    "shopping & deals": "Shopping",
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

# tier_1 categories that are ALWAYS brand-unsafe regardless of the LLM verdict —
# maps tier_1 → (min risk_level to enforce, brand-safety category to record).
#
# Currently EMPTY: the QC team's tier-1 mapping has no News or Religion bucket,
# so there is no tier_1 value to key the policy floor on. News/politics and
# religious content are still floored to brand-unsafe — but via the
# "Political Content" / "Controversial Social Issues" safety categories (a
# prompt rule + the deterministic gate), not via tier_1. The mechanism below is
# kept so a future tier can be floored again by adding one line.
HARDCODED_UNSAFE_TIER1: dict[str, tuple[str, str]] = {}

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
