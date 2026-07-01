"""Shared concurrency governors for the multi-lane architecture.

- TokenBucket: bounds the AGGREGATE request rate (watch-page navigations/min)
  across all browser lanes, independent of lane count — the real bot-hygiene
  governor. A lane must acquire a token before navigating.
- CircuitBreaker: tracks "stress" signals (captcha trips, login-required,
  consent re-prompts, clustered failures) in a rolling window and tells lanes
  to back off (slow the bucket) or retire (reduce concurrency) before a hard halt.
- LLM concurrency is bounded by a plain threading.BoundedSemaphore created in
  the orchestrator (peak concurrent calls = analysis_workers x channel-brief
  fan-out, which can exceed Ollama's ceiling — the semaphore is the one cap).

A monotonic clock is injected so tests can drive time deterministically without
real sleeping.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class TokenBucket:
    """Classic token bucket. capacity=rate_per_min tokens, refilled continuously.
    acquire() blocks until a token is available (or returns early if a stop Event
    is set). Thread-safe across N lanes."""

    def __init__(self, rate_per_min: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._rate_per_s = max(rate_per_min, 1) / 60.0
        self._capacity = max(rate_per_min, 1)
        self._tokens = float(self._capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def set_rate(self, rate_per_min: int) -> None:
        with self._lock:
            self._rate_per_s = max(rate_per_min, 1) / 60.0
            self._capacity = max(rate_per_min, 1)
            self._tokens = min(self._tokens, float(self._capacity))

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_s)
            self._last = now

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill_locked()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def acquire(self, stop: threading.Event | None = None, max_wait_s: float = 120.0) -> bool:
        """Block until a token is granted. Returns False if stop is set or
        max_wait_s elapses without a token."""
        deadline = self._clock() + max_wait_s
        while True:
            if stop is not None and stop.is_set():
                return False
            if self.try_acquire():
                return True
            if self._clock() >= deadline:
                return False
            # wait roughly until the next token would be available
            with self._lock:
                deficit = 1.0 - self._tokens
                wait = max(deficit / self._rate_per_s, 0.02) if self._rate_per_s else 0.5
            self._sleep(min(wait, 1.0))


class CircuitBreaker:
    """Counts stress events in a rolling window and drives a degradation ladder:
      level 0 — healthy
      level 1 — 1 event: halve rate + widen jitter (caller applies)
      level 2+ — retire one lane per additional event, down to min_lanes
      hard captcha / floor still tripping — caller triggers global halt.
    Lane retirement is advisory: lanes consult should_retire() and exit cleanly."""

    def __init__(self, *, min_lanes: int, start_lanes: int, window_s: float = 300.0,
                 enabled: bool = True, clock: Callable[[], float] = time.monotonic):
        self.enabled = enabled
        self.min_lanes = min_lanes
        self._allowed_lanes = start_lanes
        self._start_lanes = start_lanes
        self._window_s = window_s
        self._clock = clock
        self._events: list[float] = []
        self._lock = threading.Lock()

    def _prune_locked(self) -> None:
        cutoff = self._clock() - self._window_s
        self._events = [t for t in self._events if t >= cutoff]

    def record_stress(self, kind: str = "stress") -> int:
        """Register a stress signal; returns the new stress level (event count in
        window). Also lowers the allowed-lane budget for level >= 2."""
        if not self.enabled:
            return 0
        with self._lock:
            self._events.append(self._clock())
            self._prune_locked()
            level = len(self._events)
            if level >= 2 and self._allowed_lanes > self.min_lanes:
                self._allowed_lanes = max(self.min_lanes, self._start_lanes - (level - 1))
            return level

    @property
    def allowed_lanes(self) -> int:
        with self._lock:
            return self._allowed_lanes

    def should_retire(self, lane_index: int) -> bool:
        """True if this lane's index is above the current allowed-lane budget."""
        if not self.enabled:
            return False
        with self._lock:
            return lane_index >= self._allowed_lanes

    def stress_level(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._events)
