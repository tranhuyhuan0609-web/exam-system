"""HTTP security headers, strict CORS origin validation, and spoof-resistant proxy client IP resolution."""

from __future__ import annotations

import ipaddress
import re

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_ORIGIN_RE = re.compile(rf"(?i:https?)://{_LABEL}(?:\.{_LABEL})*(?::(\d{{1,5}}))?", re.ASCII)
MAX_FORWARDED_FOR_LENGTH = 2048
MAX_FORWARDED_HOPS = 50


class SecurityConfigError(ValueError):
    """Raised when CORS or reverse-proxy trust configuration violates security invariants."""


def build_security_headers(
    *,
    hsts_enabled: bool = True,
    allow_same_origin_frame: bool = False,
) -> dict[str, str]:
    """Build baseline V11.3 HTTP response security headers compatible with Next.js + KaTeX."""
    frame_ancestors = "'self'" if allow_same_origin_frame else "'none'"
    csp = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "object-src 'none'",
            "base-uri 'self'",
            f"frame-ancestors {frame_ancestors}",
        ]
    )
    headers: dict[str, str] = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN" if allow_same_origin_frame else "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if hsts_enabled:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def validate_cors_origins(
    allowed_origins: tuple[str, ...],
    *,
    allow_credentials: bool,
) -> tuple[str, ...]:
    """Validate CORS origin list: forbid wildcard '*' with credentials and reject pathful origins."""
    normalized: list[str] = []
    for raw in allowed_origins:
        if not isinstance(raw, str):
            raise SecurityConfigError("CORS origin entries must be strings")
        origin = raw.strip().rstrip("/")
        if not origin:
            raise SecurityConfigError("CORS origin entries cannot be empty")
        if origin == "*":
            if allow_credentials:
                raise SecurityConfigError("CORS wildcard '*' is forbidden when allow_credentials=True")
            normalized.append("*")
            continue
        match = _ORIGIN_RE.fullmatch(origin)
        if match is None or (match.group(1) is not None and int(match.group(1)) > 65535):
            raise SecurityConfigError("invalid CORS origin syntax")
        normalized.append(origin.lower())
    return tuple(dict.fromkeys(normalized))


def is_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: tuple[str, ...],
    allow_credentials: bool,
) -> bool:
    """Check whether an incoming Origin header matches the validated CORS allowlist."""
    if not origin or not isinstance(origin, str):
        return False
    validated = validate_cors_origins(allowed_origins, allow_credentials=allow_credentials)
    candidate = origin.strip().rstrip("/").lower()
    if "*" in validated:
        return True
    return candidate in validated


def _parse_ip(raw: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a literal IP; IPv4-mapped IPv6 is unwrapped, zone ids and junk are rejected."""
    if not isinstance(raw, str):
        return None
    try:
        ip_obj = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.scope_id is not None:
            return None
        if ip_obj.ipv4_mapped is not None:
            return ip_obj.ipv4_mapped
    return ip_obj


def _parse_trusted_networks(cidrs: tuple[str, ...]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        if not isinstance(cidr, str):
            raise SecurityConfigError("invalid trusted proxy CIDR")
        try:
            nets.append(ipaddress.ip_network(cidr.strip(), strict=False))
        except ValueError as exc:
            raise SecurityConfigError("invalid trusted proxy CIDR") from exc
    return nets


def extract_client_ip(
    *,
    remote_addr: str,
    x_forwarded_for: str | None,
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> str:
    """Resolve client IP from X-Forwarded-For walking right-to-left across trusted proxy CIDRs only."""
    direct_ip = _parse_ip(remote_addr)
    if direct_ip is None:
        raise SecurityConfigError("invalid remote_addr")

    trusted_nets = _parse_trusted_networks(trusted_proxy_cidrs)
    if not trusted_nets or not any(direct_ip in net for net in trusted_nets):
        return str(direct_ip)

    if (
        not isinstance(x_forwarded_for, str)
        or not x_forwarded_for.strip()
        or len(x_forwarded_for) > MAX_FORWARDED_FOR_LENGTH
    ):
        return str(direct_ip)

    chain: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for part in x_forwarded_for.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        parsed = _parse_ip(candidate)
        if parsed is None or len(chain) >= MAX_FORWARDED_HOPS:
            # Stop trusting forged or oversized header chains
            return str(direct_ip)
        chain.append(parsed)

    for ip_obj in reversed(chain):
        if any(ip_obj in net for net in trusted_nets):
            continue
        return str(ip_obj)

    return str(chain[0]) if chain else str(direct_ip)
