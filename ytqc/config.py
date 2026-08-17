"""Configuration: ~/.ytqc/config.yaml + CLI flag overrides."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

CONFIG_DIR = Path(os.environ.get("YTQC_HOME", Path.home() / ".ytqc"))
CONFIG_PATH = CONFIG_DIR / "config.yaml"


class ProviderProfile(BaseModel):
    base_url: str
    api_key: str = "ollama"
    model: str
    supports_json_mode: bool = True
    supports_vision: bool = True
    max_images: int = 6
    timeout_s: float = 200.0

    def resolved_api_key(self) -> str:
        # "${ENV_VAR}" indirection so keys never live in the YAML itself
        if self.api_key.startswith("${") and self.api_key.endswith("}"):
            return os.environ.get(self.api_key[2:-1], "")
        return self.api_key


class BrowserConfig(BaseModel):
    kimi_url: str = "http://127.0.0.1:10086/command"
    session: str = "ytqc"               # base; lanes use "{session}-lane{i}"
    nav_sleep_min: float = 2.5
    nav_sleep_max: float = 4.5
    item_sleep_min: float = 2.0
    item_sleep_max: float = 5.0
    coffee_every: int = 25
    coffee_min: float = 30.0
    coffee_max: float = 90.0
    ready_timeout_s: float = 8.0
    # ad-gate (skippable ads can be skipped ~5s in; bumpers waited out to the cap)
    ad_max_wait_s: float = 12.0         # set ~2.0 on a Premium account (no ads)
    ad_poll_s: float = 0.5


class SamplingConfig(BaseModel):
    # channel catalog read: pages of the continuation data API to fetch (~30 titles
    # each → ~120 titles at 4) + grid screenshots to capture for thumbnail vision
    channel_pages: int = 4
    channel_grid_shots: int = 4
    frames_full: int = 5
    frames_lite: int = 2
    transcript_s_min: float = 60.0
    transcript_s_max: float = 120.0
    transcript_pct: float = 0.25
    comments_top_n: int = 10


class PipelineConfig(BaseModel):
    # Browser extraction is CPU/GPU-bound: N Chrome tabs playing video + encoding
    # canvas frames contend super-linearly. Measured sweet spot ~2-4 on a typical
    # dev machine (2 lanes ≈ 1.7x serial; >4 often net-negative). Tune to hardware
    # via --lanes; raise on a beefier box, lower on a constrained one.
    browser_lanes: int = 4              # parallel browser tabs (each its own session)
    # Each worker processes one item and makes its LLM calls SEQUENTIALLY, so
    # concurrent LLM calls ≈ active analysis_workers — this is the real throughput
    # lever (not llm_concurrency). Modest default; raise per-run with --workers.
    analysis_workers: int = 7           # parallel LLM analysis workers
    # OPT-IN: pre-trigger comment lazy-load before transcript+frames so it loads
    # in the background (poll-guarded harvest → never worse, often faster). Off by
    # default; enable after a live smoke-test on your setup.
    overlap_comment_load: bool = False
    # VidIQ overlay scraping — reads the VidIQ browser-extension panel for extra
    # stats (SEO score, channel rank, est. earnings, upload cadence, growth) and
    # generates AI insights from them. On by default; fully graceful no-op when the
    # extension/panel is absent. Disable with --no-vidiq (or here) if not installed.
    vidiq_scrape: bool = True
    vidiq_timeout_s: float = 8.0        # max poll for the async-rendered panel
    llm_concurrency: int = 8            # global burst ceiling; kept ≥ analysis_workers (Ollama-safe)
    review_threshold: float = 0.6
    cache_ttl_days: int = 7
    # cross-run extraction cache — re-QC of the same id skips re-scraping. Stores
    # the full extract, so no fidelity loss; channels get a short TTL (subs /
    # velocity / recent uploads move), videos a long one.
    extract_cache: bool = True
    extraction_ttl_days: int = 14
    extraction_ttl_days_channel: int = 3
    # adaptive bot-hygiene
    max_pages_per_min: int = 30         # global token-bucket ceiling across all lanes
    lane_stagger_s: float = 12.0        # max random per-lane startup stagger
    degrade_on_captcha: bool = True     # circuit breaker reduces lanes on stress signals
    min_lane_count: int = 2             # breaker floor


# Schema version of ~/.ytqc/config.yaml. Bump when an existing user's saved
# config would otherwise silently keep pre-upgrade behaviour, and add a matching
# step to _migrate() below.
#   1 → original
#   2 → the QC deliverable moved to the "qc" sink (qc_output.csv)
CONFIG_VERSION = 2


class YtqcConfig(BaseModel):
    config_version: int = CONFIG_VERSION
    active_provider: str = "ollama-cloud"
    # Chat-agent brain — falls back to the active provider/model. Point these at
    # a fast local model (e.g. ollama-local / gemma4:latest) for snappier chat
    # while the QC pipeline keeps using the heavier active provider.
    agent_provider: Optional[str] = None
    agent_model: Optional[str] = None
    providers: dict[str, ProviderProfile] = Field(default_factory=dict)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    # One deliverable file per run: the QC team's 12-column qc_output.csv. Add
    # "csv"/"xlsx" here (or via --sink) for the wide, every-field debug outputs.
    sinks: list[str] = Field(default_factory=lambda: ["qc"])
    output_dir: str = "./ytqc_runs"

    def provider(self, name: Optional[str] = None) -> ProviderProfile:
        key = name or self.active_provider
        if key not in self.providers:
            raise KeyError(
                f"provider {key!r} not in config (have: {sorted(self.providers)}) — run `ytqc configure`"
            )
        return self.providers[key]


DEFAULT_CONFIG = YtqcConfig(
    active_provider="ollama-cloud",
    providers={
        "ollama-cloud": ProviderProfile(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            model="gemma4:31b-cloud",
        ),
        "ollama-local": ProviderProfile(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
            model="gemma4:latest",
        ),
        "openai": ProviderProfile(
            base_url="https://api.openai.com/v1",
            api_key="${OPENAI_API_KEY}",
            model="gpt-4o-mini",
        ),
        "deepseek": ProviderProfile(
            base_url="https://api.deepseek.com/v1",
            api_key="${DEEPSEEK_API_KEY}",
            model="deepseek-chat",
            supports_vision=False,
        ),
    },
)


def _migrate(data: dict) -> tuple[dict, list[str]]:
    """Bring an older saved config up to CONFIG_VERSION. Returns (data, notes).

    A saved config OVERRIDES the code's defaults, so a field whose default we
    change never reaches anyone who has run `ytqc configure`/`ytqc setup`. Those
    users upgrade, see no difference, and reasonably conclude the upgrade
    failed. Each step here closes one of those gaps.

    In-memory only — load_config never writes. Migrating is idempotent, and
    `ytqc configure --update` persists the result when the user wants it on disk.
    """
    notes: list[str] = []
    # v1 → v2: the QC deliverable is the "qc" sink (qc_output.csv). Older configs
    # pin sinks: [csv, xlsx], which would leave the upgraded user with no QC file.
    if int(data.get("config_version") or 1) < 2:
        sinks = data.get("sinks")
        if isinstance(sinks, list) and not any(str(s).strip().lower() == "qc" for s in sinks):
            data["sinks"] = ["qc"] + list(sinks)
            notes.append("added the 'qc' sink (writes the 12-column qc_output.csv) — "
                         "your existing csv/xlsx outputs are unchanged")
        data["config_version"] = 2
    return data, notes


def load_config(path: Path = CONFIG_PATH) -> YtqcConfig:
    cfg, _notes = load_config_with_notes(path)
    return cfg


def load_config_with_notes(path: Path = CONFIG_PATH) -> tuple[YtqcConfig, list[str]]:
    """load_config plus a human-readable list of what migrating the saved config
    changed, so a CLI entry point can surface it once instead of silently."""
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
        data, notes = _migrate(data)
        return YtqcConfig.model_validate(data), notes
    return DEFAULT_CONFIG.model_copy(deep=True), []


def save_config(cfg: YtqcConfig, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
