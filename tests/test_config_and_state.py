"""Pinning tests for ytqc.config and ytqc.pipeline.state.

Two modules in one file, clearly separated below. Everything is hermetic:
no network, no browser, no Ollama, no real clock dependence. Filesystem I/O
is confined to pytest ``tmp_path``.

These tests encode the *fixed* behavior from the QA audit so it can't regress.
"""
from __future__ import annotations

import copy
import json
import threading

import pytest

# ──────────────────────────────────────────────────────────────────────────
# PART 1 — ytqc/config.py
# ──────────────────────────────────────────────────────────────────────────
from ytqc.config import (
    DEFAULT_CONFIG,
    PipelineConfig,
    ProviderProfile,
    SamplingConfig,
    YtqcConfig,
    load_config,
    save_config,
)


def _cfg_path(tmp_path):
    """A nested config path inside tmp_path; save_config must mkdir parents."""
    return tmp_path / "ytqc_home" / "config.yaml"


def test_save_load_roundtrip_key_fields(tmp_path):
    """save_config -> load_config preserves active_provider, providers,
    sampling and pipeline exactly."""
    path = _cfg_path(tmp_path)
    cfg = YtqcConfig(
        active_provider="openai",
        providers={
            "openai": ProviderProfile(
                base_url="https://api.openai.com/v1",
                api_key="${OPENAI_API_KEY}",
                model="gpt-4o-mini",
            ),
            "ollama-local": ProviderProfile(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                model="gemma4:latest",
                supports_vision=False,
            ),
        },
        sampling=SamplingConfig(channel_pages=7, comments_top_n=42),
        pipeline=PipelineConfig(analysis_workers=5, review_threshold=0.9, cache_ttl_days=30),
    )

    save_config(cfg, path)
    assert path.exists()  # parents were created

    loaded = load_config(path)

    assert loaded.active_provider == "openai"
    assert set(loaded.providers) == {"openai", "ollama-local"}
    assert loaded.providers["openai"].model == "gpt-4o-mini"
    assert loaded.providers["openai"].api_key == "${OPENAI_API_KEY}"
    assert loaded.providers["ollama-local"].supports_vision is False

    # Whole sub-config equality (pydantic models compare by field values).
    assert loaded.sampling == cfg.sampling
    assert loaded.pipeline == cfg.pipeline
    assert loaded.providers == cfg.providers
    # And the loaded object round-trips to the same dump.
    assert loaded.model_dump() == cfg.model_dump()


def test_resolved_api_key_env_indirection_set(monkeypatch):
    """${MYKEY} resolves from os.environ when MYKEY is set."""
    monkeypatch.setenv("MYKEY", "sk-secret-123")
    prof = ProviderProfile(base_url="http://x", api_key="${MYKEY}", model="m")
    assert prof.resolved_api_key() == "sk-secret-123"


def test_resolved_api_key_env_indirection_unset(monkeypatch):
    """${MYKEY} resolves to '' when MYKEY is not set."""
    monkeypatch.delenv("MYKEY", raising=False)
    prof = ProviderProfile(base_url="http://x", api_key="${MYKEY}", model="m")
    assert prof.resolved_api_key() == ""


def test_resolved_api_key_literal_passthrough():
    """A non-placeholder api_key is returned verbatim (no env lookup)."""
    prof = ProviderProfile(base_url="http://x", api_key="ollama", model="m")
    assert prof.resolved_api_key() == "ollama"


def test_provider_missing_raises_keyerror_listing_available():
    """cfg.provider('missing') raises KeyError naming the available providers."""
    cfg = YtqcConfig(
        active_provider="a",
        providers={
            "a": ProviderProfile(base_url="http://x", api_key="ollama", model="m1"),
            "b": ProviderProfile(base_url="http://y", api_key="ollama", model="m2"),
        },
    )
    with pytest.raises(KeyError) as ei:
        cfg.provider("missing")
    msg = str(ei.value)
    assert "missing" in msg
    # helpful message lists what IS available
    assert "a" in msg and "b" in msg


