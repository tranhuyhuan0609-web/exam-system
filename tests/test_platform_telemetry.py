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


# --- regression + boundary tests --------------------------------------------------------------

import math  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from app.platform.telemetry import (  # noqa: E402
    V11_SLO_TARGETS,
    SloTarget,
    TelemetryError,
    compute_percentile,
    redact_telemetry_value,
)


@pytest.mark.parametrize(
    "key",
    [
        "access_token", "refreshToken", "accessToken", "session_id", "sessionId", "Authorization",
        "X-Api-Key", "apiKey", "client_secret", "password_hash", "Set-Cookie", "private_key",
        "full_name", "fullName", "student_name", "id_token", "user_email", "mobile_phone",
    ],
)
def test_secret_like_keys_are_redacted_regardless_of_case_and_shape(key: str) -> None:
    assert redact_telemetry_value({key: "sensitive-value"}) == {key: "[REDACTED]"}


@pytest.mark.parametrize("key", ["tokens_used", "attempt_id", "duration", "status", "tenant_id", "count"])
def test_ordinary_keys_are_not_over_redacted(key: str) -> None:
    assert redact_telemetry_value({key: "visible"}) == {key: "visible"}


@pytest.mark.parametrize(
    "phone",
    ["+84 912 345 678", "+84912345678", "0912.345.678", "091-234-5678", "0912345678", "84 912 345 678"],
)
def test_vietnamese_phone_formats_are_scrubbed_from_free_text(phone: str) -> None:
    out = redact_telemetry_value(f"call {phone} now")
    assert "345" not in out and "[REDACTED_PHONE]" in out


def test_phone_scrub_does_not_eat_unrelated_long_numbers() -> None:
    assert redact_telemetry_value("order 20261113999") == "order 20261113999"


def test_redaction_reaches_dicts_nested_in_lists_tuples_and_sets() -> None:
    value = {"rows": [{"password": "p"}], "pair": ({"token": "t"},), "emails": ["a@b.vn"]}
    out = redact_telemetry_value(value)
    assert out["rows"] == [{"password": "[REDACTED]"}]
    assert out["pair"] == [{"token": "[REDACTED]"}]
    assert out["emails"] == ["[REDACTED_EMAIL]"]


def test_list_under_sensitive_key_is_redacted_wholesale() -> None:
    assert redact_telemetry_value({"tokens": ["a"], "password": ["x", "y"]})["password"] == "[REDACTED]"


def test_deep_nesting_is_truncated() -> None:
    deep: dict = {"v": "x"}
    for _ in range(50):
        deep = {"n": deep}
    assert "[DEPTH_LIMIT]" in json.dumps(redact_telemetry_value(deep))


def test_datetime_decimal_and_object_attributes_do_not_break_the_logger() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird user@example.com"

    line = format_structured_log_event(
        level="info",
        event="x.y",
        correlation_id="corr-12345678",
        attributes={
            "at": datetime(2026, 11, 13, tzinfo=timezone.utc),
            "day": date(2026, 11, 13),
            "amount": Decimal("1.5"),
            "obj": Weird(),
            "raw": b"bytes",
            "nan": math.nan,
        },
    )
    parsed = json.loads(line)["attributes"]
    assert parsed["at"].startswith("2026-11-13")
    assert "user@example.com" not in line
    assert parsed["raw"] == "[BYTES]"
    assert parsed["nan"] is None


def test_non_finite_duration_is_dropped_and_output_is_strict_json() -> None:
    line = format_structured_log_event(level="warn", event="e", correlation_id="corr-12345678", duration_ms=math.inf)
    assert "duration_ms" not in json.loads(line)
    assert "Infinity" not in line and "NaN" not in line


def test_pii_in_event_and_ids_is_scrubbed_and_keys_are_scrubbed() -> None:
    line = format_structured_log_event(
        level="error", event="failed for a@b.vn", correlation_id="corr-12345678", attributes={"a@b.vn": 1}
    )
    assert "a@b.vn" not in line


def test_jwt_inside_nested_text_is_masked() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc-DEF_1"
    assert "eyJ" not in redact_telemetry_value({"a": [f"Bearer {token}"]})["a"][0]


def test_slo_table_contains_only_spec_9_2_figures() -> None:
    assert set(V11_SLO_TARGETS) == {"autosave_db_commit", "autosave_end_to_end", "exam_read_api"}
    db = V11_SLO_TARGETS["autosave_db_commit"]
    assert (db.max_p50_ms, db.max_p95_ms, db.max_p99_ms) == (25.0, 120.0, None)
    e2e = V11_SLO_TARGETS["autosave_end_to_end"]
    assert (e2e.max_p50_ms, e2e.max_p95_ms, e2e.max_p99_ms) == (None, 300.0, None)
    read = V11_SLO_TARGETS["exam_read_api"]
    assert (read.max_p50_ms, read.max_p95_ms, read.max_p99_ms) == (80.0, 250.0, None)
    assert read.max_error_rate is None  # the spec states no read error-rate figure
    assert "submit_api" not in V11_SLO_TARGETS


def test_slo_boundaries_are_strict_less_than() -> None:
    at_limit = evaluate_slo_compliance(operation="exam_read_api", latencies_ms=[80.0] * 100)
    assert any("p50" in v for v in at_limit.violations)
    just_under = evaluate_slo_compliance(operation="exam_read_api", latencies_ms=[79.999] * 100)
    assert just_under.passed is True


def test_slo_error_rate_boundary_for_autosave() -> None:
    samples = [10.0] * 10000
    ok = evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=samples, failed_count=0)
    assert ok.passed
    bad = evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=samples, failed_count=1)
    assert any("error_rate" in v for v in bad.violations)  # 0.0001 is not < 0.01%


def test_slo_inputs_fail_closed_with_telemetry_error() -> None:
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=[])
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=[1.0], failed_count=2)
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=[1.0], failed_count=-1)
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="nope", latencies_ms=[1.0])
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=[1.0, math.nan])
    with pytest.raises(TelemetryError):
        evaluate_slo_compliance(operation="autosave_db_commit", latencies_ms=[1.0, -5.0])


def test_percentile_nearest_rank_and_bounds() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert compute_percentile(data, 50.0) == 3.0
    assert compute_percentile(data, 100.0) == 5.0
    assert compute_percentile(data, 0.0) == 1.0
    with pytest.raises(TelemetryError):
        compute_percentile([], 50.0)
    with pytest.raises(TelemetryError):
        compute_percentile(data, 101.0)
    with pytest.raises(TelemetryError):
        compute_percentile(data, math.nan)


def test_custom_target_without_error_budget_is_accepted() -> None:
    t = SloTarget(operation="x", max_p50_ms=None, max_p95_ms=10.0)
    assert evaluate_slo_compliance(operation="x", latencies_ms=[1.0], failed_count=1, target=t).passed
