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


# --- regression + boundary tests --------------------------------------------------------------

_TRUSTED = ("10.0.0.0/8",)


@pytest.mark.parametrize("cidr", ["not-a-cidr", "10.0.0.0/33", "300.1.1.1/8", "", "10.0.0.0/8/8", None, 5])
def test_invalid_trusted_cidr_raises_security_config_error(cidr) -> None:
    with pytest.raises(SecurityConfigError):
        extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for="1.2.3.4", trusted_proxy_cidrs=(cidr,))  # type: ignore[arg-type]


def test_invalid_cidr_is_reported_even_for_an_untrusted_peer() -> None:
    with pytest.raises(SecurityConfigError):
        extract_client_ip(remote_addr="198.51.100.7", x_forwarded_for=None, trusted_proxy_cidrs=("garbage",))


def test_no_trusted_proxies_means_xff_is_ignored() -> None:
    assert extract_client_ip(remote_addr="198.51.100.7", x_forwarded_for="1.1.1.1") == "198.51.100.7"


@pytest.mark.parametrize("remote", ["", "   ", "not-an-ip", "1.2.3", "1.2.3.4.5", "fe80::1%eth0", None, 5])
def test_invalid_remote_addr_raises(remote) -> None:
    with pytest.raises(SecurityConfigError):
        extract_client_ip(remote_addr=remote, x_forwarded_for=None, trusted_proxy_cidrs=_TRUSTED)  # type: ignore[arg-type]


def test_forged_xff_entry_on_the_right_falls_back_to_the_peer() -> None:
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for="1.2.3.4, evil", trusted_proxy_cidrs=_TRUSTED) == "10.0.0.1"


def test_xff_empty_none_and_blank_entries() -> None:
    for xff in (None, "", "   ", ",,,"):
        assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for=xff, trusted_proxy_cidrs=_TRUSTED) == "10.0.0.1"
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for=" , 203.0.113.9 ,", trusted_proxy_cidrs=_TRUSTED) == "203.0.113.9"


def test_all_hops_trusted_returns_the_leftmost() -> None:
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for="10.9.9.9, 10.1.1.1", trusted_proxy_cidrs=_TRUSTED) == "10.9.9.9"


def test_multiple_trusted_proxy_hops_in_the_middle_of_the_chain() -> None:
    xff = "9.9.9.9, 203.0.113.5, 10.0.0.7, 10.0.0.8"
    assert extract_client_ip(remote_addr="10.0.0.9", x_forwarded_for=xff, trusted_proxy_cidrs=_TRUSTED) == "203.0.113.5"


def test_ipv4_mapped_ipv6_cannot_bypass_the_trusted_range() -> None:
    # ::ffff:10.0.0.7 is the trusted proxy 10.0.0.7: it must be skipped, not returned as the client.
    out = extract_client_ip(
        remote_addr="10.0.0.1", x_forwarded_for="203.0.113.5, ::ffff:10.0.0.7", trusted_proxy_cidrs=_TRUSTED
    )
    assert out == "203.0.113.5"


def test_ipv6_clients_and_trusted_ipv6_proxies() -> None:
    out = extract_client_ip(
        remote_addr="fd00::1", x_forwarded_for="2001:db8::5, fd00::2", trusted_proxy_cidrs=("fd00::/8",)
    )
    assert out == "2001:db8::5"


def test_zone_id_in_xff_is_treated_as_forged() -> None:
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for="fe80::1%eth0", trusted_proxy_cidrs=_TRUSTED) == "10.0.0.1"


def test_oversized_or_overlong_xff_is_ignored() -> None:
    long_header = ", ".join(["1.1.1.1"] * 400)
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for=long_header, trusted_proxy_cidrs=_TRUSTED) == "10.0.0.1"
    many_hops = ", ".join(["1.1.1.1"] * 60)
    assert extract_client_ip(remote_addr="10.0.0.1", x_forwarded_for=many_hops, trusted_proxy_cidrs=_TRUSTED) == "10.0.0.1"


def test_error_messages_do_not_echo_input() -> None:
    with pytest.raises(SecurityConfigError) as info:
        extract_client_ip(remote_addr="SECRET-VALUE", x_forwarded_for=None, trusted_proxy_cidrs=_TRUSTED)
    assert "SECRET-VALUE" not in str(info.value)
    with pytest.raises(SecurityConfigError) as info2:
        validate_cors_origins(("https://SECRET-VALUE/path",), allow_credentials=False)
    assert "SECRET-VALUE" not in str(info2.value)


@pytest.mark.parametrize(
    "origin",
    [
        "https://exam.edu.vn/path", "ftp://exam.edu.vn", "exam.edu.vn", "https://", "https://exam edu.vn",
        "https://exam.edu.vn:99999", "https://.exam.edu.vn", "https://exam..edu.vn", "https://-a.com",
        "https://exam.edu.vn?x=1", "https://exam.edu.vn#frag", "https://user@exam.edu.vn", "javascript:alert(1)",
        "https://tést.vn", "", "   ", "https://exam.edu.vn\nX: y", "https://" + "a" * 64 + ".vn",
    ],
)
def test_malformed_cors_origins_are_rejected(origin: str) -> None:
    with pytest.raises(SecurityConfigError):
        validate_cors_origins((origin,), allow_credentials=True)


def test_cors_validation_normalises_deduplicates_and_allows_ports() -> None:
    out = validate_cors_origins(
        ("HTTPS://App.Exam.edu.vn/", "https://app.exam.edu.vn", "http://localhost:3000", "https://x.vn:65535"),
        allow_credentials=True,
    )
    assert out == ("https://app.exam.edu.vn", "http://localhost:3000", "https://x.vn:65535")


def test_cors_wildcard_without_credentials_is_allowed_and_non_string_entries_rejected() -> None:
    assert validate_cors_origins(("*",), allow_credentials=False) == ("*",)
    with pytest.raises(SecurityConfigError):
        validate_cors_origins((None,), allow_credentials=False)  # type: ignore[arg-type]


def test_origin_check_edge_cases() -> None:
    allowed = ("https://app.exam.edu.vn",)
    for origin in (None, "", "   ", "null", "https://app.exam.edu.vn.evil.com", "http://app.exam.edu.vn", 5):
        assert not is_origin_allowed(origin, allowed_origins=allowed, allow_credentials=True)  # type: ignore[arg-type]
    assert is_origin_allowed(" https://app.exam.edu.vn ", allowed_origins=allowed, allow_credentials=True)
    assert is_origin_allowed("https://anything.example", allowed_origins=("*",), allow_credentials=False)


def test_empty_allowlist_allows_nothing() -> None:
    assert not is_origin_allowed("https://a.vn", allowed_origins=(), allow_credentials=True)


def test_security_headers_variants() -> None:
    framed = build_security_headers(hsts_enabled=False, allow_same_origin_frame=True)
    assert framed["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in framed["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in framed
    strict = build_security_headers()
    csp = strict["Content-Security-Policy"]
    assert "object-src 'none'" in csp and "base-uri 'self'" in csp and "default-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]  # no inline scripts
    assert "script-src 'self'" in csp
    assert "includeSubDomains" in strict["Strict-Transport-Security"]
    assert all("\n" not in v and "\r" not in v for v in strict.values())
