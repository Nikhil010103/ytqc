__version__ = "0.2.0"

# Bump whenever any prompt text changes — it is part of the LLM response-cache key,
# so stale cached analyses are never served across prompt revisions.
# 2026-08-17: tier_1 vocabulary replaced with the QC team's 29-value mapping.
# Without this bump an upgraded install would serve cached verdicts carrying the
# retired categories (Movies & Entertainment, Comedy, …) and look un-upgraded.
PROMPT_VERSION = "2026-08-17.1"

# Bump whenever the shape of VideoExtract/ChannelExtract changes — it is part of
# the cross-run extraction-cache key, so a stale-shape bundle is never reused.
# 2026-08-17: ChannelExtract gained the Shorts inventory (tabs, has_shorts_tab,
# long_form_count, shorts_count, is_shorts_only) and ChannelVideoTile.is_short.
# A pre-bump cached channel would decode with is_shorts_only=False and silently
# report Shorts=No for every Shorts-only channel.
EXTRACT_SCHEMA_VERSION = "2026-08-17.1"
