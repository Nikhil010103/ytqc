"""LLM client: OpenAI SDK against any OpenAI-compatible endpoint (Ollama /v1
by default). Includes JSON salvage parser, 429 backoff, and temperature
escalation 0.1→0.3→0.6."""
from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from typing import Optional

# shared no-op context for the "no semaphore configured" path (thread-safe)
_NULL_CTX = contextlib.nullcontext()

import httpx
from openai import (
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
)

from ytqc.config import ProviderProfile
from ytqc.llm.cache import ResponseCache

log = logging.getLogger("ytqc.llm")

TEMP_LADDER = (0.1, 0.3, 0.6)

# Vision payload size guards (base64 char counts; ~4/3 of binary bytes).
# Drop any single image whose base64 exceeds ~5MB binary, and cap the total
# embedded payload to avoid sending an unbounded request body (DoS guard).
MAX_IMAGE_B64_CHARS = 7_000_000      # ~5MB binary per image
MAX_TOTAL_B64_CHARS = 28_000_000     # ~20MB binary across all kept images

# HTTP statuses that are terminal — never retried, fail fast.
TERMINAL_STATUSES = (401, 403, 404)


class ProviderCapabilityError(RuntimeError):
    pass


def parse_llm_json(raw: str) -> dict:
    """Robustly parse a (possibly messy) LLM response into a dict.

    Handles: <think> blocks, markdown fences, prose around the payload,
    bare-list responses, truncated JSON (bracket-balance recovery).
    """
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"```", "", cleaned).strip()

    start_idx = min(
        [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1],
        default=-1,
    )
    end_idx = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if start_idx == -1:
        raise ValueError("no JSON object or array found in response")
    cleaned = cleaned[start_idx: end_idx + 1] if end_idx >= start_idx else cleaned[start_idx:]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = _repair_json(cleaned)

    if isinstance(parsed, list):
        parsed = {"items": parsed}
    elif not isinstance(parsed, dict):
        raise ValueError(f"expected dict/list, got {type(parsed).__name__}")
    return parsed


def _repair_json(text: str) -> dict | list:
    """Salvage a truncated JSON string by closing open brackets."""
    trimmed = text.rstrip(" \t\n\r,")

    open_curly = open_square = 0
    in_string = False
    escape_next = False
    last_safe = 0

    for i, ch in enumerate(trimmed):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            open_curly += 1
        elif ch == "}":
            open_curly -= 1
            if open_curly >= 0 and open_square == 0:
                last_safe = i + 1
        elif ch == "[":
            open_square += 1
        elif ch == "]":
            open_square -= 1
            if open_square >= 0 and open_curly == 0:
                last_safe = i + 1

    candidate = trimmed[:last_safe] if last_safe > 0 else trimmed
    open_c = candidate.count("{") - candidate.count("}")
    open_s = candidate.count("[") - candidate.count("]")
    if candidate.count('"') % 2 == 1:
        candidate = candidate.rsplit('"', 1)[0].rstrip(" \t\n\r,:")
    candidate += "]" * max(open_s, 0)
    candidate += "}" * max(open_c, 0)
    return json.loads(candidate)


