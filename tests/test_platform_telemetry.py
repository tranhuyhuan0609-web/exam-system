"""Tests for app.platform.telemetry zero-PII redaction and SLO threshold evaluator."""

from __future__ import annotations

import json

from app.platform.telemetry import (
    evaluate_slo_compliance,
    format_structured_log_event,
)


def test_structured_log_redacts_pii_and_jwt_strings_recursively() -> None:
    line = format_structured_log_event(
        level="info",
        event="autosave.committed",
        correlation_id="corr-12345678",
        tenant_id="tenant-a",
        module="attempts",
        duration_ms=14.25,
        attributes={
            "student_name": "Nguyen Van A",
            "email": "hs@school.edu.vn",
            "debug_msg": "user hs@school.edu.vn phone 0912345678 token eyJhbGciOiJIUzI1NiJ9.payload.sig",
            "attempt_id": "att-99",
        },
    )
    parsed = json.loads(line)
    attrs = parsed["attributes"]
    assert attrs["student_name"] == "[REDACTED]"
    assert attrs["email"] == "[REDACTED]"
    assert "0912345678" not in attrs["debug_msg"]
    assert "hs@school.edu.vn" not in attrs["debug_msg"]
    assert "eyJhbGci" not in attrs["debug_msg"]
    assert attrs["attempt_id"] == "att-99"


def test_slo_evaluator_passes_within_budget_and_flags_breaches() -> None:
    samples_ok = [10.0] * 95 + [100.0] * 5  # p50=10 (<25), p95=10 (<120), p99=100 (<300)
    ok = evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=samples_ok, failed_count=0)
    assert ok.passed is True
    assert ok.violations == ()

    samples_bad = [15.0] * 50 + [140.0] * 50  # p95=140 >= 120ms target
    bad = evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=samples_bad, failed_count=0)
    assert bad.passed is False
    assert any("p95" in v for v in bad.violations)
