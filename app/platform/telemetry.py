"""Zero-PII structured telemetry logger and V11.3 production SLO threshold evaluator."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

_SENSITIVE_FIELD_KEYS: frozenset[str] = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "full_name",
        "student_name",
        "parent_name",
        "cccd",
        "cmnd",
        "address",
        "password",
        "secret",
        "token",
        "authorization",
        "cookie",
        "api_key",
    }
)

_JWT_VALUE_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*\b")
_EMAIL_VALUE_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_VN_PHONE_RE = re.compile(r"(?<!\d)(?:\+84|0)[35789]\d{8}(?!\d)")


@dataclass(frozen=True)
class SloTarget:
    operation: str
    max_p50_ms: float | None
    max_p95_ms: float
    max_p99_ms: float | None = None
    max_error_rate: float = 0.001


V11_SLO_TARGETS: dict[str, SloTarget] = {
    "autosave_db_commit": SloTarget(
        operation="autosave_db_commit",
        max_p50_ms=25.0,
        max_p95_ms=120.0,
        max_p99_ms=300.0,
        max_error_rate=0.0001,
    ),
    "exam_read_api": SloTarget(
        operation="exam_read_api",
        max_p50_ms=80.0,
        max_p95_ms=250.0,
        max_error_rate=0.0005,
    ),
    "submit_api": SloTarget(
        operation="submit_api",
        max_p50_ms=None,
        max_p95_ms=250.0,
        max_error_rate=0.0001,
    ),
}


@dataclass(frozen=True)
class SloEvaluationReport:
    operation: str
    sample_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_rate: float
    passed: bool
    violations: tuple[str, ...]


def _scrub_string(value: str) -> str:
    masked = _JWT_VALUE_RE.sub("[REDACTED_JWT]", value)
    masked = _EMAIL_VALUE_RE.sub("[REDACTED_EMAIL]", masked)
    masked = _VN_PHONE_RE.sub("[REDACTED_PHONE]", masked)
    return masked


def redact_telemetry_value(value: Any, *, key_hint: str = "") -> Any:
    """Recursively scrub PII and credential tokens from telemetry attributes."""
    if key_hint.lower() in _SENSITIVE_FIELD_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: redact_telemetry_value(v, key_hint=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_telemetry_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def format_structured_log_event(
    *,
    level: str,
    event: str,
    correlation_id: str,
    tenant_id: str | None = None,
    module: str | None = None,
    duration_ms: float | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Serialize one JSON telemetry event guaranteed free of PII or bearer secrets."""
    payload: dict[str, Any] = {
        "level": level.upper(),
        "event": event,
        "correlation_id": correlation_id,
    }
    if tenant_id:
        payload["tenant_id"] = tenant_id
    if module:
        payload["module"] = module
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 3)
    if attributes:
        payload["attributes"] = redact_telemetry_value(attributes)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_percentile(sorted_values: list[float], pct: float) -> float:
    """Compute nearest-rank percentile on a non-empty ascending list of measurements."""
    if not sorted_values:
        raise ValueError("measurements cannot be empty")
    rank = max(1, math.ceil((pct / 100.0) * len(sorted_values)))
    return float(sorted_values[rank - 1])


def evaluate_slo_compliance(
    *,
    operation: str,
    latencies_ms: list[float],
    failed_count: int = 0,
    target: SloTarget | None = None,
) -> SloEvaluationReport:
    """Verify measured latencies & error rate against V11.3 Chapter 9 SLO contracts."""
    if not latencies_ms:
        raise ValueError("latencies_ms must contain at least one sample")
    if failed_count < 0 or failed_count > len(latencies_ms):
        raise ValueError("failed_count out of range")

    slo = target or V11_SLO_TARGETS[operation]
    ordered = sorted(latencies_ms)
    p50 = round(compute_percentile(ordered, 50.0), 3)
    p95 = round(compute_percentile(ordered, 95.0), 3)
    p99 = round(compute_percentile(ordered, 99.0), 3)
    err_rate = round(failed_count / len(ordered), 6)

    violations: list[str] = []
    if slo.max_p50_ms is not None and p50 >= slo.max_p50_ms:
        violations.append(f"p50 {p50}ms >= target {slo.max_p50_ms}ms")
    if p95 >= slo.max_p95_ms:
        violations.append(f"p95 {p95}ms >= target {slo.max_p95_ms}ms")
    if slo.max_p99_ms is not None and p99 >= slo.max_p99_ms:
        violations.append(f"p99 {p99}ms >= target {slo.max_p99_ms}ms")
    if err_rate >= slo.max_error_rate:
        violations.append(f"error_rate {err_rate} >= target {slo.max_error_rate}")

    return SloEvaluationReport(
        operation=operation,
        sample_count=len(ordered),
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        error_rate=err_rate,
        passed=not violations,
        violations=tuple(violations),
    )
