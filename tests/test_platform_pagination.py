"""Tests for app.platform.pagination HMAC cursors, allowlists, and correlation IDs."""

from __future__ import annotations

import pytest

from app.platform.pagination import (
    MAX_PAGE_LIMIT,
    CursorError,
    build_cursor_page,
    clamp_limit,
    decode_cursor,
    filter_sort_fields,
    resolve_correlation_id,
)

_SECRET = "cursor-hmac-secret-key"


def test_hmac_cursor_round_trip_page_and_tamper_rejection() -> None:
    rows = [
        {"id": "q-1", "updated_at": "2026-11-13T10:00:00Z"},
        {"id": "q-2", "updated_at": "2026-11-13T09:00:00Z"},
        {"id": "q-3", "updated_at": "2026-11-13T08:00:00Z"},
    ]
    limit = clamp_limit(2)
    page = build_cursor_page(
        rows,
        limit=limit,
        sort_field="updated_at",
        direction="desc",
        key_extractor=lambda r: (r["updated_at"], r["id"]),
        secret=_SECRET,
    )
    assert page.has_more is True
    assert len(page.items) == 2
    assert page.next_cursor is not None

    decoded = decode_cursor(
        page.next_cursor,
        secret=_SECRET,
        allowed_sort_fields=frozenset({"updated_at", "created_at"}),
        expected_sort_field="updated_at",
        expected_direction="desc",
    )
    assert decoded.tie_id == "q-2"
    assert decoded.sort_value == "2026-11-13T09:00:00Z"

    # Tampered cursor payload fails HMAC verification
    tampered = "X" + page.next_cursor[1:]
    with pytest.raises(CursorError, match="signature"):
        decode_cursor(
            tampered,
            secret=_SECRET,
            allowed_sort_fields=frozenset({"updated_at"}),
        )


def test_clamp_limit_sort_allowlist_and_correlation_sanitization() -> None:
    assert clamp_limit(9999) == MAX_PAGE_LIMIT
    assert filter_sort_fields("created_at", allowed=("created_at", "score"), default="created_at") == "created_at"
    with pytest.raises(CursorError):
        filter_sort_fields("id; DROP TABLE users;--", allowed=("created_at",), default="created_at")

    assert resolve_correlation_id("req-abc-12345") == "req-abc-12345"
    crlf = resolve_correlation_id("bad\r\nInjected: log")
    assert crlf.startswith("corr-")
    assert "\n" not in crlf
