__version__ = "0.1.0"

# Bump whenever any prompt text changes — it is part of the LLM response-cache key,
# so stale cached analyses are never served across prompt revisions.
PROMPT_VERSION = "2026-06-24.2"

# Bump whenever the shape of VideoExtract/ChannelExtract changes — it is part of
# the cross-run extraction-cache key, so a stale-shape bundle is never reused.
EXTRACT_SCHEMA_VERSION = "2026-06-24.2"
