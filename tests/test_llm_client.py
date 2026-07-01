"""Hermetic tests for ytqc.llm.client.LLMClient.chat_json control flow.

Pins the QA-hardened behaviour: independent parse vs network retry budgets,
temperature escalation 0.1->0.3->0.6, 429 backoff, terminal HTTP/auth fast-fail
(no sleep), httpx timeout treated as a retryable network error, the vision
capability gate, the max_images + payload-size guards, and the cache
short-circuit.

NO network, NO real OpenAI: we build a real LLMClient with a minimal
ProviderProfile, then monkeypatch ``llm._client.chat.completions.create`` to a
fake. ``time.sleep`` is patched to a recorder so retries never wait for real.
``parse_llm_json`` itself is covered in test_llm_parsing.py and not re-tested here.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
)

from ytqc.config import ProviderProfile
from ytqc.llm import client as client_mod
from ytqc.llm.client import (
    MAX_IMAGE_B64_CHARS,
    MAX_TOTAL_B64_CHARS,
    TEMP_LADDER,
    LLMClient,
    ProviderCapabilityError,
)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _make_profile(**ov):
    base = dict(
        base_url="http://localhost:11434/v1",
        api_key="unused",
        model="fake-model",
        supports_json_mode=True,
        supports_vision=True,
        max_images=6,
        timeout_s=200.0,
    )
    base.update(ov)
    return ProviderProfile(**base)


def _make_client(monkeypatch, profile=None, cache=None):
    """Build a real LLMClient (constructs a real OpenAI() object — no network
    until .create is hit), then guarantee .create is never the real thing by
    leaving it for each test to monkeypatch explicitly."""
    llm = LLMClient(profile or _make_profile(), provider_name="fake", cache=cache)
    return llm


def _resp(content: str):
    """A stand-in for the OpenAI ChatCompletion response object: only the
    attribute chain the client touches (resp.choices[0].message.content)."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _CreateStub:
    """Replacement for client.chat.completions.create.

    ``script`` is a list of either:
      * a str  -> returned as the message content for that call
      * an Exception instance -> raised on that call
    Records every kwargs dict passed in ``.kwargs_log``.
    """

    def __init__(self, script):
        self._script = list(script)
        self.kwargs_log: list[dict] = []
        self.call_count = 0

    def __call__(self, **kwargs):
        self.kwargs_log.append(kwargs)
        self.call_count += 1
        if not self._script:
            raise AssertionError("_CreateStub: script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _resp(item)

    @property
    def temps(self) -> list[float]:
        return [kw["temperature"] for kw in self.kwargs_log]


def _install_create(monkeypatch, llm, stub):
    monkeypatch.setattr(llm._client.chat.completions, "create", stub)


class _SleepRecorder:
    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, secs):
        self.waits.append(secs)


@pytest.fixture
def sleeps(monkeypatch):
    rec = _SleepRecorder()
    monkeypatch.setattr(client_mod.time, "sleep", rec)
    return rec


# Lightweight openai-typed terminal errors. The real SDK __init__ wants a
# response/body; the client only branches on the *type*, so a light subclass
# that records a message is faithful and hermetic.
class _FakeAuthError(AuthenticationError):
    def __init__(self, msg="invalid api key"):
        Exception.__init__(self, msg)


class _FakeNotFound(NotFoundError):
    def __init__(self, msg="model not found"):
        Exception.__init__(self, msg)


class _FakePermissionDenied(PermissionDeniedError):
    def __init__(self, msg="forbidden"):
        Exception.__init__(self, msg)


def _status_error(status: int, msg: str | None = None):
    """A generic provider error carrying a .status_code attribute, exercising
    the client's getattr(exc, 'status_code', None) branch."""
    exc = RuntimeError(msg or f"HTTP {status}")
    exc.status_code = status
    return exc


