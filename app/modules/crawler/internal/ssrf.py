"""Pure anti-SSRF address validation for A02 permitted web crawler.

No network calls and no DNS resolution inside this module: callers pass
already-resolved IP address strings to ``check_resolved_addresses``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass

_IPV4_MAPPED_NETWORK = ipaddress.IPv6Network("::ffff:0:0/96")
_CGNAT_NETWORK = ipaddress.IPv4Network("100.64.0.0/10")
_THIS_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")
_ULA_NETWORK = ipaddress.IPv6Network("fc00::/7")
_LINK_LOCAL_V6 = ipaddress.IPv6Network("fe80::/10")


class SSRFDNSRebindingError(PermissionError):
    """Raised when a host resolves to any non-public or blocked IP address."""


@dataclass(frozen=True)
class SSRFCheckResult:
    """Validated resolution result with pinned primary global IP."""

    host: str
    pinned_ip: str
    verified_ips: tuple[str, ...]


def is_public_address(ip: str) -> bool:
    """Return True iff ``ip`` is a global, non-multicast, non-internal address.

    Explicitly rejects loopback, private, link-local, CGNAT (100.64.0.0/10),
    0.0.0.0/8, IPv6 ::1, fc00::/7, fe80::/10, and IPv4-mapped IPv6 (::ffff:0:0/96).
    """
    raw = (ip or "").strip()
    if not raw or "%" in raw:
        return False
    try:
        ip_obj = ipaddress.ip_address(raw)
    except ValueError:
        return False

    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None or ip_obj in _IPV4_MAPPED_NETWORK:
            return False
        if ip_obj in _ULA_NETWORK or ip_obj in _LINK_LOCAL_V6:
            return False
    else:
        if ip_obj in _CGNAT_NETWORK or ip_obj in _THIS_NETWORK:
            return False

    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return False

    return bool(ip_obj.is_global)


def check_resolved_addresses(host: str, addresses: Sequence[str]) -> SSRFCheckResult:
    """Validate every resolved IP address for ``host``; reject if any is non-public."""
    clean_host = (host or "").strip()
    if not clean_host:
        raise SSRFDNSRebindingError("SSRF_BLOCKED: empty host")
    if not addresses:
        raise SSRFDNSRebindingError(f"SSRF_BLOCKED: no resolved addresses for {clean_host}")

    verified: list[str] = []
    for raw_ip in addresses:
        clean_ip = (raw_ip or "").strip()
        if not is_public_address(clean_ip):
            raise SSRFDNSRebindingError(f"SSRF_BLOCKED: host {clean_host} resolved to disallowed address {raw_ip!r}")
        verified.append(str(ipaddress.ip_address(clean_ip)))

    return SSRFCheckResult(
        host=clean_host,
        pinned_ip=verified[0],
        verified_ips=tuple(verified),
    )
