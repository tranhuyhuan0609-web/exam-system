"""HMAC-signed keyset cursor pagination, sort-field allowlisting, and safe correlation IDs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
_SAFE_CORRELATION_RE = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")


class CursorError(ValueError):
    """Raised on malformed, tampered, or disallowed cursor/pagination parameters."""


@dataclass(frozen=True)
class DecodedCursor:
    sort_field: str
    sort_value: Any
    tie_id: str
    direction: str


@dataclass(frozen=True)
class CursorPage(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


def clamp_limit(raw_limit: int | None, *, default: int = DEFAULT_PAGE_LIMIT, max_limit: int = MAX_PAGE_LIMIT) -> int:
    """Clamp caller limit into 1..max_limit safely."""
    if raw_limit is None:
        return default
    if raw_limit <= 0:
        raise CursorError("limit must be positive")
    return min(raw_limit, max_limit)


def encode_cursor(
    *,
    sort_field: str,
    sort_value: Any,
    tie_id: str,
    direction: str = "desc",
    secret: str,
) -> str:
    """Create an HMAC-SHA256 signed base64url keyset cursor."""
    if not secret:
        raise ValueError("cursor secret cannot be empty")
    clean_dir = direction.lower()
    if clean_dir not in {"asc", "desc"}:
        raise CursorError(f"unsupported sort direction: {direction!r}")
    payload_bytes = json.dumps(
        {
            "f": sort_field,
            "v": sort_value,
            "id": str(tie_id),
            "d": clean_dir,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    return f"{b64_payload}.{b64_sig}"


def decode_cursor(
    cursor: str,
    *,
    secret: str,
    allowed_sort_fields: frozenset[str],
    expected_sort_field: str | None = None,
    expected_direction: str | None = None,
) -> DecodedCursor:
    """Verify HMAC signature and sort-field allowlist before decoding a keyset cursor."""
    if not secret:
        raise ValueError("cursor secret cannot be empty")
    parts = cursor.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CursorError("malformed cursor structure")

    b64_payload, b64_sig = parts
    expected_sig = hmac.new(secret.encode("utf-8"), b64_payload.encode("ascii"), hashlib.sha256).digest()
    try:
        actual_sig = base64.urlsafe_b64decode(b64_sig + "=" * (-len(b64_sig) % 4))
    except Exception as exc:
        raise CursorError("malformed cursor signature encoding") from exc

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise CursorError("cursor signature verification failed")

    try:
        raw_json = base64.urlsafe_b64decode(b64_payload + "=" * (-len(b64_payload) % 4)).decode("utf-8")
        data = json.loads(raw_json)
    except Exception as exc:
        raise CursorError("malformed cursor payload") from exc

    sort_field = str(data.get("f", ""))
    direction = str(data.get("d", ""))
    tie_id = str(data.get("id", ""))
    if sort_field not in allowed_sort_fields:
        raise CursorError(f"cursor sort_field {sort_field!r} not in allowlist")
    if direction not in {"asc", "desc"} or not tie_id:
        raise CursorError("cursor missing valid direction or tie_id")
    if expected_sort_field is not None and sort_field != expected_sort_field:
        raise CursorError("cursor sort_field mismatches query parameter")
    if expected_direction is not None and direction != expected_direction.lower():
        raise CursorError("cursor direction mismatches query direction")

    return DecodedCursor(
        sort_field=sort_field,
        sort_value=data.get("v"),
        tie_id=tie_id,
        direction=direction,
    )


def build_cursor_page(
    rows_plus_one: Sequence[T],
    *,
    limit: int,
    sort_field: str,
    direction: str = "desc",
    key_extractor: Callable[[T], tuple[Any, str]],
    secret: str,
) -> CursorPage[T]:
    """Slice a `limit + 1` query result cleanly into a CursorPage with signed next_cursor."""
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
        )
    return CursorPage(items=page_items, next_cursor=next_cursor, has_more=has_more, limit=limit)


def resolve_correlation_id(incoming: str | None) -> str:
    """Validate incoming X-Correlation-Id against log-injection or mint a sortable fallback ID."""
    if incoming:
        cleaned = incoming.strip()
        if _SAFE_CORRELATION_RE.match(cleaned):
            return cleaned
    ms_hex = f"{time.time_ns() // 1_000_000:012x}"
    rand_hex = os.urandom(6).hex()
    return f"corr-{ms_hex}-{rand_hex}"


def filter_sort_fields(requested: str | None, *, allowed: Iterable[str], default: str) -> str:
    """Validate user-supplied sort column strictly against an allowlist."""
    allowed_set = frozenset(allowed)
    if default not in allowed_set:
        raise ValueError("default sort field must belong to allowed set")
    if not requested or not requested.strip():
        return default
    candidate = requested.strip()
    if candidate not in allowed_set:
        raise CursorError(f"unsupported sort field: {candidate!r}")
    return candidate
