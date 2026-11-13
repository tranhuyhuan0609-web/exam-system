"""Pure token-bucket rate limiter and Idempotency-Key request guard (V11.3 platform kernel)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class IdempotencyError(ValueError):
    """Raised on missing/malformed Idempotency-Key headers."""


class IdempotencyMismatchError(ValueError):
    """Raised when an Idempotency-Key is reused with a different request payload."""


class IdempotencyOutcome(StrEnum):
    EXECUTE_NEW = "EXECUTE_NEW"
    REPLAY_CACHED = "REPLAY_CACHED"


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    capacity: float
    refill_per_second: float


# Standard V11.3 baseline rules
CRAWLER_ORIGIN_RULE = RateLimitRule(name="crawler_origin", capacity=1.0, refill_per_second=1.0 / 3.0)
AUTH_LOGIN_RULE = RateLimitRule(name="auth_login", capacity=10.0, refill_per_second=10.0 / 60.0)
AUTOSAVE_BURST_RULE = RateLimitRule(name="exam_autosave", capacity=30.0, refill_per_second=5.0)


@dataclass(frozen=True)
class TokenBucketState:
    tokens: float
    last_refill_at: float


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    next_state: TokenBucketState
    headers: dict[str, str]


def evaluate_token_bucket(
    *,
    rule: RateLimitRule,
    state: TokenBucketState | None,
    now_seconds: float,
    cost: float = 1.0,
) -> RateLimitDecision:
    """Evaluate a monotonic-clock token bucket check cleanly with Retry-After headers."""
    if rule.capacity <= 0 or rule.refill_per_second <= 0 or cost <= 0:
        raise ValueError("rule capacity, refill_per_second, and cost must be positive")

    if state is None:
        available = rule.capacity
    else:
        elapsed = max(0.0, now_seconds - state.last_refill_at)
        available = min(rule.capacity, state.tokens + elapsed * rule.refill_per_second)

    if available >= cost:
        leftover = available - cost
        next_state = TokenBucketState(tokens=leftover, last_refill_at=now_seconds)
        remaining = max(0, int(math.floor(leftover)))
        return RateLimitDecision(
            allowed=True,
            remaining=remaining,
            retry_after_seconds=0,
            next_state=next_state,
            headers={
                "X-RateLimit-Limit": str(int(rule.capacity)),
                "X-RateLimit-Remaining": str(remaining),
            },
        )

    deficit = cost - available
    wait_exact = deficit / rule.refill_per_second
    retry_after = max(1, int(math.ceil(wait_exact - 1e-9)))
    next_state = TokenBucketState(tokens=available, last_refill_at=now_seconds)
    return RateLimitDecision(
        allowed=False,
        remaining=0,
        retry_after_seconds=retry_after,
        next_state=next_state,
        headers={
            "X-RateLimit-Limit": str(int(rule.capacity)),
            "X-RateLimit-Remaining": "0",
            "Retry-After": str(retry_after),
        },
    )


def validate_idempotency_key(raw_key: str | None) -> str:
    """Ensure Idempotency-Key has safe ASCII characters and 8..128 length."""
    if not raw_key or not raw_key.strip():
        raise IdempotencyError("missing Idempotency-Key header")
    cleaned = raw_key.strip()
    if not _IDEMPOTENCY_KEY_RE.match(cleaned):
        raise IdempotencyError("Idempotency-Key must be 8..128 characters of [A-Za-z0-9._:-]")
    return cleaned


def compute_request_fingerprint(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | list[Any] | str | None = None,
) -> str:
    """Compute canonical SHA-256 digest over HTTP method, path, and normalized JSON body."""
    canonical = json.dumps(
        {
            "method": method.strip().upper(),
            "path": path.strip(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedIdempotencyRecord:
    scoped_key: str
    fingerprint: str
    status_code: int
    response_body: dict[str, Any]
    expires_at: float


class IdempotencyGuard:
    """Deterministic key-and-fingerprint guard preventing duplicate execution or silent key reuse."""

    def __init__(self, ttl_seconds: float = 86_400.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._records: dict[str, CachedIdempotencyRecord] = {}

    def _scope(self, *, tenant_id: str, actor_id: str, key: str) -> str:
        return f"{tenant_id}:{actor_id}:{key}"

    def check_or_replay(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        fingerprint: str,
        now_seconds: float,
    ) -> tuple[IdempotencyOutcome, CachedIdempotencyRecord | None]:
        valid_key = validate_idempotency_key(idempotency_key)
        scoped = self._scope(tenant_id=tenant_id, actor_id=actor_id, key=valid_key)
        existing = self._records.get(scoped)
        if existing is None:
            return IdempotencyOutcome.EXECUTE_NEW, None
        if now_seconds >= existing.expires_at:
            del self._records[scoped]
            return IdempotencyOutcome.EXECUTE_NEW, None
        if existing.fingerprint != fingerprint:
            raise IdempotencyMismatchError(
                f"Idempotency-Key {valid_key!r} reused with a different request payload"
            )
        return IdempotencyOutcome.REPLAY_CACHED, existing

    def store(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        idempotency_key: str,
        fingerprint: str,
        status_code: int,
        response_body: dict[str, Any],
        now_seconds: float,
    ) -> CachedIdempotencyRecord:
        valid_key = validate_idempotency_key(idempotency_key)
        scoped = self._scope(tenant_id=tenant_id, actor_id=actor_id, key=valid_key)
        rec = CachedIdempotencyRecord(
            scoped_key=scoped,
            fingerprint=fingerprint,
            status_code=status_code,
            response_body=dict(response_body),
            expires_at=now_seconds + self._ttl_seconds,
        )
        self._records[scoped] = rec
        return rec
