"""Unit tests for the shared concurrency governors (deterministic clock — no
real sleeping)."""
import threading

import pytest

from ytqc.pipeline.governor import CircuitBreaker, TokenBucket


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


# ── TokenBucket ──────────────────────────────────────────────────────────

def test_bucket_starts_full_and_drains():
    c = FakeClock()
    tb = TokenBucket(60, clock=c.now, sleep=c.sleep)   # cap 60, 1/sec
    got = sum(1 for _ in range(60) if tb.try_acquire())
    assert got == 60
    assert not tb.try_acquire()        # empty


def test_bucket_refills_over_time():
    c = FakeClock()
    tb = TokenBucket(60, clock=c.now, sleep=c.sleep)
    for _ in range(60):
        tb.try_acquire()
    assert not tb.try_acquire()
    c.t += 5.0                          # 5s → ~5 tokens at 1/sec
    assert sum(1 for _ in range(5) if tb.try_acquire()) == 5
    assert not tb.try_acquire()


def test_bucket_acquire_blocks_then_grants():
    c = FakeClock()
    tb = TokenBucket(60, clock=c.now, sleep=c.sleep)
    for _ in range(60):
        tb.try_acquire()
    assert tb.acquire(max_wait_s=10)    # should sleep (advancing clock) then succeed
    assert c.slept                      # it slept at least once


def test_bucket_acquire_respects_stop_event():
    c = FakeClock()
    tb = TokenBucket(60, clock=c.now, sleep=c.sleep)
    for _ in range(60):
        tb.try_acquire()
    stop = threading.Event()
    stop.set()
    assert tb.acquire(stop=stop) is False    # halts immediately, no token


def test_bucket_set_rate_lowers_cap():
    c = FakeClock()
    tb = TokenBucket(60, clock=c.now, sleep=c.sleep)
    tb.set_rate(10)
    for _ in range(60):
        tb.try_acquire()
    c.t += 1.0                          # at 10/min ≈ 0.167/sec, 1s → <1 token
    assert not tb.try_acquire()


# ── CircuitBreaker ─────────────────────────────────────────────────────────

def test_breaker_healthy_no_retire():
    cb = CircuitBreaker(min_lanes=2, start_lanes=10)
    assert cb.allowed_lanes == 10
    assert not cb.should_retire(9)


def test_breaker_level1_no_lane_cut():
    cb = CircuitBreaker(min_lanes=2, start_lanes=10)
    assert cb.record_stress() == 1
    assert cb.allowed_lanes == 10       # level 1 only slows, doesn't retire


def test_breaker_level2plus_retires_lanes():
    cb = CircuitBreaker(min_lanes=2, start_lanes=10)
    cb.record_stress()                  # 1
    cb.record_stress()                  # 2 → allowed 9
    assert cb.allowed_lanes == 9
    assert cb.should_retire(9) and not cb.should_retire(8)
    cb.record_stress()                  # 3 → allowed 8
    assert cb.allowed_lanes == 8


def test_breaker_floor_at_min_lanes():
    cb = CircuitBreaker(min_lanes=2, start_lanes=5)
    for _ in range(20):
        cb.record_stress()
    assert cb.allowed_lanes == 2        # never below the floor


def test_breaker_disabled_is_noop():
    cb = CircuitBreaker(min_lanes=2, start_lanes=10, enabled=False)
    cb.record_stress(); cb.record_stress(); cb.record_stress()
    assert cb.allowed_lanes == 10
    assert not cb.should_retire(99)


def test_breaker_window_prunes_old_events():
    c = FakeClock()
    cb = CircuitBreaker(min_lanes=2, start_lanes=10, window_s=300, clock=c.now)
    cb.record_stress(); cb.record_stress()
    assert cb.stress_level() == 2
    c.t += 400                          # past the window
    assert cb.stress_level() == 0
