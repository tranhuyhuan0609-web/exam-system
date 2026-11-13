"""Zero-PII structured telemetry logger and V11.3 production SLO threshold evaluator."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


class TelemetryError(ValueError):
    """Raised on invalid telemetry or SLO evaluation input."""


# Single-word markers matched against the word segments of a normalised key
# ("refreshToken" -> ["refresh", "token"]); "tokens_used" is deliberately NOT sensitive.
_SENSITIVE_SEGMENTS: frozenset[str] = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "cccd",
        "cmnd",
        "address",
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "jwt",
        "bearer",
        "credential",
        "credentials",
        "signature",
        "otp",
    }
)
# Multi-word markers matched against the compacted key ("session_id" -> "sessionid").
_SENSITIVE_COMPACT: tuple[str, ...] = (
    "apikey",
    "accesskey",
    "privatekey",
    "sessionid",
    "fullname",
    "studentname",
    "parentname",
    "teachername",
    "setcookie",
)

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
MAX_REDACTION_DEPTH = 16

_JWT_VALUE_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_EMAIL_VALUE_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_VN_PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)[\s.\-]?[35789](?:[\s.\-]?\d){8}(?!\d)")


@dataclass(frozen=True)
class SloTarget:
    operation: str
    max_p50_ms: float | None
    max_p95_ms: float
    max_p99_ms: float | None = None
    max_error_rate: float | None = None


# Only figures stated in docs/architecture/chapters/09_10_qa_k6_rls_tests_14_runbooks.md section 9.2
# (plus the steady-state error rate "< 0.01%" of load scenario 1, used for autosave).
V11_SLO_TARGETS: dict[str, SloTarget] = {
    # "Server DB Commit: p50 < 25ms, p95 < 120ms"
    "autosave_db_commit": SloTarget(
        operation="autosave_db_commit",
        max_p50_ms=25.0,
        max_p95_ms=120.0,
        max_error_rate=0.0001,
    ),
    # "End-to-End API: p95 < 300ms"
    "autosave_end_to_end": SloTarget(
        operation="autosave_end_to_end",
        max_p50_ms=None,
        max_p95_ms=300.0,
    ),
    # "p50 < 80ms, p95 < 250ms" for content read / exam delivery
    "exam_read_api": SloTarget(
        operation="exam_read_api",
        max_p50_ms=80.0,
        max_p95_ms=250.0,
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


def scrub_string(value: str) -> str:
    masked = _JWT_VALUE_RE.sub("[REDACTED_JWT]", value)
    masked = _EMAIL_VALUE_RE.sub("[REDACTED_EMAIL]", masked)
    masked = _VN_PHONE_RE.sub("[REDACTED_PHONE]", masked)
    return masked


def is_sensitive_key(key: str) -> bool:
    spaced = _CAMEL_RE.sub("_", key)
    segments = [seg for seg in _NON_ALNUM_RE.split(spaced.lower()) if seg]
    if any(seg in _SENSITIVE_SEGMENTS for seg in segments):
        return True
    compact = "".join(segments)
    return any(marker in compact for marker in _SENSITIVE_COMPACT)


def redact_telemetry_value(value: Any, *, key_hint: str = "", _depth: int = 0) -> Any:
    """Recursively scrub PII and credential tokens from telemetry attributes.

    The result only contains JSON-safe scalars, lists and dicts.
    """
    if key_hint and is_sensitive_key(key_hint):
        return "[REDACTED]"
    if _depth > MAX_REDACTION_DEPTH:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, dict):
        return {
            scrub_string(str(k)): redact_telemetry_value(v, key_hint=str(k), _depth=_depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_telemetry_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[BYTES]"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return scrub_string(str(value))


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
        "level": scrub_string(str(level).upper()),
        "event": scrub_string(str(event)),
        "correlation_id": scrub_string(str(correlation_id)),
    }
    if tenant_id:
        payload["tenant_id"] = scrub_string(str(tenant_id))
    if module:
        payload["module"] = scrub_string(str(module))
    if duration_ms is not None and math.isfinite(duration_ms):
        payload["duration_ms"] = round(duration_ms, 3)
    if attributes:
        payload["attributes"] = redact_telemetry_value(attributes)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def compute_percentile(sorted_values: list[float], pct: float) -> float:
    """Compute nearest-rank percentile on a non-empty ascending list of measurements."""
    if not sorted_values:
        raise TelemetryError("measurements cannot be empty")
    if not math.isfinite(pct) or pct < 0.0 or pct > 100.0:
        raise TelemetryError("percentile must be within 0..100")
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
        raise TelemetryError("latencies_ms must contain at least one sample")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in latencies_ms):
        raise TelemetryError("latencies_ms must be finite and non-negative")
    if isinstance(failed_count, bool) or not isinstance(failed_count, int):
        raise TelemetryError("failed_count must be an integer")
    if failed_count < 0 or failed_count > len(latencies_ms):
        raise TelemetryError("failed_count out of range")

    slo = target or V11_SLO_TARGETS.get(operation)
    if slo is None:
        raise TelemetryError("unknown SLO operation")
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
    if slo.max_error_rate is not None and err_rate >= slo.max_error_rate:
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
