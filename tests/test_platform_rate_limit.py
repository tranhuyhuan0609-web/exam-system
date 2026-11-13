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


# --- regression + boundary tests --------------------------------------------------------------

import math  # noqa: E402
import threading  # noqa: E402

from app.platform.rate_limit import (  # noqa: E402
    RateLimitCostError,
    RateLimitError,
    TokenBucketState,
)

_RULE = RateLimitRule("t", capacity=5.0, refill_per_second=1.0)
_IDEM = {"tenant_id": "t1", "actor_id": "u1", "idempotency_key": "idem-key-0001"}


def test_cost_above_capacity_is_a_clear_refusal() -> None:
    with pytest.raises(RateLimitCostError):
        evaluate_token_bucket(rule=_RULE, state=None, now_seconds=0.0, cost=5.5)
    # exactly capacity is allowed and drains the bucket
    d = evaluate_token_bucket(rule=_RULE, state=None, now_seconds=0.0, cost=5.0)
    assert d.allowed and d.remaining == 0


def test_bucket_refill_boundaries() -> None:
    drained = evaluate_token_bucket(rule=_RULE, state=None, now_seconds=10.0, cost=5.0).next_state
    at_boundary = evaluate_token_bucket(rule=_RULE, state=drained, now_seconds=11.0)
    assert at_boundary.allowed is True  # exactly 1 token refilled
    early = evaluate_token_bucket(rule=_RULE, state=drained, now_seconds=10.999)
    assert early.allowed is False and early.retry_after_seconds == 1
    assert early.headers["Retry-After"] == "1"
    # refill is capped at capacity after a long idle period
    idle = evaluate_token_bucket(rule=_RULE, state=drained, now_seconds=10_000.0)
    assert idle.remaining == 4


def test_clock_going_backwards_does_not_mint_tokens_or_rewind_state() -> None:
    state = TokenBucketState(tokens=0.0, last_refill_at=100.0)
    d = evaluate_token_bucket(rule=_RULE, state=state, now_seconds=50.0)
    assert d.allowed is False
    assert d.next_state.last_refill_at == 100.0


def test_non_finite_or_invalid_inputs_raise_rate_limit_error() -> None:
    for kwargs in (
        dict(now_seconds=math.nan),
        dict(now_seconds=math.inf),
        dict(now_seconds=0.0, cost=math.nan),
        dict(now_seconds=0.0, cost=0.0),
        dict(now_seconds=0.0, cost=-1.0),
    ):
        with pytest.raises(RateLimitError):
            evaluate_token_bucket(rule=_RULE, state=None, **kwargs)
    for rule in (RateLimitRule("x", 0, 1), RateLimitRule("x", 1, 0), RateLimitRule("x", math.nan, 1)):
        with pytest.raises(RateLimitError):
            evaluate_token_bucket(rule=rule, state=None, now_seconds=0.0)
    with pytest.raises(RateLimitError):
        evaluate_token_bucket(rule=_RULE, state=TokenBucketState(math.nan, 0.0), now_seconds=1.0)


def test_check_is_atomic_second_identical_request_is_in_progress() -> None:
    guard = IdempotencyGuard()
    first = guard.check_or_replay(fingerprint="fp", now_seconds=1.0, **_IDEM)
    second = guard.check_or_replay(fingerprint="fp", now_seconds=1.0, **_IDEM)
    assert first[0] == IdempotencyOutcome.EXECUTE_NEW
    assert second[0] == IdempotencyOutcome.IN_PROGRESS
    guard.store(status_code=200, response_body={"ok": True}, fingerprint="fp", now_seconds=2.0, **_IDEM)
    third = guard.check_or_replay(fingerprint="fp", now_seconds=3.0, **_IDEM)
    assert third[0] == IdempotencyOutcome.REPLAY_CACHED


def test_concurrent_identical_requests_execute_exactly_once() -> None:
    guard = IdempotencyGuard()
    outcomes: list[IdempotencyOutcome] = []
    barrier = threading.Barrier(16)

    def worker() -> None:
        barrier.wait()
        outcomes.append(guard.check_or_replay(fingerprint="fp", now_seconds=1.0, **_IDEM)[0])

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count(IdempotencyOutcome.EXECUTE_NEW) == 1
    assert outcomes.count(IdempotencyOutcome.IN_PROGRESS) == 15


def test_release_lets_a_failed_execution_be_retried() -> None:
    guard = IdempotencyGuard()
    guard.check_or_replay(fingerprint="fp", now_seconds=1.0, **_IDEM)
    guard.release(tenant_id="t1", actor_id="u1", idempotency_key="idem-key-0001")
    assert guard.check_or_replay(fingerprint="fp", now_seconds=2.0, **_IDEM)[0] == IdempotencyOutcome.EXECUTE_NEW


def test_release_never_drops_a_completed_record() -> None:
    guard = IdempotencyGuard()
    guard.check_or_replay(fingerprint="fp", now_seconds=1.0, **_IDEM)
    guard.store(status_code=201, response_body={}, fingerprint="fp", now_seconds=1.0, **_IDEM)
    guard.release(tenant_id="t1", actor_id="u1", idempotency_key="idem-key-0001")
    assert guard.check_or_replay(fingerprint="fp", now_seconds=2.0, **_IDEM)[0] == IdempotencyOutcome.REPLAY_CACHED


def test_stale_pending_reservation_expires() -> None:
    guard = IdempotencyGuard(pending_ttl_seconds=10.0)
    guard.check_or_replay(fingerprint="fp", now_seconds=0.0, **_IDEM)
    assert guard.check_or_replay(fingerprint="fp", now_seconds=9.9, **_IDEM)[0] == IdempotencyOutcome.IN_PROGRESS
    assert guard.check_or_replay(fingerprint="fp", now_seconds=10.0, **_IDEM)[0] == IdempotencyOutcome.EXECUTE_NEW