def test_load_config_missing_path_returns_deepcopy_of_default(tmp_path):
    """load_config on a missing path returns a deep copy of DEFAULT_CONFIG;
    mutating the result must NOT change DEFAULT_CONFIG."""
    missing = tmp_path / "does-not-exist.yaml"
    assert not missing.exists()

    loaded = load_config(missing)

    # Same values as the default.
    assert loaded.model_dump() == DEFAULT_CONFIG.model_dump()
    # But a distinct object graph.
    assert loaded is not DEFAULT_CONFIG
    assert loaded.providers is not DEFAULT_CONFIG.providers

    # Snapshot the default before mutating the copy.
    before = copy.deepcopy(DEFAULT_CONFIG.model_dump())

    loaded.active_provider = "tampered"
    loaded.providers["openai"].model = "tampered-model"
    loaded.providers["brand-new"] = ProviderProfile(
        base_url="http://x", api_key="ollama", model="z"
    )
    loaded.sampling.channel_pages = 999

    after = DEFAULT_CONFIG.model_dump()
    assert after == before, "mutating load_config() result leaked into DEFAULT_CONFIG"
    assert DEFAULT_CONFIG.active_provider != "tampered"
    assert "brand-new" not in DEFAULT_CONFIG.providers
    assert DEFAULT_CONFIG.providers["openai"].model != "tampered-model"


def test_load_config_via_ytqc_home_env(tmp_path, monkeypatch):
    """End-to-end with the documented YTQC_HOME indirection.

    config.CONFIG_DIR/CONFIG_PATH are bound at import time, so we recompute the
    path the same way the module does and feed it explicitly — this pins that
    a YAML written under YTQC_HOME loads back correctly.
    """
    monkeypatch.setenv("YTQC_HOME", str(tmp_path / "home"))
    path = (tmp_path / "home") / "config.yaml"
    cfg = YtqcConfig(active_provider="openai")
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.active_provider == "openai"


# ──────────────────────────────────────────────────────────────────────────
# PART 2 — ytqc/pipeline/state.py
# ──────────────────────────────────────────────────────────────────────────
from ytqc.pipeline.state import RunState


def test_mark_stage_of_is_done_roundtrip(tmp_path):
    st = RunState(str(tmp_path), run_id="run-1")

    assert st.stage_of("vid-1") is None
    assert st.is_done("vid-1") is False

    st.mark("vid-1", "EXTRACTED")
    assert st.stage_of("vid-1") == "EXTRACTED"
    assert st.is_done("vid-1") is False

    st.mark("vid-1", "SUNK")
    assert st.stage_of("vid-1") == "SUNK"
    assert st.is_done("vid-1") is True


def test_mark_persists_across_reopen(tmp_path):
    """A new RunState bound to the same run_id replays the JSONL checkpoint."""
    st = RunState(str(tmp_path), run_id="run-persist")
    st.mark("vid-a", "EXTRACTED")
    st.mark("vid-b", "ANALYZED")
    st.mark("vid-b", "SUNK")

    reopened = RunState(str(tmp_path), run_id="run-persist")
    assert reopened.stage_of("vid-a") == "EXTRACTED"
    # last write wins for vid-b
    assert reopened.stage_of("vid-b") == "SUNK"
    assert reopened.is_done("vid-b") is True


def test_save_load_artifact_roundtrip(tmp_path):
    st = RunState(str(tmp_path), run_id="run-art")
    payload = {"tier_1": "Automobiles", "nested": {"k": [1, 2, 3]}, "unicode": "café"}
    st.save_artifact("vid-1", "extracted.json", payload)
    assert st.load_artifact("vid-1", "extracted.json") == payload


