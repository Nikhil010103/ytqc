"""Shared, hermetic test doubles for the ytqc test suite.

Nothing here touches the network, a real browser, Ollama, or the clock.

Two doubles:

  FakeLLMClient  — drop-in for ytqc.llm.client.LLMClient. Routes chat_json()
                   to canned dicts via a router callable, an in-order
                   responses list, or a by-system-substring map.

  FakeKimiClient — drop-in for ytqc.browser.webbridge.KimiClient. Routes
                   .js(code) to canned payloads by matching the passed code
                   against the ytqc.browser.youtube_js constants.

Plus module-level factories returning known-good *raw* LLM outputs
(good_content_output / good_vision_evidence / good_channel_synth)
built from the real closed vocabulary in ytqc.taxonomy, so a test can grab a
valid baseline and override a single field.

Usage sketch
------------
    from tests.fakes import FakeLLMClient, FakeKimiClient, good_content_output
    from tests.fixtures import yt_payloads as P

    llm = FakeLLMClient(by_system={"senior brand-safety": good_content_output()})
    kimi = FakeKimiClient({"player_response": P.PLAYER_RESPONSE_OK,
                           "likes": P.LIKES_OK}, default={"ok": False})
"""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Optional

from ytqc.browser import youtube_js as J

# ──────────────────────────────────────────────────────────────────────────
# Known-good raw LLM output factories
# ──────────────────────────────────────────────────────────────────────────
# These mirror the JSON schemas declared in ytqc/llm/prompts.py and use real
# values from ytqc.taxonomy so that ytqc.agents.validator.normalize() accepts
# good_content_output()/good_channel_synth() and the model validators accept
# good_vision_evidence().


def good_content_output(**overrides: Any) -> dict:
    """A valid raw Content-Analyst / Channel-Synthesizer output dict.

    Passes ytqc.agents.validator.normalize() unchanged (tier_1 is a real
    TIER_1_CATEGORIES value, brand_safety/targeted_audience shapes are valid).
    Override any top-level key via kwargs, e.g.
    ``good_content_output(tier_1="Kids", kids_age_group="3-5 years")``.
    """
    out: dict = {
        "summary": "A detailed track-day review of the Kawasaki Ninja ZX-4R.",
        "hook": "Can a 400cc inline-four really hang on a race circuit?",
        "content_themes": ["Product Review", "Motorsport"],
        "topics": ["Kawasaki Ninja ZX-4R", "track-day setup", "lap times"],
        "sentiment": "positive",
        "comment_sentiment": {
            "overall": "positive",
            "summary": "Viewers are excited about the lap times.",
            "sample_count": 3,
        },
        "primary_audience": "Motorcycle enthusiasts aged 25-34 interested in track riding.",
        "target_industries": ["Two-Wheeler Brands", "Riding Gear", "Track Tyres"],
        "brand_safety": {
            "is_safe": True,
            "risk_level": "none",
            "triggered_categories": [],
            "explanation": "Clean automotive content with no triggers.",
        },
        "tier_1": "Automobiles",
        "tier_2": "motorcycle reviews",
        "tier_classification_reasoning": (
            "YouTube Category 'Autos & Vehicles' and tag 'ninja zx-4r' "
            "→ tier_1=Automobiles, tier_2=motorcycle reviews."
        ),
        "keywords": ["kawasaki", "ninja zx-4r", "track day", "motorcycle review", "inline four"],
        "language": "en",
        "targeted_region": "United States",
        "kids_age_group": None,
        "targeted_audience": {
            "age_group": "25-34",
            "gender": "male",
            "interests": ["track-day riders", "sportbike fans", "motorsport"],
        },
        "suitable_age_group": "all ages",
        "is_premium_luxury": False,
        "qc_notes": "",
    }
    out.update(overrides)
    return out


