"""Unit tests for A02 anti-SSRF IP validation (pure, no network, no conftest)."""

from __future__ import annotations

import pytest

from app.modules.crawler.public import (
    SSRFDNSRebindingError,
    check_resolved_addresses,
    is_public_address,
)


@pytest.mark.parametrize(
    "ip",
    [
        # Loopback
        "127.0.0.1",
        "127.255.255.254",
        "::1",
        # Private RFC1918
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        # Link-local & cloud metadata
        "169.254.169.254",
        "169.254.0.1",
        "fe80::1",
        # CGNAT 100.64.0.0/10
        "100.64.0.1",
        "100.127.255.254",
        # Unspecified / 0.0.0.0/8
        "0.0.0.0",
        "0.1.2.3",
        "::",
        # IPv6 ULA fc00::/7
        "fc00::1",
        "fd12:3456:789a::1",
        # IPv4-mapped IPv6 ::ffff:0:0/96
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:8.8.8.8",
        # Multicast & malformed
        "224.0.0.1",
        "ff02::1",
        "fe80::1%eth0",
        "",
        "not-an-ip",
    ],
)
def test_disallowed_addresses_rejected(ip: str) -> None:
    assert is_public_address(ip) is False


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "2001:4860:4860::8888",
        "2606:4700:4700::1111",
    ],
)
def test_global_public_addresses_accepted(ip: str) -> None:
    assert is_public_address(ip) is True


def test_check_resolved_addresses_rejects_mixed_public_and_private() -> None:
    with pytest.raises(SSRFDNSRebindingError, match="SSRF_BLOCKED"):
        check_resolved_addresses("example.edu.vn", ["93.184.216.34", "10.0.0.1"])

    with pytest.raises(SSRFDNSRebindingError, match="SSRF_BLOCKED"):
        check_resolved_addresses("example.edu.vn", ["93.184.216.34", "169.254.169.254"])

    with pytest.raises(SSRFDNSRebindingError, match="SSRF_BLOCKED"):
        check_resolved_addresses("example.edu.vn", ["2001:4860:4860::8888", "::ffff:127.0.0.1"])


def test_check_resolved_addresses_rejects_empty_inputs() -> None:
    with pytest.raises(SSRFDNSRebindingError):
        check_resolved_addresses("", ["8.8.8.8"])
    with pytest.raises(SSRFDNSRebindingError):
        check_resolved_addresses("example.edu.vn", [])


def test_check_resolved_addresses_pins_first_verified_public_ip() -> None:
    res = check_resolved_addresses("example.edu.vn", ["93.184.216.34", "1.1.1.1"])
    assert res.host == "example.edu.vn"
    assert res.pinned_ip == "93.184.216.34"
    assert res.verified_ips == ("93.184.216.34", "1.1.1.1")