def test_save_load_artifact_item_id_with_slash(tmp_path):
    """An item_id containing '/' must not escape/break the artifacts dir."""
    st = RunState(str(tmp_path), run_id="run-slash")
    item_id = "channel/UC123/video-7"
    payload = {"summary": "ok", "n": 1}

    st.save_artifact(item_id, "analyzed.json", payload)
    assert st.load_artifact(item_id, "analyzed.json") == payload

    # The artifact must live strictly under the artifacts dir (no traversal).
    written = list(st.artifacts.rglob("analyzed.json"))
    assert len(written) == 1
    assert st.artifacts in written[0].parents
    # '/' was flattened, not interpreted as path separators.
    assert "/" not in written[0].relative_to(st.artifacts).parts[0]


def test_mark_with_payload_also_writes_artifact(tmp_path):
    """mark(..., payload=...) records the stage AND drops a <stage>.json artifact."""
    st = RunState(str(tmp_path), run_id="run-payload")
    payload = {"x": 1}
    st.mark("vid-1", "EXTRACTED", payload=payload)
    assert st.stage_of("vid-1") == "EXTRACTED"
    assert st.load_artifact("vid-1", "extracted.json") == payload


def test_malformed_line_skipped_on_reload(tmp_path):
    """A junk line in state.jsonl is skipped; valid stages still load."""
    st = RunState(str(tmp_path), run_id="run-junk")
    st.mark("vid-1", "EXTRACTED")
    st.mark("vid-2", "SUNK")

    # Inject a malformed line plus a valid-JSON-but-missing-keys line.
    with st.state_path.open("a") as f:
        f.write("this is not json\n")
        f.write(json.dumps({"no_item_id": True, "stage": "X"}) + "\n")
        f.write("{ partial json\n")

    reopened = RunState(str(tmp_path), run_id="run-junk")
    # Valid records survive.
    assert reopened.stage_of("vid-1") == "EXTRACTED"
    assert reopened.stage_of("vid-2") == "SUNK"
    assert reopened.is_done("vid-2") is True
    # Junk did not create phantom entries.
    assert "no_item_id" not in reopened._stages
    assert set(reopened._stages) == {"vid-1", "vid-2"}


def test_resume_existing_run(tmp_path):
    st = RunState(str(tmp_path), run_id="run-resume-ok")
    st.mark("vid-1", "SUNK")

    resumed = RunState.resume(str(tmp_path), "run-resume-ok")
    assert resumed.run_id == "run-resume-ok"
    assert resumed.is_done("vid-1") is True


def test_resume_nonexistent_run_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RunState.resume(str(tmp_path), "no-such-run")


def test_thread_safety_concurrent_marks(tmp_path):
    """~20 threads mark distinct ids concurrently. Afterward every id is
    recorded and state.jsonl has zero malformed lines (each line parses as
    JSON with the expected keys)."""
    st = RunState(str(tmp_path), run_id="run-threads")
    n = 20
    ids = [f"vid-{i:02d}" for i in range(n)]
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker(item_id: str):
        try:
            barrier.wait()  # maximize contention
            st.mark(item_id, "SUNK")
        except Exception as e:  # pragma: no cover - surfaced via assert below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker errors: {errors}"

    # Every id was recorded in memory.
    for item_id in ids:
        assert st.stage_of(item_id) == "SUNK"
        assert st.is_done(item_id) is True

    # The JSONL file has exactly n lines, each well-formed (no interleaved
    # / torn writes from the lock).
    lines = st.state_path.read_text().splitlines()
    assert len(lines) == n
    seen = set()
    for line in lines:
        rec = json.loads(line)  # raises if any line is malformed
        assert set(rec) >= {"item_id", "stage", "ts"}
        assert rec["stage"] == "SUNK"
        seen.add(rec["item_id"])
    assert seen == set(ids)

    # A fresh reopen replays all of them cleanly.
    reopened = RunState(str(tmp_path), run_id="run-threads")
    assert set(reopened._stages) == set(ids)