def good_vision_evidence(**overrides: Any) -> dict:
    """A valid raw Vision-Analyst output dict.

    Validates against ytqc.models.VisionEvidence (the vision analyst calls
    ``VisionEvidence.model_validate({**raw, "ok": True})``). Do NOT include
    "ok"/"error" — the analyst injects ok itself.
    """
    out: dict = {
        "frames": [
            {"position": "thumbnail", "description": "Sportbike on a race track"},
            {"position": "middle", "description": "Rider leaning into a corner"},
        ],
        "on_screen_text": ["ZX-4R", "Lap 3"],
        "content_format": "product review",
        "production_quality": "professional",
        "visual_kids_signals": {"present": False, "signals": []},
        "visual_safety_flags": [],
        "people": {"apparent_age_range": "25-40", "notes": "Adult male rider in gear"},
        "brands_or_products_visible": ["Kawasaki", "Alpinestars"],
        "premium_luxury_signals": [],
        "visible_language": "en",
    }
    out.update(overrides)
    return out


# Channel synthesizer emits the same schema as the content analyst, so the
# baseline is identical; provided under a clear name for call sites.
def good_channel_synth(**overrides: Any) -> dict:
    """A valid raw Channel-Synthesizer output dict (same schema as content)."""
    base = good_content_output()
    base.update({
        "summary": "A motorcycle channel publishing reviews and track-day content weekly.",
        "qc_notes": "Agrees with draft aggregate; no deviation.",
    })
    base.update(overrides)
    return base


def good_judge_output(**overrides: Any) -> dict:
    """A valid raw Judge output dict: {resolved_fields, judge_notes}."""
    out: dict = {
        "resolved_fields": {},
        "judge_notes": "No change required; analyst output upheld.",
    }
    out.update(overrides)
    return out


# ──────────────────────────────────────────────────────────────────────────
# Provider profile stub
# ──────────────────────────────────────────────────────────────────────────
class _FakeProfile:
    """Minimal stand-in for ytqc.config.ProviderProfile, exposing only the
    attributes the LLM callers read off ``llm.profile``."""

    def __init__(self, supports_vision: bool = True, max_images: int = 6,
                 supports_json_mode: bool = True, model: str = "fake-model"):
        self.supports_vision = supports_vision
        self.max_images = max_images
        self.supports_json_mode = supports_json_mode
        self.model = model


# ──────────────────────────────────────────────────────────────────────────
# FakeLLMClient
# ──────────────────────────────────────────────────────────────────────────
class FakeLLMClient:
    """Hermetic stand-in for ytqc.llm.client.LLMClient.

    Configure routing with exactly one of:
      * router=callable(system, user, images_b64) -> dict
      * responses=[dict, ...]            returned in call order
      * by_system={substr: dict | [dict, ...]}  matched against the system prompt

    For by_system, a list value is consumed one entry per matching call (so you
    can script multiple temperature-escalation rounds for the same agent); a
    single dict is returned on every matching call.

    Attributes mirror LLMClient: .provider_name, .model, .profile (with
    .supports_vision and .max_images), .calls (incremented per chat_json),
    .cache_hits (always 0 here), plus .history — a list of
    (system, user, images_b64) tuples for assertions.
    """

    def __init__(
        self,
        *,
        router: Optional[Callable[[str, str, Optional[list]], dict]] = None,
        responses: Optional[list] = None,
        by_system: Optional[dict] = None,
        provider_name: str = "fake",
        model: str = "fake-model",
        supports_vision: bool = True,
        max_images: int = 6,
        supports_json_mode: bool = True,
    ):
        configured = [x is not None for x in (router, responses, by_system)]
        if sum(configured) > 1:
            raise ValueError(
                "FakeLLMClient: pass at most one of router / responses / by_system"
            )
        self._router = router
        # store a private mutable copy so popping from responses / by_system
        # lists doesn't mutate the caller's data
        self._responses = list(responses) if responses is not None else None
        self._by_system = {
            k: (list(v) if isinstance(v, list) else v)
            for k, v in (by_system or {}).items()
        } if by_system is not None else None

        self.provider_name = provider_name
        self.profile = _FakeProfile(
            supports_vision=supports_vision,
            max_images=max_images,
            supports_json_mode=supports_json_mode,
            model=model,
        )
        self._model = model
        self.calls = 0
        self.cache_hits = 0
        self.history: list[tuple[str, str, Optional[list]]] = []

    @property
    def model(self) -> str:
        return self._model

    def chat_json(
        self,
        system: str,
        user: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.1,
        escalate: bool = True,
        **kw: Any,
    ) -> dict:
        self.calls += 1
        self.history.append((system, user, images_b64))
        result = self._route(system, user, images_b64)
        # Deep-copy so a caller mutating the returned dict can't corrupt the
        # canned data (the real client returns freshly-parsed JSON each call).
        return copy.deepcopy(result)

    # ── routing ───────────────────────────────────────────────────────────
    def _route(self, system: str, user: str, images_b64: Optional[list]) -> dict:
        if self._router is not None:
            out = self._router(system, user, images_b64)
            if out is None:
                raise AssertionError("FakeLLMClient router returned None")
            return out

        if self._responses is not None:
            if not self._responses:
                raise AssertionError(
                    "FakeLLMClient: responses list exhausted "
                    f"(call #{self.calls}); add more canned responses"
                )
            return self._responses.pop(0)

        if self._by_system is not None:
            for substr, value in self._by_system.items():
                if substr in system:
                    if isinstance(value, list):
                        if not value:
                            raise AssertionError(
                                f"FakeLLMClient: by_system[{substr!r}] list exhausted"
                            )
                        return value.pop(0)
                    return value
            raise AssertionError(
                "FakeLLMClient: no by_system key matched system prompt. "
                f"keys={list(self._by_system)!r}; system starts: {system[:80]!r}"
            )

        raise AssertionError(
            "FakeLLMClient has no response configured "
            "(pass router=, responses=, or by_system=)"
        )


