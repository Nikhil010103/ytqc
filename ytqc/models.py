"""Pydantic data models flowing through the pipeline."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class InputItem(BaseModel):
    id: str
    type: Literal["channel", "video"]
    label: Optional[str] = None          # optional expectation column from input file


class TranscriptResult(BaseModel):
    source: Literal["panel", "none"] = "none"
    track_kind: Optional[str] = None     # 'manual' | 'asr' from captionTracks (provenance)
    track_lang: Optional[str] = None
    segments: list[dict] = Field(default_factory=list)   # [{start_s, text}]
    excerpt_block: str = ""              # formatted prompt block from sampling


class FrameSet(BaseModel):
    thumbnail_b64: Optional[str] = None
    frames_b64: list[str] = Field(default_factory=list)
    frame_timestamps: list[float] = Field(default_factory=list)
    method: Literal["canvas", "screenshot", "none"] = "none"


class CommentData(BaseModel):
    count: int = 0                       # parsed numeric comment count
    count_text: str = ""                 # raw header text (provenance, e.g. "3,496 Comments")
    top_comments: list[dict] = Field(default_factory=list)  # [{author, text, likes}]


class VidIQStats(BaseModel):
    """Raw stats scraped from the VidIQ browser-extension overlay. Everything is
    kept verbatim (as the panel rendered it, K/M/B abbreviations and all) — never
    coerced. ok=False means the panel was absent/failed and this is fully ignorable."""
    ok: bool = False
    present: bool = False                 # overlay panel detected + populated in DOM
    scope: Literal["video", "channel"] = "video"
    # --- video-page panel (#video-companion-root, Overview tab) ---
    subscribers_text: str = ""           # channel card "Subs", e.g. "4.51M"
    total_views_text: str = ""           # channel card "Views", e.g. "2.5B"
    video_count_text: str = ""           # e.g. "427"
    channel_age_text: str = ""           # e.g. "11 years old"
    seo_score_text: str = ""             # vidIQ SEO score out of 100, e.g. "93.7"
    controversial_keywords: list[str] = Field(default_factory=list)  # only if unlocked (paid)
    controversial_locked: bool = False   # section present but Boost-gated (free plan)
    # --- channel-page panel (#page-header "Quick channel stats") ---
    subscribers_growth_text: str = ""    # e.g. "+0.22%"
    views_gained_7d_text: str = ""       # e.g. "+15,333,011"
    rank_text: str = ""                  # vidIQ channel rank, e.g. "#9.7k"
    est_monthly_earnings_text: str = ""  # e.g. "US$13.3k"
    avg_video_length_text: str = ""      # e.g. "6.3 minutes"
    upload_frequency_text: str = ""      # e.g. "~1 uploads per week"
    similar_channels: list[str] = Field(default_factory=list)  # ["Macha", ...]
    # full verbatim panel text (provenance + fed to the insight agent; kept off QCRecord)
    raw_text: str = ""
    error: Optional[str] = None


class VideoExtract(BaseModel):
    video_id: str
    ok: bool = True
    title: str = ""
    author: str = ""
    channel_id: str = ""
    duration_s: float = 0
    view_count: int = 0
    likes: int = 0
    keywords: list[str] = Field(default_factory=list)
    description: str = ""
    youtube_category: str = ""
    publish_date: str = ""
    is_family_safe: Optional[bool] = None
    is_live: bool = False
    days_since_publish: float = 365.0
    views_per_day: float = 0
    transcript: TranscriptResult = Field(default_factory=TranscriptResult)
    frames: FrameSet = Field(default_factory=FrameSet)
    comments: CommentData = Field(default_factory=CommentData)
    vidiq: VidIQStats = Field(default_factory=VidIQStats)
    provenance: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None


class ChannelVideoTile(BaseModel):
    video_id: str
    title: str = ""
    views: int = 0
    days_ago: float = 365.0
    length_text: str = ""


class ChannelExtract(BaseModel):
    channel_id: str
    ok: bool = True
    title: str = ""
    external_id: str = ""                # UC... id
    description: str = ""
    subscribers: int = 0
    total_views: int = 0
    video_count: int = 0
    country: str = ""
    joined_date: str = ""
    links: list[dict] = Field(default_factory=list)
    channel_keywords: str = ""
    is_family_safe: Optional[bool] = None
    recent_videos: list[ChannelVideoTile] = Field(default_factory=list)
    avg_views_last5: float = 0
    avg_views_prev5: float = 0
    velocity_score: float = 0
    home_screenshot_b64: Optional[str] = None
    grid_screenshots_b64: list[str] = Field(default_factory=list)  # /videos grid (thumbnails) for vision
    vidiq: VidIQStats = Field(default_factory=VidIQStats)
    provenance: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None


class VisionEvidence(BaseModel):
    ok: bool = True
    frames: list[dict] = Field(default_factory=list)      # [{position, description}]
    on_screen_text: list[str] = Field(default_factory=list)
    content_format: str = "other"
    production_quality: str = "amateur"
    visual_kids_signals: dict = Field(default_factory=lambda: {"present": False, "signals": []})
    visual_safety_flags: list[dict] = Field(default_factory=list)
    people: dict = Field(default_factory=dict)
    brands_or_products_visible: list[str] = Field(default_factory=list)
    premium_luxury_signals: list[str] = Field(default_factory=list)
    visible_language: Optional[str] = None
    error: Optional[str] = None


class BrandSafety(BaseModel):
    is_safe: bool = True
    risk_level: str = "none"
    triggered_categories: list[str] = Field(default_factory=list)
    explanation: str = ""


class TargetedAudience(BaseModel):
    age_group: Optional[str] = None
    gender: str = "any"
    interests: list[str] = Field(default_factory=list)


class AnalystOutput(BaseModel):
    """Raw (pre-validation) output of the Content Analyst / Channel Synthesizer."""
    summary: str = ""
    hook: str = ""
    content_themes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    comment_sentiment: dict = Field(default_factory=dict)
    primary_audience: str = ""
    target_industries: list[str] = Field(default_factory=list)
    brand_safety: BrandSafety = Field(default_factory=BrandSafety)
    tier_1: Optional[str] = None
    tier_2: Optional[str] = None
    tier_classification_reasoning: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    language: Optional[str] = None
    targeted_region: Optional[str] = None
    kids_age_group: Optional[str] = None
    targeted_audience: TargetedAudience = Field(default_factory=TargetedAudience)
    suitable_age_group: Optional[str] = None
    is_premium_luxury: bool = False
    qc_notes: str = ""


class QCRecord(BaseModel):
    """The final, validated record written to sinks — the Mirrors QC schema."""
    id: str
    type: str
    name: str = ""
    status: str = "OK"                   # OK | ERROR
    error: str = ""
    # classification
    tier_1: Optional[str] = None
    tier_2: Optional[str] = None
    tier_classification_reasoning: Optional[str] = None
    brand_safety_is_safe: Optional[bool] = None
    brand_safety_risk_level: str = "none"
    brand_safety_triggered_categories: list[str] = Field(default_factory=list)
    brand_safety_explanation: str = ""
    brand_unsafe_category: Optional[str] = None
    language: Optional[str] = None
    targeted_region: Optional[str] = None
    kids_age_group: Optional[str] = None
    audience_age_group: Optional[str] = None
    audience_gender: str = "any"
    audience_interests: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    content_themes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    summary: str = ""
    suitable_age_group: Optional[str] = None
    is_premium_luxury: bool = False
    comment: str = ""                    # QC notes
    # stats (deterministic, extraction-owned)
    subscribers: int = 0
    total_views: int = 0
    video_count: int = 0
    views_per_day: float = 0
    velocity_score: float = 0
    likes: int = 0
    comments_count: int = 0
    publish_or_join_date: str = ""
    country: str = ""
    duration_s: float = 0
    # vidiq overlay stats (verbatim pass-through) + AI insight
    vidiq_subscribers: str = ""
    vidiq_total_views: str = ""
    vidiq_video_count: str = ""
    vidiq_channel_age: str = ""
    vidiq_seo_score: str = ""
    vidiq_subscribers_growth: str = ""
    vidiq_views_gained_7d: str = ""
    vidiq_rank: str = ""
    vidiq_est_monthly_earnings: str = ""
    vidiq_avg_video_length: str = ""
    vidiq_upload_frequency: str = ""
    vidiq_similar_channels: list[str] = Field(default_factory=list)
    vidiq_controversial_keywords: list[str] = Field(default_factory=list)
    vidiq_controversial_locked: bool = False
    vidiq_insight: str = ""              # AI-generated, user-understandable summary
    vidiq_signals: list[str] = Field(default_factory=list)  # structured signal bullets
    # meta
    transcript_source: str = "none"
    confidence: float = 1.0
    needs_review: bool = False
    judge_invoked: bool = False
    provider: str = ""
    model: str = ""
    run_id: str = ""
    analyzed_at: str = ""

    def to_flat_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        for k, v in d.items():
            if isinstance(v, list):
                v = "; ".join(str(x) for x in v)
                d[k] = v
            # CSV/spreadsheet formula injection guard: prefix risky leading
            # chars with a single quote so Excel/Calc treat them as text.
            if isinstance(v, str) and v and v[0] in "=+@-":
                d[k] = "'" + v
        return d