# ──────────────────────────────────────────────────────────────────────────
# Temperature escalation on parse failure
# ──────────────────────────────────────────────────────────────────────────
def test_temperature_escalates_0_1_to_0_3_to_0_6_on_parse_failure(monkeypatch, sleeps):
    llm = _make_client(monkeypatch)
    # two un-parseable bodies, then valid JSON
    stub = _CreateStub(["this is not json", "still not json", '{"tier_1": "Automobiles"}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"tier_1": "Automobiles"}
    # Temperatures climbed across the ladder, one rung per parse failure.
    assert stub.temps == [0.1, 0.3, 0.6]
    assert list(TEMP_LADDER) == [0.1, 0.3, 0.6]
    # No network errors occurred -> never slept.
    assert sleeps.waits == []
    # .calls is incremented after each *successful* create() but before parse
    # validation (it tracks raw API spend), so the two parse-failed rounds still
    # count: 2 failures + 1 success == 3.
    assert llm.calls == 3


def test_temperature_clamps_at_top_of_ladder_when_parse_keeps_failing(monkeypatch, sleeps):
    # 4 parse failures then success; ladder has only 3 rungs so it clamps at 0.6.
    llm = _make_client(monkeypatch)
    stub = _CreateStub(["nope", "nope", "nope", "nope", '{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"ok": 1}
    assert stub.temps == [0.1, 0.3, 0.6, 0.6, 0.6]
    assert sleeps.waits == []


def test_escalate_false_pins_single_temperature(monkeypatch, sleeps):
    llm = _make_client(monkeypatch)
    stub = _CreateStub(['{"ok": true}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user", temperature=0.3, escalate=False)

    assert out == {"ok": True}
    assert stub.temps == [0.3]


def test_parse_budget_exhausted_raises_runtimeerror(monkeypatch, sleeps):
    # Every body unparseable; parse budget = max_attempts default 5.
    llm = _make_client(monkeypatch)
    stub = _CreateStub(["x"] * 5)
    _install_create(monkeypatch, llm, stub)

    with pytest.raises(RuntimeError):
        llm.chat_json("sys", "user")

    # 5 parse attempts, all consumed; no network sleeps.
    assert stub.call_count == 5
    assert sleeps.waits == []


# ──────────────────────────────────────────────────────────────────────────
# 429 backoff: uses the NETWORK budget, does not consume parse retries
# ──────────────────────────────────────────────────────────────────────────
def test_429_backoff_uses_network_budget_and_preserves_parse_budget(monkeypatch, sleeps):
    llm = _make_client(monkeypatch)
    # two 429s then a clean parse
    stub = _CreateStub([
        _status_error(429, "Too Many Requests 429"),
        _status_error(429, "Too Many Requests 429"),
        '{"tier_1": "Automobiles"}',
    ])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"tier_1": "Automobiles"}
    # Backed off twice with the 429 schedule: 5*2**0=5, then 5*2**1=10.
    assert sleeps.waits == [5.0, 10.0]
    # Parse retries were untouched: every create() ran at the base temperature
    # because temp_idx (the parse ladder pointer) never advanced.
    assert stub.temps == [0.1, 0.1, 0.1]
    assert stub.call_count == 3
    assert llm.calls == 1


def test_429_detected_via_message_string_when_no_status_code(monkeypatch, sleeps):
    # An exception with no status_code but "429" in its text still backs off.
    llm = _make_client(monkeypatch)
    err = RuntimeError("rate limited: 429 slow down")
    stub = _CreateStub([err, '{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"ok": 1}
    assert sleeps.waits == [5.0]


def test_429_storm_cannot_starve_parse_budget(monkeypatch, sleeps):
    # A flood of 429s exhausts the NETWORK budget and fails — but the parse
    # ladder pointer never moved, proving the budgets are independent.
    llm = _make_client(monkeypatch)
    stub = _CreateStub([_status_error(429) for _ in range(5)])
    _install_create(monkeypatch, llm, stub)

    with pytest.raises(RuntimeError):
        llm.chat_json("sys", "user")

    # network_budget == 5 (default max_attempts) -> 5 create calls, 5 backoffs.
    assert stub.call_count == 5
    assert len(sleeps.waits) == 5
    # Every attempt at base temperature: parse ladder untouched.
    assert stub.temps == [0.1] * 5


# ──────────────────────────────────────────────────────────────────────────
# Terminal errors fast-fail with NO sleep
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("exc_factory", [
    _FakeAuthError,
    _FakeNotFound,
    _FakePermissionDenied,
])
def test_terminal_openai_typed_error_fast_fails_without_sleep(monkeypatch, sleeps, exc_factory):
    llm = _make_client(monkeypatch)
    stub = _CreateStub([exc_factory()])
    _install_create(monkeypatch, llm, stub)

    with pytest.raises(RuntimeError) as ei:
        llm.chat_json("sys", "user")

    assert "terminal" in str(ei.value).lower()
    # Failed on the first create with no retry and no backoff.
    assert stub.call_count == 1
    assert sleeps.waits == []


@pytest.mark.parametrize("status", [401, 403, 404])
def test_terminal_http_status_fast_fails_without_sleep(monkeypatch, sleeps, status):
    llm = _make_client(monkeypatch)
    stub = _CreateStub([_status_error(status)])
    _install_create(monkeypatch, llm, stub)

    with pytest.raises(RuntimeError) as ei:
        llm.chat_json("sys", "user")

    assert f"HTTP {status}" in str(ei.value)
    assert stub.call_count == 1
    assert sleeps.waits == []


# ──────────────────────────────────────────────────────────────────────────
# httpx timeout -> retryable NETWORK error with backoff (not a parse failure)
# ──────────────────────────────────────────────────────────────────────────
def test_httpx_timeout_is_retryable_network_error_with_backoff(monkeypatch, sleeps):
    llm = _make_client(monkeypatch)
    stub = _CreateStub([
        httpx.TimeoutException("read timeout"),
        httpx.TimeoutException("read timeout"),
        '{"ok": 1}',
    ])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"ok": 1}
    # Timeout backoff schedule: min(60, 2**network_attempt): 2**0=1, 2**1=2.
    assert sleeps.waits == [1.0, 2.0]
    # Parse ladder pointer untouched -> base temperature throughout.
    assert stub.temps == [0.1, 0.1, 0.1]


def test_openai_apitimeout_is_retryable_network_error(monkeypatch, sleeps):
    llm = _make_client(monkeypatch)
    req = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    stub = _CreateStub([APITimeoutError(req), '{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"ok": 1}
    assert sleeps.waits == [1.0]
    assert stub.temps == [0.1, 0.1]


# ──────────────────────────────────────────────────────────────────────────
# Vision capability gate — raises BEFORE any create() call
# ──────────────────────────────────────────────────────────────────────────
def test_vision_gate_raises_before_any_create_call(monkeypatch, sleeps):
    llm = _make_client(monkeypatch, profile=_make_profile(supports_vision=False))
    stub = _CreateStub(['{"ok": 1}'])  # would succeed if ever called
    _install_create(monkeypatch, llm, stub)

    with pytest.raises(ProviderCapabilityError):
        llm.chat_json("sys", "user", images_b64=["abc123base64"])

    # The gate fires before any network call.
    assert stub.call_count == 0
    assert sleeps.waits == []


def test_vision_gate_allows_text_only_calls_on_no_vision_provider(monkeypatch, sleeps):
    # No images -> gate does not fire even on a vision-incapable provider.
    llm = _make_client(monkeypatch, profile=_make_profile(supports_vision=False))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")
    assert out == {"ok": 1}
    assert stub.call_count == 1


# ──────────────────────────────────────────────────────────────────────────
# max_images truncation + payload SIZE guard
# ──────────────────────────────────────────────────────────────────────────
def _image_parts(kwargs: dict) -> list[dict]:
    """Pull the image_url content parts out of the user message of a create()
    kwargs dict."""
    user_msg = next(m for m in kwargs["messages"] if m["role"] == "user")
    content = user_msg["content"]
    if isinstance(content, str):
        return []
    return [c for c in content if c.get("type") == "image_url"]


def test_max_images_truncates_to_profile_cap(monkeypatch, sleeps):
    llm = _make_client(monkeypatch, profile=_make_profile(max_images=3))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    imgs = [f"img{i}" for i in range(10)]
    llm.chat_json("sys", "user", images_b64=imgs)

    parts = _image_parts(stub.kwargs_log[0])
    assert len(parts) == 3
    # The kept images are the first 3 (slice [:max_images]).
    sent_urls = [p["image_url"]["url"] for p in parts]
    assert sent_urls == [
        "data:image/jpeg;base64,img0",
        "data:image/jpeg;base64,img1",
        "data:image/jpeg;base64,img2",
    ]


def test_oversized_single_image_is_dropped_by_size_guard(monkeypatch, sleeps):
    llm = _make_client(monkeypatch, profile=_make_profile(max_images=6))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    small = "a" * 100
    oversized = "b" * (MAX_IMAGE_B64_CHARS + 1)  # just over the per-image cap
    llm.chat_json("sys", "user", images_b64=[small, oversized])

    parts = _image_parts(stub.kwargs_log[0])
    # The oversized frame is dropped; only the small one survives.
    assert len(parts) == 1
    assert parts[0]["image_url"]["url"] == f"data:image/jpeg;base64,{small}"


def test_image_at_per_image_cap_boundary_is_kept(monkeypatch, sleeps):
    # Exactly == cap is kept (guard drops only strictly greater than cap).
    llm = _make_client(monkeypatch, profile=_make_profile(max_images=6))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    at_cap = "c" * MAX_IMAGE_B64_CHARS
    llm.chat_json("sys", "user", images_b64=[at_cap])

    parts = _image_parts(stub.kwargs_log[0])
    assert len(parts) == 1


def test_total_payload_ceiling_drops_later_frames(monkeypatch, sleeps):
    # Each frame is half the per-image cap so none is individually oversized,
    # but the cumulative total is bounded by MAX_TOTAL_B64_CHARS.
    llm = _make_client(monkeypatch, profile=_make_profile(max_images=6))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    each = MAX_IMAGE_B64_CHARS - 1  # under per-image cap, ~6.99MB chars each
    n = 6
    imgs = [chr(ord("a") + i) * each for i in range(n)]
    total_if_all_kept = each * n
    assert total_if_all_kept > MAX_TOTAL_B64_CHARS  # sanity: ceiling must bite

    llm.chat_json("sys", "user", images_b64=imgs)

    parts = _image_parts(stub.kwargs_log[0])
    kept_total = sum(len(p["image_url"]["url"]) for p in parts)
    # Fewer than all frames survived, and their summed base64 chars stay under
    # the total ceiling.
    assert 0 < len(parts) < n
    kept_b64_chars = sum(
        len(p["image_url"]["url"]) - len("data:image/jpeg;base64,") for p in parts
    )
    assert kept_b64_chars <= MAX_TOTAL_B64_CHARS


def test_guard_keeps_all_normal_images(monkeypatch, sleeps):
    llm = _make_client(monkeypatch, profile=_make_profile(max_images=6))
    stub = _CreateStub(['{"ok": 1}'])
    _install_create(monkeypatch, llm, stub)

    imgs = ["x" * 1000, "y" * 2000, "z" * 3000]
    llm.chat_json("sys", "user", images_b64=imgs)

    parts = _image_parts(stub.kwargs_log[0])
    assert len(parts) == 3


# ──────────────────────────────────────────────────────────────────────────
# Cache short-circuit
# ──────────────────────────────────────────────────────────────────────────
class _FakeCache:
    """Minimal cache exposing the surface LLMClient uses: make_key (static on
    the real ResponseCache, but the client calls ResponseCache.make_key, not
    self.cache.make_key) plus get/put. We seed get() with a hit."""

    def __init__(self, hit=None):
        self._hit = hit
        self.get_keys: list[str] = []
        self.put_calls: list[tuple] = []

    def get(self, key):
        self.get_keys.append(key)
        return self._hit

    def put(self, key, value):
        self.put_calls.append((key, value))


def test_cache_hit_short_circuits_create_and_increments_cache_hits(monkeypatch, sleeps):
    cached_payload = {"tier_1": "Automobiles", "cached": True}
    cache = _FakeCache(hit=cached_payload)
    llm = _make_client(monkeypatch, cache=cache)
    stub = _CreateStub(['{"should": "not be used"}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == cached_payload
    assert stub.call_count == 0       # network never touched
    assert llm.cache_hits == 1
    assert llm.calls == 0             # no create+parse round happened
    assert sleeps.waits == []
    assert cache.put_calls == []      # nothing new to store on a hit


def test_cache_miss_populates_cache_and_calls_create(monkeypatch, sleeps):
    cache = _FakeCache(hit=None)
    llm = _make_client(monkeypatch, cache=cache)
    stub = _CreateStub(['{"tier_1": "Automobiles"}'])
    _install_create(monkeypatch, llm, stub)

    out = llm.chat_json("sys", "user")

    assert out == {"tier_1": "Automobiles"}
    assert stub.call_count == 1
    assert llm.cache_hits == 0
    # On a miss the parsed result is written back.
    assert len(cache.put_calls) == 1
    stored_key, stored_val = cache.put_calls[0]
    assert stored_val == {"tier_1": "Automobiles"}
    # The same key was used for get and put.
    assert cache.get_keys == [stored_key]
