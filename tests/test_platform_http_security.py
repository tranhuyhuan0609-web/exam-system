"""Tests for app.platform.http_security headers, CORS validation, and proxy IP resolution."""

from __future__ import annotations

import pytest

from app.platform.http_security import (
    SecurityConfigError,
    build_security_headers,
    extract_client_ip,
    is_origin_allowed,
    validate_cors_origins,
)


def test_security_headers_enforce_csp_nosniff_and_frame_lockdown() -> None:
    hdrs = build_security_headers(hsts_enabled=True, allow_same_origin_frame=False)
    assert hdrs["X-Content-Type-Options"] == "nosniff"
    assert hdrs["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in hdrs["Content-Security-Policy"]
    assert "unsafe-eval" not in hdrs["Content-Security-Policy"]
    assert "Strict-Transport-Security" in hdrs


def test_cors_forbids_wildcard_with_credentials_and_validates_origins() -> None:
    with pytest.raises(SecurityConfigError, match="wildcard"):
        validate_cors_origins(("https://exam.edu.vn", "*"), allow_credentials=True)

    with pytest.raises(SecurityConfigError, match="syntax"):
        validate_cors_origins(("https://exam.edu.vn/path",), allow_credentials=True)

    assert is_origin_allowed(
        "HTTPS://APP.EXAM.EDU.VN/",
        allowed_origins=("https://app.exam.edu.vn",),
        allow_credentials=True,
    )
    assert not is_origin_allowed(
        "https://evil.example",
        allowed_origins=("https://app.exam.edu.vn",),
        allow_credentials=True,
    )


def test_extract_client_ip_walks_right_to_left_stopping_at_untrusted_client() -> None:
    # Attacker prepended spoofed 8.8.8.8, real client is 203.0.113.50 behind 10.0.0.0/8 proxies
    resolved = extract_client_ip(
        remote_addr="10.0.1.5",
        x_forwarded_for="8.8.8.8, 203.0.113.50, 10.0.2.9",
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert resolved == "203.0.113.50"

    # Untrusted direct peer cannot spoof X-Forwarded-For
    direct = extract_client_ip(
        remote_addr="198.51.100.7",
        x_forwarded_for="127.0.0.1",
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert direct == "198.51.100.7"
