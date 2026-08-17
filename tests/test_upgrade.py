"""Upgrade safety: an existing install must actually change behaviour.

Three separate things silently defeat an upgrade, and all three are invisible
(the tool runs fine, it just behaves like the old version):
  * a SAVED config overrides a changed default → no qc_output.csv
  * the LLM response cache is keyed on PROMPT_VERSION → old-taxonomy verdicts
  * the extraction cache is keyed on EXTRACT_SCHEMA_VERSION → old bundle shape
"""
from __future__ import annotations

import yaml

from ytqc import EXTRACT_SCHEMA_VERSION, PROMPT_VERSION, __version__
from ytqc.config import CONFIG_VERSION, _migrate, load_config_with_notes, save_config


# ── saved-config migration ────────────────────────────────────────────────
def test_pre_v2_config_gains_the_qc_sink():
    """The exact shape every existing user has on disk today."""
    data, notes = _migrate({"sinks": ["csv", "xlsx"]})
    assert data["sinks"] == ["qc", "csv", "xlsx"]     # qc first; nothing removed
    assert data["config_version"] == 2
    assert notes and "qc" in notes[0]


def test_migration_is_idempotent():
    once, _ = _migrate({"sinks": ["csv"]})
    twice, notes = _migrate(dict(once))
    assert twice["sinks"] == once["sinks"]
    assert notes == []                                # nothing more to say


def test_migration_leaves_a_current_config_alone():
    data, notes = _migrate({"config_version": CONFIG_VERSION, "sinks": ["csv"]})
    assert data["sinks"] == ["csv"]                   # a deliberate choice is respected
    assert notes == []


def test_migration_tolerates_a_config_with_no_sinks_key():
    data, notes = _migrate({"active_provider": "ollama-cloud"})
    assert "sinks" not in data                        # model default (["qc"]) applies
    assert data["config_version"] == 2 and notes == []


def test_loading_an_old_config_does_not_write_to_disk(tmp_path):
    """Migration is in-memory: loading a config must never mutate the user's
    file behind their back (every CLI command loads it, including in tests)."""
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"sinks": ["csv", "xlsx"]}))
    before = p.read_text()

    cfg, notes = load_config_with_notes(p)
    assert cfg.sinks == ["qc", "csv", "xlsx"]         # correct behaviour immediately
    assert notes
    assert p.read_text() == before                    # …but the file is untouched


def test_configure_update_persists_the_migration(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"sinks": ["csv", "xlsx"]}))
    cfg, _ = load_config_with_notes(p)
    save_config(cfg, p)

    reloaded, notes = load_config_with_notes(p)
    assert reloaded.sinks == ["qc", "csv", "xlsx"]
    assert reloaded.config_version == CONFIG_VERSION
    assert notes == []                                # already migrated on disk


def test_a_fresh_config_needs_no_migration(tmp_path):
    from ytqc.config import DEFAULT_CONFIG
    p = tmp_path / "config.yaml"
    save_config(DEFAULT_CONFIG, p)
    cfg, notes = load_config_with_notes(p)
    assert notes == [] and cfg.sinks == ["qc"]


# ── cache-invalidating version bumps ──────────────────────────────────────
# These pin the CURRENT values. If you change a prompt or an extract model and
# these still pass, you forgot the bump and upgraded users get stale results.
def test_prompt_version_covers_the_new_taxonomy():
    assert PROMPT_VERSION == "2026-08-17.1"


def test_extract_schema_version_covers_the_shorts_fields():
    assert EXTRACT_SCHEMA_VERSION == "2026-08-17.1"


def test_package_version_bumped_so_pipx_upgrade_reinstalls():
    """pip skips a same-version VCS reinstall, so a stale version number is
    enough to make `pipx upgrade` a silent no-op."""
    assert __version__ == "0.2.0"


def test_prompt_version_actually_keys_the_llm_cache():
    from ytqc.llm.cache import ResponseCache
    k1 = ResponseCache.make_key("ollama", "m", "sys", "user", None)
    import ytqc.llm.cache as cache_mod
    original = cache_mod.PROMPT_VERSION
    try:
        cache_mod.PROMPT_VERSION = "different"
        k2 = ResponseCache.make_key("ollama", "m", "sys", "user", None)
    finally:
        cache_mod.PROMPT_VERSION = original
    assert k1 != k2, "a PROMPT_VERSION bump must invalidate cached analyses"


def test_extract_schema_version_actually_keys_the_extract_cache():
    from ytqc.browser.extract_cache import ExtractCache
    k1 = ExtractCache.make_key("UC1", "channel")
    import ytqc.browser.extract_cache as ec
    original = ec.EXTRACT_SCHEMA_VERSION
    try:
        ec.EXTRACT_SCHEMA_VERSION = "different"
        k2 = ExtractCache.make_key("UC1", "channel")
    finally:
        ec.EXTRACT_SCHEMA_VERSION = original
    assert k1 != k2, "an EXTRACT_SCHEMA_VERSION bump must invalidate cached extracts"
