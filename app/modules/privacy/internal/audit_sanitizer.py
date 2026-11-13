"""Recursive audit-log JSON sanitizer with free-text PII masking (modules F04/G02)."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.modules.privacy.internal.blind_index import validate_pepper

DEFAULT_MAX_AUDIT_DEPTH = 32

SENSITIVE_AUDIT_KEYS: frozenset[str] = frozenset(
    {
        "email",
        "rawemail",
        "plaintextemail",
        "guardianemail",
        "password",
        "phone",
        "phonenumber",
        "fullname",
        "displayname",
        "cccd",
    }
)

_KEY_NORM_RE = re.compile(r"[\s_\-]+")
_EMAIL_IN_TEXT_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_VN_PHONE_IN_TEXT_RE = re.compile(
    r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?[35789](?:[\s.\-]?\d){8}|[\s.\-]?2(?:[\s.\-]?\d){8,9})(?!\d)"
)


def _normalize_audit_key(key: str) -> str:
    return _KEY_NORM_RE.sub("", key.casefold())


def _short_hmac(raw_value: str, key: bytes) -> str:
    return hmac.new(key, raw_value.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _mask_free_text(text: str, key: bytes) -> str:
    masked = _EMAIL_IN_TEXT_RE.sub(
        lambda m: f"[REDACTED_EMAIL:{_short_hmac(m.group(0), key)}]",
        text,
    )
    return _VN_PHONE_IN_TEXT_RE.sub(
        lambda m: f"[REDACTED_PHONE:{_short_hmac(m.group(0), key)}]",
        masked,
    )


def _sanitize_value(value: Any, key: bytes, *, depth: int, max_depth: int) -> Any:
    if depth > max_depth:
        raise ValueError(f"Audit payload exceeds maximum nesting depth of {max_depth}")

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for k, v in value.items():
            k_str = str(k)
            if _normalize_audit_key(k_str) in SENSITIVE_AUDIT_KEYS and v is not None:
                sanitized[f"{k_str}_redacted_hash"] = f"hmac:{_short_hmac(str(v), key)}"
            else:
                sanitized[k_str] = _sanitize_value(v, key, depth=depth + 1, max_depth=max_depth)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_value(item, key, depth=depth + 1, max_depth=max_depth) for item in value]

    if isinstance(value, tuple):
        return [_sanitize_value(item, key, depth=depth + 1, max_depth=max_depth) for item in value]

    if isinstance(value, str):
        return _mask_free_text(value, key)

    return value


def sanitize_audit_json(
    payload: Mapping[str, Any] | Sequence[Any] | None,
    cluster_pepper: bytes,
    *,
    max_depth: int = DEFAULT_MAX_AUDIT_DEPTH,
) -> dict[str, Any]:
    """Return a deep-sanitized copy of an audit payload without mutating inputs."""
    key = validate_pepper(cluster_pepper)
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if payload is None:
        return {}
    if isinstance(payload, Mapping):
        return _sanitize_value(payload, key, depth=0, max_depth=max_depth)
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        return {"items": _sanitize_value(list(payload), key, depth=0, max_depth=max_depth)}
    raise TypeError("payload must be a mapping, sequence, or None")
