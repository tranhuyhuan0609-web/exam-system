"""Tests for app.platform.pagination HMAC cursors, allowlists, and correlation IDs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math

import pytest

from app.platform.pagination import (
    MAX_PAGE_LIMIT,
    CursorError,
    build_cursor_page,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    filter_sort_fields,
    resolve_correlation_id,
)

_SECRET = "cursor-hmac-secret-key"
_NOW = 1_794_000_000
_CTX = {"tenant_id": "tenant-a", "route": "GET /api/v1/questions"}
_FIELDS = frozenset({"updated_at", "created_at"})


def _encode(**kw) -> str:
    args = dict(
        sort_field="updated_at", sort_value="2026-11-13T09:00:00Z", tie_id="q-2", direction="desc",
        secret=_SECRET, now_epoch=_NOW, **_CTX,
    )
    args.update(kw)
    return encode_cursor(**args)


def _decode(cursor, **kw):
    args = dict(
        secret=_SECRET, allowed_sort_fields=_FIELDS, expected_sort_field="updated_at",
        expected_direction="desc", now_epoch=_NOW + 10, **_CTX,
    )
    args.update(kw)
    return decode_cursor(cursor, **args)


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
        now_epoch=_NOW,
        **_CTX,
    )
    assert page.has_more is True
    assert len(page.items) == 2
    assert page.next_cursor is not None

    decoded = _decode(page.next_cursor)
    assert decoded.tie_id == "q-2"
    assert decoded.sort_value == "2026-11-13T09:00:00Z"

    # Tampered cursor payload fails HMAC verification
    tampered = ("A" if page.next_cursor[0] != "A" else "B") + page.next_cursor[1:]
    with pytest.raises(CursorError, match="signature"):
        _decode(tampered)


def test_clamp_limit_sort_allowlist_and_correlation_sanitization() -> None:
    assert clamp_limit(9999) == MAX_PAGE_LIMIT
    assert filter_sort_fields("created_at", allowed=("created_at", "score"), default="created_at") == "created_at"
    with pytest.raises(CursorError):
        filter_sort_fields("id; DROP TABLE users;--", allowed=("created_at",), default="created_at")

    assert resolve_correlation_id("req-abc-12345") == "req-abc-12345"
    crlf = resolve_correlation_id("bad\r\nInjected: log")
    assert crlf.startswith("corr-")
    assert "\n" not in crlf


# --- regression + boundary tests --------------------------------------------------------------


def test_cursor_from_another_tenant_is_rejected() -> None:
    cursor = _encode(tenant_id="tenant-a")
    with pytest.raises(CursorError, match="signature"):
        _decode(cursor, tenant_id="tenant-b")


def test_cursor_from_another_route_is_rejected() -> None:
    cursor = _encode(route="GET /api/v1/questions")
    with pytest.raises(CursorError, match="signature"):
        _decode(cursor, route="GET /api/v1/exams")


def test_context_encoding_is_unambiguous() -> None:
    cursor = _encode(tenant_id="a|1:b", route="c")
    with pytest.raises(CursorError, match="signature"):
        _decode(cursor, tenant_id="a", route="1:b|c")


def test_cursor_expiry_boundary() -> None:
    cursor = _encode(ttl_seconds=60)  # exp = NOW + 60
    assert _decode(cursor, now_epoch=_NOW + 59).tie_id == "q-2"
    with pytest.raises(CursorError, match="expired"):
        _decode(cursor, now_epoch=_NOW + 60)
    with pytest.raises(CursorError, match="expired"):
        _decode(cursor, now_epoch=_NOW + 10_000)


def test_wrong_secret_is_rejected() -> None:
    with pytest.raises(CursorError, match="signature"):
        _decode(_encode(), secret="some-other-secret")


def test_sort_field_and_direction_are_mandatory_checks() -> None:
    cursor = _encode()
    with pytest.raises(CursorError, match="sort_field"):
        _decode(cursor, expected_sort_field="created_at")
    with pytest.raises(CursorError, match="direction"):
        _decode(cursor, expected_direction="asc")
    assert _decode(cursor, expected_direction="DESC").direction == "desc"
    with pytest.raises(CursorError, match="allowlist"):
        _decode(cursor, allowed_sort_fields=frozenset({"created_at"}))


def test_non_ascii_cursor_raises_cursor_error() -> None:
    for bad in ("éé.éé", _encode() + "é", "đ.abc"):
        with pytest.raises(CursorError):
            _decode(bad)


def test_trailing_junk_padding_and_whitespace_after_signature_are_rejected() -> None:
    cursor = _encode()
    for suffix in ("A", "AA", "=", "==", "!!", "é", " x"):
        with pytest.raises(CursorError):
            _decode(cursor + suffix)


def test_non_canonical_trailing_bits_are_rejected() -> None:
    payload, sig = _encode().split(".")
    # flip the last base64 character to another one decoding to the same bytes where possible
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = sig[-1]
    variants = [c for c in alphabet if c != last]
    results = []
    for c in variants:
        try:
            results.append(_decode(f"{payload}.{sig[:-1]}{c}"))
        except CursorError:
            continue
    assert results == []


def test_structurally_invalid_cursors() -> None:
    for bad in ("", "   ", ".", "a.", ".b", "a.b.c", "nodot", None, 5, b"a.b", "x" * 3000):
        with pytest.raises(CursorError):
            _decode(bad)  # type: ignore[arg-type]


def _forge(payload_obj, *, secret=_SECRET, context=None) -> str:
    raw = payload_obj if isinstance(payload_obj, bytes) else json.dumps(payload_obj).encode()
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    ctx = context or f"{len(_CTX['tenant_id'])}:{_CTX['tenant_id']}|{len(_CTX['route'])}:{_CTX['route']}"
    sig = hmac.new(secret.encode(), ctx.encode() + b"\x00" + b64.encode(), hashlib.sha256).digest()
    return f"{b64}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def test_validly_signed_but_malformed_payloads_are_rejected() -> None:
    good = {"f": "updated_at", "v": 1, "id": "x", "d": "desc", "exp": _NOW + 100}
    assert _decode(_forge(good)).tie_id == "x"
    for mutation in (
        {"f": 5}, {"f": None}, {"d": "sideways"}, {"d": None}, {"id": ""}, {"id": 5}, {"exp": "soon"},
        {"exp": True}, {"exp": None}, {"v": {"a": 1}}, {"v": [1]},
    ):
        with pytest.raises(CursorError):
            _decode(_forge({**good, **mutation}))
    for raw in (b"not json", b"[1,2]", b"\xff\xfe", b"123", b"null"):
        with pytest.raises(CursorError):
            _decode(_forge(raw))


def test_cursor_without_expiry_is_rejected() -> None:
    with pytest.raises(CursorError, match="expiry"):
        _decode(_forge({"f": "updated_at", "v": 1, "id": "x", "d": "desc"}))


def test_encode_validates_its_inputs() -> None:
    for kwargs in (
        dict(secret=""), dict(secret=None), dict(direction="up"), dict(direction=None), dict(sort_field=""),
        dict(sort_value={"a": 1}), dict(sort_value=math.nan), dict(sort_value=math.inf), dict(tenant_id=""),
        dict(tenant_id=None), dict(route="bad\nroute"), dict(ttl_seconds=0), dict(ttl_seconds=True),
        dict(now_epoch="x"),
    ):
        with pytest.raises(CursorError):
            _encode(**kwargs)


def test_decode_with_empty_secret_or_context_raises_cursor_error() -> None:
    cursor = _encode()
    with pytest.raises(CursorError):
        _decode(cursor, secret="")
    with pytest.raises(CursorError):
        _decode(cursor, tenant_id="")
    with pytest.raises(CursorError):
        _decode(cursor, route="")


def test_unicode_sort_values_round_trip() -> None:
    cursor = _encode(sort_value="Toán học – Vật lí", tie_id="q-é")
    decoded = _decode(cursor)
    assert decoded.sort_value == "Toán học – Vật lí" and decoded.tie_id == "q-é"


def test_page_boundaries_exact_limit_and_empty() -> None:
    kw = dict(
        sort_field="updated_at", key_extractor=lambda r: (r, str(r)), secret=_SECRET, now_epoch=_NOW, **_CTX
    )
    exact = build_cursor_page([1, 2], limit=2, **kw)
    assert exact.has_more is False and exact.next_cursor is None and exact.items == (1, 2)
    empty = build_cursor_page([], limit=2, **kw)
    assert empty.items == () and empty.next_cursor is None
    over = build_cursor_page([1, 2, 3], limit=2, **kw)
    assert over.has_more is True and over.items == (1, 2) and over.next_cursor
    with pytest.raises(CursorError):
        build_cursor_page([1], limit=0, **kw)


def test_clamp_limit_boundaries() -> None:
    assert clamp_limit(None) == 20
    assert clamp_limit(1) == 1
    assert clamp_limit(MAX_PAGE_LIMIT) == MAX_PAGE_LIMIT
    assert clamp_limit(MAX_PAGE_LIMIT + 1) == MAX_PAGE_LIMIT
    for bad in (0, -1, True, 1.5, "10"):
        with pytest.raises(CursorError):
            clamp_limit(bad)  # type: ignore[arg-type]


def test_correlation_id_boundaries_and_injection_attempts() -> None:
    ok8 = "a" * 8
    ok100 = "a" * 100
    assert resolve_correlation_id(ok8) == ok8
    assert resolve_correlation_id(ok100) == ok100
    assert resolve_correlation_id(" " + ok8 + " ") == ok8
    for bad in ("a" * 7, "a" * 101, "id with spaces","abc\r\nSet-Cookie: x=y", "éééééééé", "", None, 12345678):
        out = resolve_correlation_id(bad)  # type: ignore[arg-type]
        assert out.startswith("corr-") and "\n" not in out
    assert resolve_correlation_id("valid-id-1234\n") =="valid-id-1234"  # surrounding whitespace is stripped
    assert resolve_correlation_id(None) != resolve_correlation_id(None)


def test_sort_field_filter_boundaries() -> None:
    allowed = ("created_at", "score")
    assert filter_sort_fields(None, allowed=allowed, default="score") == "score"
    assert filter_sort_fields("   ", allowed=allowed, default="score") == "score"
    assert filter_sort_fields(" created_at ", allowed=allowed, default="score") == "created_at"
    for bad in ("Created_At", "created_at,score", "created_at;--", "é", 5):
        with pytest.raises(CursorError):
            filter_sort_fields(bad, allowed=allowed, default="score")  # type: ignore[arg-type]
    with pytest.raises(CursorError):
        filter_sort_fields("score", allowed=allowed, default="missing")