# ──────────────────────────────────────────────────────────────────────────
# FakeKimiClient
# ──────────────────────────────────────────────────────────────────────────
# Map a friendly label -> the youtube_js constant whose code identifies it.
# .js(code) is routed by checking, in priority order, whether the passed code
# equals or contains one of these constants (some, like COMMENTS, are passed
# after a __TOP_N__ substitution; FRAME_SEEK after a __T__ substitution — so we
# match on a stable prefix for those).
_LABEL_TO_CONSTANT: dict[str, str] = {
    "player_response": J.PLAYER_RESPONSE,
    "likes": J.LIKES,
    "channel_about": J.CHANNEL_ABOUT,
    "channel_videos": J.CHANNEL_VIDEOS,
    "transcript_open": J.TRANSCRIPT_OPEN,
    "transcript_scrape": J.TRANSCRIPT_SCRAPE,
    "watch_ready": J.WATCH_READY,
    "channel_ready": J.CHANNEL_READY,
    "frame_ready": J.FRAME_READY,
    "ad_skip": J.AD_SKIP,
    "player_quality": J.PLAYER_QUALITY,
    "player_pause": J.PLAYER_PAUSE,
}

# Constants that the callers mutate before passing to .js() — match on a prefix
# that survives the substitution. COMMENTS is split at __TOP_N__, FRAME_SEEK at
# __T__. FRAME_GRAB returns a *string* (data URL or 'ERR:...'), not a dict.
_PREFIX_LABELS: list[tuple[str, str]] = [
    ("comments", J.COMMENTS.split("__TOP_N__")[0]),
    ("frame_seek", J.FRAME_SEEK.split("__T__")[0]),
    ("frame_grab", J.FRAME_GRAB[:60]),
]


