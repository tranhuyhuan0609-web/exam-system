"""Tests for E01/G03/RB-08 pure Circuit Breaker & deterministic backoff (P70)."""

from __future__ import annotations

from app.platform.circuit_breaker import (
    CircuitBreakerPolicy,
    compute_exponential_backoff_seconds,
    evaluate_circuit_entry,
    initial_circuit,
    record_circuit_outcome,
)


def test_circuit_opens_on_5xx_or_timeout_gt_15s_and_recovers_via_half_open() -> None:
    policy = CircuitBreakerPolicy(
        failure_threshold=2,
        cooldown_seconds=30.0,
        half_open_max_probes=2,
        timeout_threshold_seconds=15.0,
    )
    snap = initial_circuit()

    # 1st failure: HTTP 503
    snap = record_circuit_outcome(
        snap,
        succeeded=False,
        latency_seconds=1.2,
        status_code=503,
        now_monotonic=100.0,
        policy=policy,
    )
    assert snap.state == "CLOSED"
    assert snap.consecutive_failures == 1

    # 2nd failure: slow call > 15s even if transport returned HTTP 200 -> trips OPEN
    snap = record_circuit_outcome(
        snap,
        succeeded=True,
        latency_seconds=16.5,
        status_code=200,
        now_monotonic=105.0,
        policy=policy,
    )
    assert snap.state == "OPEN"

    # During cooldown: fast-fail
    blocked = evaluate_circuit_entry(snap, now_monotonic=115.0, policy=policy)
    assert blocked.allowed is False
    assert blocked.reason_code == "CIRCUIT_OPEN"
    assert blocked.retry_after_seconds == 20.0

    # After cooldown: enters HALF_OPEN and closes after 2 successful probes
    probe1 = evaluate_circuit_entry(snap, now_monotonic=136.0, policy=policy)
    assert probe1.allowed is True
    assert probe1.next_snapshot.state == "HALF_OPEN"

    snap = record_circuit_outcome(
        probe1.next_snapshot,
        succeeded=True,
        latency_seconds=0.8,
        status_code=200,
        now_monotonic=137.0,
        policy=policy,
    )
    assert snap.state == "HALF_OPEN"

    probe2 = evaluate_circuit_entry(snap, now_monotonic=138.0, policy=policy)
    snap = record_circuit_outcome(
        probe2.next_snapshot,
        succeeded=True,
        latency_seconds=0.9,
        status_code=200,
        now_monotonic=139.0,
        policy=policy,
    )
    assert snap.state == "CLOSED"


def test_deterministic_exponential_backoff_bounds() -> None:
    d0 = compute_exponential_backoff_seconds(attempt=0, base_seconds=2.0, max_seconds=30.0, jitter_seed="evt-1")
    d3 = compute_exponential_backoff_seconds(attempt=3, base_seconds=2.0, max_seconds=30.0, jitter_seed="evt-1")
    assert 1.0 <= d0 <= 2.0
    assert 8.0 <= d3 <= 16.0
    assert compute_exponential_backoff_seconds(attempt=3, base_seconds=2.0, max_seconds=30.0, jitter_seed="evt-1") == d3


# --- regression + boundary tests --------------------------------------------------------------

import math  # noqa: E402

import pytest  # noqa: E402

from app.platform.circuit_breaker import CircuitBreakerError, CircuitSnapshot  # noqa: E402

_POLICY = CircuitBreakerPolicy(failure_threshold=2, cooldown_seconds=30.0, half_open_max_probes=2)


def _open_snapshot(opened_at: float | None = 100.0) -> CircuitSnapshot:
    return CircuitSnapshot("OPEN", 5, opened_at, 0, 0)


def _ok(snap, now=101.0, **kw):
    return record_circuit_outcome(
        snap, succeeded=True, latency_seconds=0.1, status_code=200, now_monotonic=now, policy=_POLICY, **kw
    )


def _fail(snap, now=101.0):
    return record_circuit_outcome(
        snap, succeeded=False, latency_seconds=0.1, status_code=503, now_monotonic=now, policy=_POLICY
    )


def test_straggler_success_in_open_does_not_close_the_breaker() -> None:
    snap = _open_snapshot()
    assert _ok(snap) == snap


def test_straggler_failure_in_open_does_not_extend_cooldown() -> None:
    snap = _open_snapshot(100.0)
    after = _fail(snap, now=120.0)
    assert after == snap and after.opened_at_monotonic == 100.0
    # cooldown still ends at 130, not 150
    assert evaluate_circuit_entry(after, now_monotonic=131.0, policy=_POLICY).allowed is True


