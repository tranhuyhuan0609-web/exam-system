"""Tests for app.platform.rate_limit token bucket and idempotency replay guard."""

from __future__ import annotations

import pytest

from app.platform.rate_limit import (
    CRAWLER_ORIGIN_RULE,
    IdempotencyError,
    IdempotencyGuard,
    IdempotencyMismatchError,
    IdempotencyOutcome,
    RateLimitRule,
    compute_request_fingerprint,
    evaluate_token_bucket,
    validate_idempotency_key,
)


def test_crawler_origin_rate_limit_enforces_1_req_per_3s_and_refills() -> None:
    d1 = evaluate_token_bucket(rule=CRAWLER_ORIGIN_RULE, state=None, now_seconds=100.0)
    assert d1.allowed is True

    d2 = evaluate_token_bucket(rule=CRAWLER_ORIGIN_RULE, state=d1.next_state, now_seconds=101.0)
    assert d2.allowed is False
    assert d2.retry_after_seconds == 2
    assert d2.headers["Retry-After"] == "2"

    d3 = evaluate_token_bucket(rule=CRAWLER_ORIGIN_RULE, state=d2.next_state, now_seconds=103.5)
    assert d3.allowed is True

    burst = evaluate_token_bucket(
        rule=RateLimitRule("burst", capacity=5, refill_per_second=1),
        state=None,
        now_seconds=0.0,
        cost=2.0,
    )
    assert burst.allowed is True
    assert burst.remaining == 3


def test_idempotency_guard_replays_matching_payload_and_blocks_mismatch() -> None:
    guard = IdempotencyGuard(ttl_seconds=60.0)
    fp1 = compute_request_fingerprint(
        method="POST",
        path="/api/v1/imports",
        payload={"sha256": "abc", "source_id": "src-1"},
    )
    fp_same = compute_request_fingerprint(
        method="post",
        path="/api/v1/imports",
        payload={"source_id": "src-1", "sha256": "abc"},
    )
    fp_diff = compute_request_fingerprint(
        method="POST",
        path="/api/v1/imports",
        payload={"sha256": "DIFFERENT", "source_id": "src-1"},
    )
    assert fp1 == fp_same

    outcome, rec = guard.check_or_replay(
        tenant_id="t1",
        actor_id="u1",
        idempotency_key="idem-key-0001",
        fingerprint=fp1,
        now_seconds=1000.0,
    )
    assert outcome == IdempotencyOutcome.EXECUTE_NEW
    assert rec is None

    guard.store(
        tenant_id="t1",
        actor_id="u1",
        idempotency_key="idem-key-0001",
        fingerprint=fp1,
        status_code=201,
        response_body={"job_id": "job-9"},
        now_seconds=1000.0,
    )

    replay_outcome, replay_rec = guard.check_or_replay(
        tenant_id="t1",
        actor_id="u1",
        idempotency_key="idem-key-0001",
        fingerprint=fp_same,
        now_seconds=1010.0,
    )
    assert replay_outcome == IdempotencyOutcome.REPLAY_CACHED
    assert replay_rec is not None and replay_rec.response_body["job_id"] == "job-9"

    with pytest.raises(IdempotencyMismatchError):
        guard.check_or_replay(
            tenant_id="t1",
            actor_id="u1",
            idempotency_key="idem-key-0001",
            fingerprint=fp_diff,
            now_seconds=1015.0,
        )

    # After TTL expires, same key may execute cleanly again
    expired_outcome, _ = guard.check_or_replay(
        tenant_id="t1",
        actor_id="u1",
        idempotency_key="idem-key-0001",
        fingerprint=fp_diff,
        now_seconds=1061.0,
    )
    assert expired_outcome == IdempotencyOutcome.EXECUTE_NEW

    with pytest.raises(IdempotencyError):
        validate_idempotency_key("bad")
