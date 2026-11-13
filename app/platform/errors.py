"""RFC 7807 Problem+JSON error envelope, V11.3 machine error catalog, and safe exception mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

_PROBLEM_BASE_URI = "https://errors.ulp.edu.vn/problems"
_SENSITIVE_KEY_TOKENS: tuple[str, ...] = ("password", "secret", "token", "api_key", "authorization", "cookie")


class PlatformErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    EXAM_SESSION_ACTIVE = "EXAM_SESSION_ACTIVE"
    PARENT_CONSENT_REQUIRED = "PARENT_CONSENT_REQUIRED"
    RIGHTS_RESTRICTED = "RIGHTS_RESTRICTED"
    STALE_CLIENT_SEQUENCE = "STALE_CLIENT_SEQUENCE"
    PREREQUISITE_CYCLE_DETECTED = "PREREQUISITE_CYCLE_DETECTED"
    PUBLISH_GATE_BLOCKED = "PUBLISH_GATE_BLOCKED"
    IDEMPOTENCY_PAYLOAD_MISMATCH = "IDEMPOTENCY_PAYLOAD_MISMATCH"
    AI_DAILY_BUDGET_EXCEEDED = "AI_DAILY_BUDGET_EXCEEDED"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_ERROR_HTTP_SPEC: dict[PlatformErrorCode, tuple[int, str]] = {
    PlatformErrorCode.UNAUTHORIZED: (401, "Authentication Required"),
    PlatformErrorCode.FORBIDDEN: (403, "Forbidden"),
    PlatformErrorCode.NOT_FOUND: (404, "Resource Not Found"),
    PlatformErrorCode.EXAM_SESSION_ACTIVE: (403, "Action Locked During Active Exam Session"),
    PlatformErrorCode.PARENT_CONSENT_REQUIRED: (403, "Parental Consent Required"),
    PlatformErrorCode.RIGHTS_RESTRICTED: (403, "Rights Restriction Prevents Access"),
    PlatformErrorCode.STALE_CLIENT_SEQUENCE: (409, "Stale Autosave Client Sequence"),
    PlatformErrorCode.PREREQUISITE_CYCLE_DETECTED: (422, "Knowledge Graph Cycle Detected"),
    PlatformErrorCode.PUBLISH_GATE_BLOCKED: (422, "Publish Gate Check Failed"),
    PlatformErrorCode.IDEMPOTENCY_PAYLOAD_MISMATCH: (422, "Idempotency Key Reused With Different Payload"),
    PlatformErrorCode.AI_DAILY_BUDGET_EXCEEDED: (429, "Tenant Daily AI Budget Exceeded"),
    PlatformErrorCode.RATE_LIMIT_EXCEEDED: (429, "Rate Limit Exceeded"),
    PlatformErrorCode.VALIDATION_ERROR: (422, "Validation Failed"),
    PlatformErrorCode.INTERNAL_ERROR: (500, "Internal Server Error"),
}


class PlatformError(Exception):
    """Base domain/platform error mapped deterministically to RFC 7807 Problem+JSON."""

    def __init__(
        self,
        code: PlatformErrorCode,
        detail: str,
        *,
        status_code: int | None = None,
        extensions: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        default_status, title = _ERROR_HTTP_SPEC[code]
        self.code = code
        self.status_code = status_code or default_status
        self.title = title
        self.detail = detail
        self.extensions = dict(extensions or {})


@dataclass(frozen=True)
class ProblemDetail:
    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: str
    correlation_id: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": self.instance,
            "error_code": self.error_code,
        }
        if self.correlation_id:
            payload["correlation_id"] = self.correlation_id
        for key, value in self.extensions.items():
            if key not in payload:
                payload[key] = value
        return payload


def _sanitize_extensions(raw: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, val in raw.items():
        lowered = key.lower()
        if any(tok in lowered for tok in _SENSITIVE_KEY_TOKENS):
            sanitized[key] = "[REDACTED]"
        elif isinstance(val, dict):
            sanitized[key] = _sanitize_extensions(val)
        else:
            sanitized[key] = val
    return sanitized


def build_problem_detail(
    *,
    code: PlatformErrorCode,
    detail: str,
    instance: str,
    correlation_id: str | None = None,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> ProblemDetail:
    """Construct an RFC 7807 ProblemDetail object for any V11.3 error code."""
    default_status, title = _ERROR_HTTP_SPEC[code]
    slug = code.value.lower().replace("_", "-")
    return ProblemDetail(
        type=f"{_PROBLEM_BASE_URI}/{slug}",
        title=title,
        status=status_override or default_status,
        detail=detail,
        instance=instance,
        error_code=code.value,
        correlation_id=correlation_id,
        extensions=_sanitize_extensions(extensions or {}),
    )


def exception_to_problem(
    exc: Exception,
    *,
    instance: str,
    correlation_id: str | None = None,
) -> ProblemDetail:
    """Safely map exceptions to RFC 7807 ProblemDetail without leaking unexpected internals."""
    if isinstance(exc, PlatformError):
        return build_problem_detail(
            code=exc.code,
            detail=exc.detail,
            instance=instance,
            correlation_id=correlation_id,
            status_override=exc.status_code,
            extensions=exc.extensions,
        )

    return build_problem_detail(
        code=PlatformErrorCode.INTERNAL_ERROR,
        detail="An unexpected internal error occurred.",
        instance=instance,
        correlation_id=correlation_id,
    )