def test_opened_at_zero_reaches_half_open_after_cooldown() -> None:
    snap = _open_snapshot(0.0)
    assert evaluate_circuit_entry(snap, now_monotonic=29.9, policy=_POLICY).allowed is False
    decision = evaluate_circuit_entry(snap, now_monotonic=30.0, policy=_POLICY)
    assert decision.allowed is True and decision.next_snapshot.state == "HALF_OPEN"


def test_open_without_timestamp_fails_closed_for_a_full_cooldown() -> None:
    decision = evaluate_circuit_entry(_open_snapshot(None), now_monotonic=500.0, policy=_POLICY)
    assert decision.allowed is False
    assert decision.next_snapshot.opened_at_monotonic == 500.0
    assert decision.retry_after_seconds == 30.0


def test_cooldown_boundary_is_inclusive() -> None:
    snap = _open_snapshot(100.0)
    assert evaluate_circuit_entry(snap, now_monotonic=129.999, policy=_POLICY).allowed is False
    assert evaluate_circuit_entry(snap, now_monotonic=130.0, policy=_POLICY).allowed is True


def test_clock_going_backwards_keeps_circuit_open() -> None:
    decision = evaluate_circuit_entry(_open_snapshot(100.0), now_monotonic=50.0, policy=_POLICY)
    assert decision.allowed is False and decision.retry_after_seconds == 30.0


def test_closed_failures_below_threshold_stay_closed_and_success_resets() -> None:
    snap = _fail(initial_circuit())
    assert snap.state == "CLOSED" and snap.consecutive_failures == 1
    assert _ok(snap) == initial_circuit()


def test_threshold_failure_opens_with_timestamp() -> None:
    snap = _fail(_fail(initial_circuit(), 10.0), 11.0)
    assert snap.state == "OPEN" and snap.opened_at_monotonic == 11.0


def test_latency_exactly_at_timeout_is_success_above_is_failure() -> None:
    at = record_circuit_outcome(
        initial_circuit(), succeeded=True, latency_seconds=15.0, now_monotonic=1.0, policy=_POLICY
    )
    over = record_circuit_outcome(
        initial_circuit(), succeeded=True, latency_seconds=15.001, now_monotonic=1.0, policy=_POLICY
    )
    assert at.consecutive_failures == 0 and over.consecutive_failures == 1


def test_status_429_and_500_fail_but_499_and_404_do_not() -> None:
    def code(c):
        return record_circuit_outcome(
            initial_circuit(), succeeded=True, latency_seconds=0.1, status_code=c, now_monotonic=1.0, policy=_POLICY
        ).consecutive_failures

    assert code(429) == 1 and code(500) == 1
    assert code(404) == 0 and code(499) == 0


def test_nan_or_negative_latency_counts_as_failure() -> None:
    for bad in (math.nan, math.inf):
        snap = record_circuit_outcome(
            initial_circuit(), succeeded=True, latency_seconds=bad, now_monotonic=1.0, policy=_POLICY
        )
        assert snap.consecutive_failures == 1


def test_half_open_failure_reopens_and_probe_saturation_blocks() -> None:
    half = CircuitSnapshot("HALF_OPEN", 0, 100.0, 0, 2)
    blocked = evaluate_circuit_entry(half, now_monotonic=140.0, policy=_POLICY)
    assert blocked.allowed is False and blocked.reason_code == "HALF_OPEN_PROBE_SATURATED"
    reopened = _fail(half, now=141.0)
    assert reopened.state == "OPEN" and reopened.opened_at_monotonic == 141.0


def test_half_open_success_never_underflows_in_flight() -> None:
    half = CircuitSnapshot("HALF_OPEN", 0, 100.0, 0, 0)
    assert _ok(half).half_open_in_flight == 0


def test_invalid_policy_and_backoff_inputs_raise_own_error() -> None:
    with pytest.raises(CircuitBreakerError):
        compute_exponential_backoff_seconds(attempt=-1)
    with pytest.raises(CircuitBreakerError):
        compute_exponential_backoff_seconds(attempt=1, base_seconds=math.nan)
    with pytest.raises(CircuitBreakerError):
        compute_exponential_backoff_seconds(attempt=1, max_seconds=-1.0)
    with pytest.raises(CircuitBreakerError):
        CircuitBreakerPolicy(failure_threshold=0)


def test_backoff_is_capped_deterministic_and_unjittered_without_seed() -> None:
    assert compute_exponential_backoff_seconds(attempt=50, base_seconds=1.0, max_seconds=60.0) == 60.0
    assert compute_exponential_backoff_seconds(attempt=2, base_seconds=1.0, max_seconds=60.0) == 4.0
    a = compute_exponential_backoff_seconds(attempt=4, jitter_seed="x")
    assert a == compute_exponential_backoff_seconds(attempt=4, jitter_seed="x")
    assert 8.0 <= a <= 16.0