class FakeKimiClient:
    """Hermetic stand-in for ytqc.browser.webbridge.KimiClient.

    Feed it a label->payload dict; .js(code) matches the code against the
    youtube_js constants and returns the configured payload (or ``default``).

    Recognised labels (see _LABEL_TO_CONSTANT / _PREFIX_LABELS):
      player_response, likes, comments, channel_about, channel_videos,
      transcript_open, transcript_scrape, watch_ready, channel_ready,
      frame_ready, frame_seek, frame_grab, ad_skip, player_quality,
      player_pause.

    navigate()/scroll()/item_pause()/close() are no-ops (navigate records the
    url). screenshot_b64() returns ``screenshot`` (default a tiny JPEG b64).
    poll_window_var() returns ``poll`` if configured else {"state": "timeout"}.

    Inspection attributes: .navigated (list of urls), .js_calls (list of raw
    code strings), .closed (bool).
    """

    def __init__(
        self,
        routes: Optional[dict] = None,
        *,
        default: Any = None,
        screenshot: Optional[str] = None,
        poll: Any = None,
    ):
        self._routes = dict(routes or {})
        self._default = default if default is not None else {"ok": False}
        # default screenshot: a tiny valid base64 JPEG (1x1) so callers get a
        # non-empty string. Import lazily to avoid a hard fixtures dependency.
        if screenshot is not None:
            self._screenshot = screenshot
        else:
            try:
                from tests.fixtures.yt_payloads import TINY_JPEG_B64
                self._screenshot = TINY_JPEG_B64
            except Exception:
                self._screenshot = ""
        self._poll = poll
        # inspection
        self.navigated: list[str] = []
        self.js_calls: list[str] = []
        self.closed = False

    # ── raw bridge surface used by callers ─────────────────────────────────
    def js(self, code: str, timeout: float = 45.0) -> Any:
        self.js_calls.append(code)
        label = self._match_label(code)
        if label is not None and label in self._routes:
            return copy.deepcopy(self._routes[label])
        return copy.deepcopy(self._default)

    def _match_label(self, code: str) -> Optional[str]:
        if not isinstance(code, str):
            return None
        # exact / containment match against full constants first (most specific)
        for label, const in _LABEL_TO_CONSTANT.items():
            if code == const or const in code:
                return label
        # prefix match for substituted / string-returning constants
        for label, prefix in _PREFIX_LABELS:
            if prefix and prefix in code:
                return label
        return None

    def screenshot_b64(self) -> str:
        return self._screenshot

    def navigate(self, url: str, new_tab: bool = False, ready_js: Optional[str] = None) -> None:
        self.navigated.append(url)

    def scroll(self, px: int = 500, settle: float = 0.8) -> None:
        return None

    def item_pause(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def poll_window_var(self, var: str, timeout_s: float = 10.0, interval: float = 0.3) -> Any:
        if self._poll is not None:
            return copy.deepcopy(self._poll)
        return {"state": "timeout"}


# ── FakeAgentLLM ────────────────────────────────────────────────────────────
# Emits scripted tool-calling turns shaped like OpenAI chat completions, so the
# agent loop can be tested hermetically (no network). Each scripted turn is
# either {"tool_calls": [{"name", "arguments": <dict or json str>}]} or
# {"content": "<final text>"}. Optionally raise on the first N create() calls to
# exercise temperature escalation.
class _FakeFunc:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, i, name, arguments):
        self.id = f"call_{i}"
        self.type = "function"
        self.function = _FakeFunc(name, arguments)


class _FakeMessage:
    def __init__(self, content, tool_calls):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(self, message, finish_reason):
        self.choices = [_FakeChoice(message, finish_reason)]


class FakeAgentLLM:
    """Drop-in for ytqc.agent.loop.AgentLLM. `script` is a list of turns popped
    in order on each create(). `raise_times` makes the first N create() calls
    raise (to test escalation)."""
    def __init__(self, script, model="fake", raise_times=0):
        self.model = model
        self._script = list(script)
        self._raise_left = raise_times
        self.calls = 0
        self.temps = []

    def create(self, messages, tools, temperature):
        self.calls += 1
        self.temps.append(temperature)
        if self._raise_left > 0:
            self._raise_left -= 1
            raise RuntimeError("simulated transient failure")
        turn = self._script.pop(0) if self._script else {"content": "(no more script)"}
        tcs = turn.get("tool_calls")
        if tcs:
            calls = [_FakeToolCall(i, t["name"], t.get("arguments", {})) for i, t in enumerate(tcs)]
            return _FakeCompletion(_FakeMessage(turn.get("content", ""), calls), "tool_calls")
        return _FakeCompletion(_FakeMessage(turn.get("content", ""), None), "stop")
