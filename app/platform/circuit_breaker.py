"""E01 / G03 / RB-08 pure Circuit Breaker state machine & retry backoff policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

CircuitState = Literal["CLOSED", "OPEN", "HALF_OPEN"]

DEFAULT_LLM_TIMEOUT_SECONDS = 15.0  # V11.3 Chapter 3 E01 / RB-08 threshold


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    half_open_max_probes: int = 2
    timeout_threshold_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    opened_at_monotonic: float | None
    half_open_successes: int
    half_open_in_flight: int


@dataclass(frozen=True)
class AllowDecision:
    allowed: bool
    next_snapshot: CircuitSnapshot
    retry_after_seconds: float
    reason_code: str


def initial_circuit() -> CircuitSnapshot:
    return CircuitSnapshot(
        state="CLOSED",
        consecutive_failures=0,
        opened_at_monotonic=None,
        half_open_successes=0,
        half_open_in_flight=0,
    )


def evaluate_circuit_entry(
    snapshot: CircuitSnapshot,
    *,
    now_monotonic: float,
    policy: CircuitBreakerPolicy | None = None,
) -> AllowDecision:
    """Decide whether an outbound LLM/storage/webhook call may enter the circuit."""
    cfg = policy or CircuitBreakerPolicy()

    if snapshot.state == "CLOSED":
        return AllowDecision(
            allowed=True,
            next_snapshot=snapshot,
            retry_after_seconds=0.0,
            reason_code="CIRCUIT_CLOSED",
        )

    if snapshot.state == "OPEN":
        opened_at = snapshot.opened_at_monotonic or now_monotonic
        elapsed = max(0.0, now_monotonic - opened_at)
        if elapsed < cfg.cooldown_seconds:
            return AllowDecision(
                allowed=False,
                next_snapshot=snapshot,
                retry_after_seconds=round(cfg.cooldown_seconds - elapsed, 3),
                reason_code="CIRCUIT_OPEN",
            )
        half_open = CircuitSnapshot(
            state="HALF_OPEN",
            consecutive_failures=0,
            opened_at_monotonic=opened_at,
            half_open_successes=0,
            half_open_in_flight=1,
        )
        return AllowDecision(
            allowed=True,
            next_snapshot=half_open,
            retry_after_seconds=0.0,
            reason_code="HALF_OPEN_PROBE",
        )

    # HALF_OPEN
    if snapshot.half_open_in_flight >= cfg.half_open_max_probes:
        return AllowDecision(
            allowed=False,
            next_snapshot=snapshot,
            retry_after_seconds=1.0,
            reason_code="HALF_OPEN_PROBE_SATURATED",
        )

    next_snap = CircuitSnapshot(
        state="HALF_OPEN",
        consecutive_failures=snapshot.consecutive_failures,
        opened_at_monotonic=snapshot.opened_at_monotonic,
        half_open_successes=snapshot.half_open_successes,
        half_open_in_flight=snapshot.half_open_in_flight + 1,
    )
    return AllowDecision(
        allowed=True,
        next_snapshot=next_snap,
        retry_after_seconds=0.0,
        reason_code="HALF_OPEN_PROBE",
    )


def record_circuit_outcome(
    snapshot: CircuitSnapshot,
    *,
    succeeded: bool,
    latency_seconds: float,
    status_code: int | None = None,
    now_monotonic: float,
    policy: CircuitBreakerPolicy | None = None,
) -> CircuitSnapshot:
    """Record call result; treats >15s timeout or 429/5xx HTTP statuses as trip failures."""
    cfg = policy or CircuitBreakerPolicy()
    is_failure = (
        not succeeded
        or latency_seconds > cfg.timeout_threshold_seconds
        or (status_code is not None and (status_code == 429 or status_code >= 500))
    )

    if not is_failure:
        if snapshot.state == "HALF_OPEN":
            successes = snapshot.half_open_successes + 1
            in_flight = max(0, snapshot.half_open_in_flight - 1)
            if successes >= cfg.half_open_max_probes:
                return initial_circuit()
            return CircuitSnapshot(
                state="HALF_OPEN",
                consecutive_failures=0,
                opened_at_monotonic=snapshot.opened_at_monotonic,
                half_open_successes=successes,
                half_open_in_flight=in_flight,
            )
        return initial_circuit()

    # Failure branch
    if snapshot.state == "HALF_OPEN":
        return CircuitSnapshot(
            state="OPEN",
            consecutive_failures=cfg.failure_threshold,
            opened_at_monotonic=now_monotonic,
            half_open_successes=0,
            half_open_in_flight=0,
        )

    failures = snapshot.consecutive_failures + 1
    if failures >= cfg.failure_threshold:
        return CircuitSnapshot(
            state="OPEN",
            consecutive_failures=failures,
            opened_at_monotonic=now_monotonic,
            half_open_successes=0,
            half_open_in_flight=0,
        )
    return CircuitSnapshot(
        state="CLOSED",
        consecutive_failures=failures,
        opened_at_monotonic=snapshot.opened_at_monotonic,
        half_open_successes=0,
        half_open_in_flight=0,
    )


def compute_exponential_backoff_seconds(
    *,
    attempt: int,
    base_seconds: float = 1.0,
    max_seconds: float = 60.0,
    jitter_seed: str = "",
) -> float:
    """Compute bounded deterministic full-jitter backoff for G03 Outbox / E01 retries."""
    if attempt < 0:
        raise ValueError("attempt must be non-negative")
    cap = min(max_seconds, base_seconds * (2**min(attempt, 10)))
    if not jitter_seed:
        return round(cap, 3)
    digest = hashlib.sha256(f"{jitter_seed}:{attempt}".encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    # Preserve at least 50% of cap + deterministic up to cap to avoid thundering herd.
    delay = cap * (0.5 + 0.5 * fraction)
    return round(delay, 3)
