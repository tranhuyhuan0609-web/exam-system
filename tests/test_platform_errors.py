"""Tests for app.platform.errors RFC 7807 Problem+JSON builder and safe mapping."""

from __future__ import annotations

from app.platform.errors import (
    PlatformError,
    PlatformErrorCode,
    exception_to_problem,
)


def test_maps_v11_platform_errors_and_redacts_sensitive_extensions() -> None:
    err = PlatformError(
        PlatformErrorCode.STALE_CLIENT_SEQUENCE,
        "client_sequence 4 <= server_sequence 5",
        extensions={"server_sequence": 5, "auth_token": "secret-leak"},
    )
    problem = exception_to_problem(
        err,
        instance="/api/v1/attempts/att-1/responses/item-2",
        correlation_id="corr-123",
    )
    body = problem.to_dict()
    assert body["status"] == 409
    assert body["error_code"] == "STALE_CLIENT_SEQUENCE"
    assert body["correlation_id"] == "corr-123"
    assert body["server_sequence"] == 5
    assert body["auth_token"] == "[REDACTED]"


def test_unexpected_exception_never_leaks_internal_details() -> None:
    raw = RuntimeError("FATAL: password authentication failed for pg user postgres@10.0.0.5")
    problem = exception_to_problem(raw, instance="/api/v1/search", correlation_id="corr-500")
    body = problem.to_dict()
    assert body["status"] == 500
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "postgres" not in body["detail"]
    assert "10.0.0.5" not in body["detail"]
