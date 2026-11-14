"""HMAC-signed keyset cursor pagination, sort-field allowlisting, and safe correlation IDs.

Cursors are bound to their context (tenant and route), to the sort field and direction, and they
expire, so a cursor minted for one tenant or endpoint is useless anywhere else.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from app.platform.b64url import Base64UrlError, b64url_decode_strict, b64url_encode

T = TypeVar("T")

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
DEFAULT_CURSOR_TTL_SECONDS = 3600
MAX_CURSOR_LENGTH = 2048
_SAFE_CORRELATION_RE = re.compile(r"[A-Za-z0-9._:-]{8,100}", re.ASCII)
_CONTEXT_FORBIDDEN_RE = re.compile(r"[\x00-\x1f\x7f]")


class CursorError(ValueError):
    """Raised on malformed, tampered, expired, foreign or disallowed cursor/pagination parameters."""


@dataclass(frozen=True)
class DecodedCursor:
    sort_field: str
    sort_value: Any
    tie_id: str
    direction: str
    expires_at: int


@dataclass(frozen=True)
class CursorPage[T]:
    items: tuple[T, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


def clamp_limit(raw_limit: int | None, *, default: int = DEFAULT_PAGE_LIMIT, max_limit: int = MAX_PAGE_LIMIT) -> int:
    """Clamp caller limit into 1..max_limit safely."""
    if raw_limit is None:
        return default
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        raise CursorError("limit must be an integer")
    if raw_limit <= 0:
        raise CursorError("limit must be positive")
    return min(raw_limit, max_limit)


def _require_secret(secret: object) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise CursorError("cursor secret cannot be empty")
    return secret.encode("utf-8")


def _context_bytes(tenant_id: object, route: object) -> bytes:
    """Length-prefixed (tenant, route) binding so no two contexts share an encoding."""
    parts: list[str] = []
    for label, value in (("tenant_id", tenant_id), ("route", route)):
        if not isinstance(value, str) or not value.strip() or _CONTEXT_FORBIDDEN_RE.search(value):
            raise CursorError(f"cursor {label} must be a non-empty printable string")
        parts.append(f"{len(value)}:{value}")
    return "|".join(parts).encode("utf-8")


def _sign(secret: bytes, context: bytes, b64_payload: str) -> bytes:
    return hmac.new(secret, context + b"\x00" + b64_payload.encode("ascii"), hashlib.sha256).digest()


def _is_json_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def encode_cursor(
    *,
    sort_field: str,
    sort_value: Any,
    tie_id: str,
    direction: str = "desc",
    secret: str,
    tenant_id: str,
    route: str,
    now_epoch: int | None = None,
    ttl_seconds: int = DEFAULT_CURSOR_TTL_SECONDS,
) -> str:
    """Create an HMAC-SHA256 signed base64url keyset cursor bound to tenant and route."""
    key = _require_secret(secret)
    context = _context_bytes(tenant_id, route)
    if not isinstance(direction, str) or direction.lower() not in {"asc", "desc"}:
        raise CursorError("unsupported sort direction")
    if not isinstance(sort_field, str) or not sort_field:
        raise CursorError("sort_field must be a non-empty string")
    if not _is_json_scalar(sort_value):
        raise CursorError("sort_value must be a JSON scalar")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise CursorError("ttl_seconds must be a positive integer")
    now = int(time.time()) if now_epoch is None else now_epoch
    if isinstance(now, bool) or not isinstance(now, int):
        raise CursorError("now_epoch must be an integer")
    payload_bytes = json.dumps(
        {
            "f": sort_field,
            "v": sort_value,
            "id": str(tie_id),
            "d": direction.lower(),
            "exp": now + ttl_seconds,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    b64_payload = b64url_encode(payload_bytes)
    return f"{b64_payload}.{b64url_encode(_sign(key, context, b64_payload))}"


def decode_cursor(
    cursor: str,
    *,
    secret: str,
    allowed_sort_fields: frozenset[str],
    expected_sort_field: str,
    expected_direction: str,
    tenant_id: str,
    route: str,
    now_epoch: int | None = None,
) -> DecodedCursor:
    """Verify signature, context binding, expiry and sort contract of a keyset cursor."""
    key = _require_secret(secret)
    context = _context_bytes(tenant_id, route)
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise CursorError("malformed cursor structure")
    parts = cursor.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CursorError("malformed cursor structure")

    b64_payload, b64_sig = parts
    try:
        actual_sig = b64url_decode_strict(b64_sig)
        raw_payload = b64url_decode_strict(b64_payload)
    except Base64UrlError as exc:
        raise CursorError("malformed cursor encoding") from exc

    if not hmac.compare_digest(_sign(key, context, b64_payload), actual_sig):
        raise CursorError("cursor signature verification failed")

    try:
        data = json.loads(raw_payload.decode("utf-8"))
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        raise CursorError("malformed cursor payload") from exc
    if not isinstance(data, dict):
        raise CursorError("malformed cursor payload")

    sort_field = data.get("f")
    direction = data.get("d")
    tie_id = data.get("id")
    expires_at = data.get("exp")
    if not isinstance(sort_field, str) or sort_field not in allowed_sort_fields:
        raise CursorError("cursor sort_field not in allowlist")
    if direction not in {"asc", "desc"} or not isinstance(tie_id, str) or not tie_id:
        raise CursorError("cursor missing valid direction or tie_id")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise CursorError("cursor missing expiry")
    if sort_field != expected_sort_field:
        raise CursorError("cursor sort_field mismatches query parameter")
    if not isinstance(expected_direction, str) or direction != expected_direction.lower():
        raise CursorError("cursor direction mismatches query direction")
    sort_value = data.get("v")
    if not _is_json_scalar(sort_value):
        raise CursorError("cursor sort_value must be a JSON scalar")

    now = int(time.time()) if now_epoch is None else now_epoch
    if isinstance(now, bool) or not isinstance(now, int):
        raise CursorError("now_epoch must be an integer")
    if now >= expires_at:
        raise CursorError("cursor has expired")

    return DecodedCursor(
        sort_field=sort_field,
        sort_value=sort_value,
        tie_id=tie_id,
        direction=direction,
        expires_at=expires_at,
    )


def build_cursor_page[T](
    rows_plus_one: Sequence[T],
    *,
    limit: int,
    sort_field: str,
    direction: str = "desc",
    key_extractor: Callable[[T], tuple[Any, str]],
    secret: str,
    tenant_id: str,
    route: str,
    now_epoch: int | None = None,
    ttl_seconds: int = DEFAULT_CURSOR_TTL_SECONDS,
) -> CursorPage[T]:
    """Slice a `limit + 1` query result cleanly into a CursorPage with signed next_cursor."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise CursorError("limit must be a positive integer")
    has_more = len(rows_plus_one) > limit
    page_items = tuple(rows_plus_one[:limit])
    next_cursor: str | None = None
    if has_more and page_items:
        sort_value, tie_id = key_extractor(page_items[-1])
        next_cursor = encode_cursor(
            sort_field=sort_field,
            sort_value=sort_value,
            tie_id=tie_id,
            direction=direction,
            secret=secret,
            tenant_id=tenant_id,
            route=route,
            now_epoch=now_epoch,
            ttl_seconds=ttl_seconds,
        )
    return CursorPage(items=page_items, next_cursor=next_cursor, has_more=has_more, limit=limit)


def resolve_correlation_id(incoming: str | None) -> str:
    """Validate incoming X-Correlation-Id against log-injection or mint a sortable fallback ID."""
    if isinstance(incoming, str) and incoming:
        cleaned = incoming.strip()
        if _SAFE_CORRELATION_RE.fullmatch(cleaned):
            return cleaned
    ms_hex = f"{time.time_ns() // 1_000_000:012x}"
    rand_hex = os.urandom(6).hex()
    return f"corr-{ms_hex}-{rand_hex}"


def filter_sort_fields(requested: str | None, *, allowed: Iterable[str], default: str) -> str:
    """Validate user-supplied sort column strictly against an allowlist."""
    allowed_set = frozenset(allowed)
    if default not in allowed_set:
        raise CursorError("default sort field must belong to allowed set")
    if requested is None:
        return default
    if not isinstance(requested, str):
        raise CursorError("sort field must be a string")
    if not requested.strip():
        return default
    candidate = requested.strip()
    if candidate not in allowed_set:
        raise CursorError("unsupported sort field")
    return candidate
