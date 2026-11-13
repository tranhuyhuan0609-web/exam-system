"""Pure token-bucket rate limiter and Idempotency-Key request guard (V11.3 platform kernel)."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{8,128}", re.ASCII)
MAX_IDEMPOTENCY_RECORDS = 100_000
DEFAULT_PENDING_TTL_SECONDS = 60.0


class RateLimitError(ValueError):
    """Raised on invalid rate-limit rules, costs or clock values."""


class RateLimitCostError(RateLimitError):
    """Raised when a single request costs more than the bucket capacity (can never succeed)."""


class IdempotencyError(ValueError):
    """Raised on missing/malformed Idempotency-Key headers or invalid guard input."""


class IdempotencyMismatchError(IdempotencyError):
    """Raised when an Idempotency-Key is reused with a different request payload."""


class IdempotencyOutcome(StrEnum):
    EXECUTE_NEW = "EXECUTE_NEW"
    REPLAY_CACHED = "REPLAY_CACHED"
    IN_PROGRESS = "IN_PROGRESS"


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


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def evaluate_token_bucket(
    *,
    rule: RateLimitRule,
    state: TokenBucketState | None,
    now_seconds: float,
    cost: float = 1.0,
) -> RateLimitDecision:
    """Evaluate a monotonic-clock token bucket check cleanly with Retry-After headers."""
    if not all(_is_finite_number(n) for n in (rule.capacity, rule.refill_per_second, cost, now_seconds)):
        raise RateLimitError("rule, cost and clock values must be finite numbers")
    if rule.capacity <= 0 or rule.refill_per_second <= 0 or cost <= 0:
        raise RateLimitError("rule capacity, refill_per_second, and cost must be positive")
    if cost > rule.capacity:
        raise RateLimitCostError("request cost exceeds bucket capacity and can never be admitted")

    if state is None:
        available = rule.capacity
    else:
        if not (_is_finite_number(state.tokens) and _is_finite_number(state.last_refill_at)) or state.tokens < 0:
            raise RateLimitError("invalid bucket state")
        elapsed = max(0.0, now_seconds - state.last_refill_at)
        available = min(rule.capacity, state.tokens + elapsed * rule.refill_per_second)
        # never move the refill clock backwards if the caller's clock jumped
        now_seconds = max(now_seconds, state.last_refill_at)

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
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise IdempotencyError("missing Idempotency-Key header")
    cleaned = raw_key.strip()
    if _IDEMPOTENCY_KEY_RE.fullmatch(cleaned) is None:
        raise IdempotencyError("Idempotency-Key must be 8..128 characters of [A-Za-z0-9._:-]")
    return cleaned


def compute_request_fingerprint(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | list[Any] | str | None = None,
) -> str:
    """Compute canonical SHA-256 digest over HTTP method, path, and normalized JSON body."""
    if not isinstance(method, str) or not isinstance(path, str) or not method.strip() or not path.strip():
        raise IdempotencyError("method and path must be non-empty strings")
    try:
        canonical = json.dumps(
            {
                "method": method.strip().upper(),
                "path": path.strip(),
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as exc:  # UnicodeEncodeError is a ValueError
        raise IdempotencyError("payload is not canonical JSON") from exc


@dataclass(frozen=True)
class CachedIdempotencyRecord:
    scoped_key: str
    fingerprint: str
    status_code: int | None
    response_body: dict[str, Any] | None
    expires_at: float
    in_progress: bool = False


def _require_id(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdempotencyError(f"{label} must be a non-empty string")
    return value


def _fingerprints_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _require_fingerprint(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise IdempotencyError("fingerprint must be a non-empty string")
    return value


def _require_now(value: object) -> float:
    if not _is_finite_number(value):
        raise IdempotencyError("now_seconds must be finite")
    return float(value)  # type: ignore[arg-type]


class IdempotencyGuard:
    """Atomic key-and-fingerprint guard preventing duplicate execution or silent key reuse.

    ``check_or_replay`` reserves the key in the same locked step that reports EXECUTE_NEW, so two
    concurrent identical requests cannot both execute: the second one gets IN_PROGRESS. Call
    ``store`` to complete the reservation or ``release`` to abandon it after a failure.
    """

    def __init__(
        self,
        ttl_seconds: float = 86_400.0,
        *,
        pending_ttl_seconds: float = DEFAULT_PENDING_TTL_SECONDS,
        max_records: int = MAX_IDEMPOTENCY_RECORDS,
    ) -> None:
        for value in (ttl_seconds, pending_ttl_seconds):
            if not _is_finite_number(value) or value <= 0:
                raise IdempotencyError("ttl_seconds must be a positive finite number")
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise IdempotencyError("max_records must be a positive integer")
        self._ttl_seconds = float(ttl_seconds)
        self._pending_ttl_seconds = float(pending_ttl_seconds)
        self._max_records = max_records
        self._records: dict[tuple[str, str, str], CachedIdempotencyRecord] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    @staticmethod
    def _scope(*, tenant_id: str, actor_id: str, key: str) -> tuple[str, str, str]:
        return (_require_id("tenant_id", tenant_id), _require_id("actor_id", actor_id), key)

    @staticmethod
    def _scoped_label(scope: tuple[str, str, str]) -> str:
        # Length-prefixed so that ids containing ":" can never collide.
        return "|".join(f"{len(part)}:{part}" for part in scope)

    def _evict_expired(self, now_seconds: float) -> None:
        expired = [k for k, rec in self._records.items() if now_seconds >= rec.expires_at]
        for k in expired:
            del self._records[k]

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
        scope = self._scope(tenant_id=tenant_id, actor_id=actor_id, key=valid_key)
        fingerprint = _require_fingerprint(fingerprint)
        now = _require_now(now_seconds)
        with self._lock:
            self._evict_expired(now)
            existing = self._records.get(scope)
            if existing is None:
                if len(self._records) >= self._max_records:
                    raise IdempotencyError("idempotency registry is full")
                self._records[scope] = CachedIdempotencyRecord(
                    scoped_key=self._scoped_label(scope),
                    fingerprint=fingerprint,
                    status_code=None,
                    response_body=None,
                    expires_at=now + self._pending_ttl_seconds,
                    in_progress=True,
                )
                return IdempotencyOutcome.EXECUTE_NEW, None
            if not _fingerprints_equal(existing.fingerprint, fingerprint):
                raise IdempotencyMismatchError("Idempotency-Key reused with a different request payload")
            if existing.in_progress:
                return IdempotencyOutcome.IN_PROGRESS, None
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
        scope = self._scope(tenant_id=tenant_id, actor_id=actor_id, key=valid_key)
        if isinstance(status_code, bool) or not isinstance(status_code, int) or not 100 <= status_code <= 599:
            raise IdempotencyError("status_code must be an HTTP status")
        fingerprint = _require_fingerprint(fingerprint)
        now = _require_now(now_seconds)
        rec = CachedIdempotencyRecord(
            scoped_key=self._scoped_label(scope),
            fingerprint=fingerprint,
            status_code=status_code,
            response_body=copy.deepcopy(dict(response_body)),
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            existing = self._records.get(scope)
            if existing is not None and not _fingerprints_equal(existing.fingerprint, fingerprint):
                raise IdempotencyMismatchError("Idempotency-Key reused with a different request payload")
            if existing is None:
                self._evict_expired(now)
                if len(self._records) >= self._max_records:
                    raise IdempotencyError("idempotency registry is full")
            self._records[scope] = rec
        return rec

    def release(self, *, tenant_id: str, actor_id: str, idempotency_key: str) -> None:
        """Abandon an IN_PROGRESS reservation after a failed execution so a retry can run."""
        valid_key = validate_idempotency_key(idempotency_key)
        scope = self._scope(tenant_id=tenant_id, actor_id=actor_id, key=valid_key)
        with self._lock:
            existing = self._records.get(scope)
            if existing is not None and existing.in_progress:
                del self._records[scope]
