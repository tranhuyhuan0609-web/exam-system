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
    assert (
        compute_exponential_backoff_seconds(attempt=3, base_seconds=2.0, max_seconds=30.0, jitter_seed="evt-1")
        == d3
    )