class LLMClient:
    def __init__(self, profile: ProviderProfile, provider_name: str,
                 cache: Optional[ResponseCache] = None, semaphore=None):
        self.profile = profile
        self.provider_name = provider_name
        self.cache = cache
        # optional shared BoundedSemaphore: global cap on concurrent LLM calls,
        # so N analysis workers x channel-brief fan-out can't exceed Ollama's ceiling
        self._sema = semaphore
        self.calls = 0
        self.cache_hits = 0
        self._client = OpenAI(
            base_url=profile.base_url,
            api_key=profile.resolved_api_key() or "unused",
            timeout=profile.timeout_s,
        )

    @property
    def model(self) -> str:
        return self.profile.model

    def chat_json(
        self,
        system: str,
        user: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.1,
        escalate: bool = True,
        max_attempts: int = 5,
        max_parse_attempts: Optional[int] = None,
        max_network_attempts: Optional[int] = None,
    ) -> dict:
        """One JSON-returning chat call with caching, network backoff and
        temperature escalation on parse failure. Raises on terminal failure.

        Parse retries (temperature ladder 0.1->0.3->0.6) and network retries
        (429/5xx/timeout backoff) draw on *independent* budgets so a 429 storm
        cannot exhaust the attempts reserved for JSON-parse escalation.
        ``max_attempts`` is retained for backward compatibility and seeds both
        budgets when the explicit ones are not supplied. Terminal HTTP errors
        (401/403/404) fail fast without sleeping."""
        # Independent budgets: parse-escalation vs network backoff.
        parse_budget = max_parse_attempts if max_parse_attempts is not None else max_attempts
        network_budget = max_network_attempts if max_network_attempts is not None else max_attempts
        parse_budget = max(1, parse_budget)
        network_budget = max(1, network_budget)

        images_b64 = images_b64 or []
        # Vision-capability gate BEFORE any network call.
        if images_b64 and not self.profile.supports_vision:
            raise ProviderCapabilityError(
                f"provider {self.provider_name!r} does not support vision input"
            )
        images_b64 = images_b64[: self.profile.max_images]
        images_b64 = self._guard_image_payload(images_b64)

        key = None
        if self.cache:
            key = ResponseCache.make_key(self.provider_name, self.model, system, user, images_b64)
            cached = self.cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached

        content: list[dict] | str
        if images_b64:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img}"}}
                for img in images_b64
            ]
        else:
            content = user

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        temps = [t for t in TEMP_LADDER if t >= temperature] or [temperature]
        if not escalate:
            temps = [temperature]

        # Global concurrent-LLM cap (multi-lane). Acquired AFTER the cache check
        # so cache hits never consume a permit; released on every exit path.
        sema_cm = self._sema if self._sema is not None else _NULL_CTX
        with sema_cm:
            last_exc: Exception | None = None
            temp_idx = 0
            parse_attempt = 0       # consumes parse_budget; drives the temperature ladder
            network_attempt = 0     # consumes network_budget; drives backoff
            while parse_attempt < parse_budget and network_attempt < network_budget:
                temp = temps[min(temp_idx, len(temps) - 1)]
                try:
                    kwargs: dict = dict(model=self.model, messages=messages, temperature=temp)
                    if self.profile.supports_json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    t0 = time.time()
                    resp = self._client.chat.completions.create(**kwargs)
                    raw = (resp.choices[0].message.content or "").strip()
                    self.calls += 1
                    parsed = parse_llm_json(raw)
                    log.debug("llm ok temp=%.1f %.1fs", temp, time.time() - t0)
                    if self.cache and key:
                        self.cache.put(key, parsed)
                    return parsed
                except (json.JSONDecodeError, ValueError) as exc:
                    last_exc = exc
                    temp_idx += 1           # escalate temperature out of degenerate sampling
                    parse_attempt += 1
                    log.warning("json parse failed (parse attempt %d/%d, temp %.1f): %s",
                                parse_attempt, parse_budget, temp, exc)
                except (AuthenticationError, PermissionDeniedError, NotFoundError) as exc:
                    # Terminal: bad credentials / forbidden / model-not-found. Fail
                    # fast — retrying or sleeping cannot recover these.
                    raise RuntimeError(
                        f"LLM terminal error ({type(exc).__name__}): {exc}"
                    ) from exc
                except (APITimeoutError, httpx.TimeoutException) as exc:
                    # Timeout is a retryable NETWORK error, not a parse failure.
                    last_exc = exc
                    wait = min(60.0, 2.0 ** network_attempt)
                    network_attempt += 1
                    log.warning("llm timeout (network attempt %d/%d), backing off %.0fs: %s",
                                network_attempt, network_budget, wait, exc)
                    time.sleep(wait)
                except Exception as exc:
                    last_exc = exc
                    status = getattr(exc, "status_code", None)
                    if status in TERMINAL_STATUSES:
                        # Fast-fail terminal HTTP errors without sleeping.
                        raise RuntimeError(
                            f"LLM terminal error (HTTP {status}): {exc}"
                        ) from exc
                    if status == 429 or "429" in str(exc):
                        wait = min(60.0, 5.0 * (2 ** network_attempt))
                        network_attempt += 1
                        log.warning("rate limited (network attempt %d/%d), backing off %.0fs",
                                    network_attempt, network_budget, wait)
                        time.sleep(wait)
                    else:
                        wait = 2 ** network_attempt
                        network_attempt += 1
                        log.warning("llm error (network attempt %d/%d): %s",
                                    network_attempt, network_budget, exc)
                        time.sleep(wait)
            raise RuntimeError(
                f"LLM call failed (parse attempts {parse_attempt}/{parse_budget}, "
                f"network attempts {network_attempt}/{network_budget}): {last_exc}"
            )

    def _guard_image_payload(self, images_b64: list[str]) -> list[str]:
        """Bound the embedded base64 vision payload to avoid sending an
        unbounded request body. Drops oversized individual frames and caps the
        cumulative payload, logging a warning when frames are dropped."""
        if not images_b64:
            return images_b64

        kept: list[str] = []
        total = 0
        dropped_oversize = 0
        dropped_ceiling = 0
        for img in images_b64:
            n = len(img)
            if n > MAX_IMAGE_B64_CHARS:
                dropped_oversize += 1
                continue
            if total + n > MAX_TOTAL_B64_CHARS:
                dropped_ceiling += 1
                continue
            kept.append(img)
            total += n

        if dropped_oversize:
            log.warning("dropped %d oversized vision frame(s) exceeding %d base64 chars",
                        dropped_oversize, MAX_IMAGE_B64_CHARS)
        if dropped_ceiling:
            log.warning("dropped %d vision frame(s) to keep total payload under %d base64 chars",
                        dropped_ceiling, MAX_TOTAL_B64_CHARS)
        return kept
