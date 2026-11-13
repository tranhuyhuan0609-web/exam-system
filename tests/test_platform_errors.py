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


# --- regression + boundary tests --------------------------------------------------------------

import pytest  # noqa: E402

from app.platform.errors import build_problem_detail  # noqa: E402


def _ext(extensions: dict) -> dict:
    return build_problem_detail(
        code=PlatformErrorCode.VALIDATION_ERROR, detail="d", instance="/x", extensions=extensions
    ).to_dict()


def test_tokens_used_is_not_redacted_but_real_tokens_are() -> None:
    body = _ext({"tokens_used": 1234, "access_token": "x", "refreshToken": "y", "id_token": "z", "max_tokens": 5})
    assert body["tokens_used"] == 1234
    assert body["max_tokens"] == 5
    assert body["access_token"] == body["refreshToken"] == body["id_token"] == "[REDACTED]"


def test_secrets_inside_lists_of_dicts_and_tuples_are_redacted() -> None:
    body = _ext(
        {
            "errors": [{"field": "a", "password": "hunter2"}, {"nested": [{"api_key": "k"}]}],
            "pair": ({"secret": "s"},),
            "deep": {"x": {"y": {"Authorization": "Bearer abc"}}},
        }
    )
    dumped = repr(body)
    for leaked in ("hunter2", "'k'", "'s'", "Bearer abc"):
        assert leaked not in dumped
    assert body["errors"][0]["field"] == "a"


def test_pii_in_detail_and_extension_values_is_scrubbed() -> None:
    body = build_problem_detail(
        code=PlatformErrorCode.VALIDATION_ERROR,
        detail="bad value for user hs@school.edu.vn phone 0912345678",
        instance="/x",
        extensions={"note": "token eyJhbGciOiJIUzI1NiJ9.payload.sig"},
    ).to_dict()
    assert "hs@school.edu.vn" not in body["detail"] and "0912345678" not in body["detail"]
    assert "eyJ" not in body["note"]


def test_non_string_extension_keys_and_values_do_not_crash() -> None:
    body = _ext({1: "a", "when": __import__("datetime").datetime(2026, 11, 13)})
    assert body["1"] == "a" and body["when"].startswith("2026-11-13")


def test_core_fields_cannot_be_overridden_by_extensions() -> None:
    body = _ext({"status": 200, "error_code": "FAKE", "type": "x"})
    assert body["status"] == 422 and body["error_code"] == "VALIDATION_ERROR"


def test_error_codes_match_the_rest_of_the_code_base() -> None:
    codes = {c.value for c in PlatformErrorCode}
    assert "PAYLOAD_HASH_MISMATCH" in codes
    assert "DAILY_AI_BUDGET_EXCEEDED" in codes
    assert "IDEMPOTENCY_PAYLOAD_MISMATCH" not in codes
    assert "AI_DAILY_BUDGET_EXCEEDED" not in codes
    assert build_problem_detail(
        code=PlatformErrorCode.PAYLOAD_HASH_MISMATCH, detail="d", instance="/x"
    ).status == 422
    assert build_problem_detail(
        code=PlatformErrorCode.DAILY_AI_BUDGET_EXCEEDED, detail="d", instance="/x"
    ).status == 429


def test_every_error_code_has_a_status_and_a_problem_uri() -> None:
    for code in PlatformErrorCode:
        problem = build_problem_detail(code=code, detail="d", instance="/x")
        assert 400 <= problem.status <= 599
        assert problem.type.endswith(code.value.lower().replace("_", "-"))


def test_status_override_is_validated() -> None:
    assert PlatformError(PlatformErrorCode.NOT_FOUND, "d", status_code=410).status_code == 410
    for bad in (200, 99, 600, True, "404"):
        assert PlatformError(PlatformErrorCode.NOT_FOUND, "d", status_code=bad).status_code == 404  # type: ignore[arg-type]
    problem = build_problem_detail(code=PlatformErrorCode.NOT_FOUND, detail="d", instance="/x", status_override=302)
    assert problem.status == 404


def test_invalid_code_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        PlatformError("NOT_A_CODE", "d")  # type: ignore[arg-type]


def test_subclassed_platform_error_and_plain_exceptions_map_correctly() -> None:
    class Custom(PlatformError):
        pass

    problem = exception_to_problem(Custom(PlatformErrorCode.FORBIDDEN, "no"), instance="/a")
    assert problem.status == 403
    for exc in (KeyError("secret"), ValueError("postgres://u:p@h/db"), Exception()):
        out = exception_to_problem(exc, instance="/a", correlation_id=None).to_dict()
        assert out["status"] == 500 and "postgres" not in repr(out) and "correlation_id" not in out


def test_extensions_mapping_is_copied_not_aliased() -> None:
    ext = {"a": 1}
    err = PlatformError(PlatformErrorCode.NOT_FOUND, "d", extensions=ext)
    ext["a"] = 2
    assert err.extensions == {"a": 1}