def test_expired_records_are_evicted_without_looking_the_same_key_up_again() -> None:
    guard = IdempotencyGuard(ttl_seconds=10.0)
    for i in range(50):
        guard.store(
            tenant_id="t", actor_id="u", idempotency_key=f"idem-key-{i:04d}", fingerprint="fp",
            status_code=200, response_body={}, now_seconds=0.0,
        )
    assert len(guard) == 50
    guard.check_or_replay(tenant_id="t", actor_id="u", idempotency_key="idem-key-9999", fingerprint="fp", now_seconds=11.0)
    assert len(guard) == 1


def test_registry_capacity_is_bounded() -> None:
    guard = IdempotencyGuard(max_records=2)
    for i in range(2):
        guard.check_or_replay(tenant_id="t", actor_id="u", idempotency_key=f"idem-key-{i:04d}", fingerprint="fp", now_seconds=0.0)
    with pytest.raises(IdempotencyError, match="full"):
        guard.check_or_replay(tenant_id="t", actor_id="u", idempotency_key="idem-key-0003", fingerprint="fp", now_seconds=0.0)


def test_scope_with_colons_cannot_collide_across_tenants() -> None:
    guard = IdempotencyGuard()
    key = "idem-key-0001"
    a = guard.check_or_replay(tenant_id="a:b", actor_id="c", idempotency_key=key, fingerprint="fp1", now_seconds=0.0)
    b = guard.check_or_replay(tenant_id="a", actor_id="b:c", idempotency_key=key, fingerprint="fp2", now_seconds=0.0)
    assert a[0] == b[0] == IdempotencyOutcome.EXECUTE_NEW


def test_tenants_do_not_share_keys() -> None:
    guard = IdempotencyGuard()
    guard.check_or_replay(tenant_id="t1", actor_id="u", idempotency_key="idem-key-0001", fingerprint="fp", now_seconds=0.0)
    other = guard.check_or_replay(tenant_id="t2", actor_id="u", idempotency_key="idem-key-0001", fingerprint="fp", now_seconds=0.0)
    assert other[0] == IdempotencyOutcome.EXECUTE_NEW


def test_mismatch_error_does_not_echo_the_key() -> None:
    guard = IdempotencyGuard()
    guard.check_or_replay(fingerprint="fp1", now_seconds=0.0, **_IDEM)
    with pytest.raises(IdempotencyMismatchError) as info:
        guard.check_or_replay(fingerprint="fp2", now_seconds=0.0, **_IDEM)
    assert "idem-key-0001" not in str(info.value)


def test_idempotency_key_validation_boundaries() -> None:
    assert validate_idempotency_key("a" * 8) == "a" * 8
    assert validate_idempotency_key(" " + "a" * 128 + " ") == "a" * 128
    for bad in (None, "", "   ", "a" * 7, "a" * 129, "has space1", "new\nline-key", "keyékey1234", "a;b;c;d;e;f", 12345678):
        with pytest.raises(IdempotencyError):
            validate_idempotency_key(bad)  # type: ignore[arg-type]


def test_guard_rejects_bad_ids_fingerprints_clock_and_status() -> None:
    guard = IdempotencyGuard()
    with pytest.raises(IdempotencyError):
        guard.check_or_replay(tenant_id="", actor_id="u", idempotency_key="idem-key-0001", fingerprint="fp", now_seconds=0.0)
    with pytest.raises(IdempotencyError):
        guard.check_or_replay(tenant_id="t", actor_id="u", idempotency_key="idem-key-0001", fingerprint="", now_seconds=0.0)
    with pytest.raises(IdempotencyError):
        guard.check_or_replay(tenant_id="t", actor_id="u", idempotency_key="idem-key-0001", fingerprint="fp", now_seconds=math.nan)
    with pytest.raises(IdempotencyError):
        guard.store(status_code=42, response_body={}, fingerprint="fp", now_seconds=0.0, **_IDEM)
    for ttl in (0, -1, math.nan, math.inf):
        with pytest.raises(IdempotencyError):
            IdempotencyGuard(ttl_seconds=ttl)


def test_stored_response_is_isolated_from_caller_mutation() -> None:
    guard = IdempotencyGuard()
    body = {"nested": {"a": 1}}
    rec = guard.store(status_code=200, response_body=body, fingerprint="fp", now_seconds=0.0, **_IDEM)
    body["nested"]["a"] = 2
    assert rec.response_body == {"nested": {"a": 1}}


def test_fingerprint_is_stable_and_sensitive_to_method_path_payload() -> None:
    base = compute_request_fingerprint(method="POST", path="/a", payload={"x": 1})
    assert base == compute_request_fingerprint(method=" post ", path=" /a ", payload={"x": 1})
    assert base != compute_request_fingerprint(method="PUT", path="/a", payload={"x": 1})
    assert base != compute_request_fingerprint(method="POST", path="/b", payload={"x": 1})
    assert base != compute_request_fingerprint(method="POST", path="/a", payload={"x": 2})
    assert compute_request_fingerprint(method="GET", path="/a") == compute_request_fingerprint(method="GET", path="/a", payload=None)


def test_fingerprint_rejects_non_canonical_payloads() -> None:
    for payload in ({"x": math.nan}, {"x": object()}, {"x": "\ud800"}):
        with pytest.raises(IdempotencyError):
            compute_request_fingerprint(method="POST", path="/a", payload=payload)
    with pytest.raises(IdempotencyError):
        compute_request_fingerprint(method="", path="/a")
    with pytest.raises(IdempotencyError):
        compute_request_fingerprint(method="POST", path=None)  # type: ignore[arg-type]
