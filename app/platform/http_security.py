"""HTTP security headers, strict CORS origin validation, and spoof-resistant proxy client IP resolution."""

from __future__ import annotations

import ipaddress
import re

_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?$")


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
        origin = raw.strip().rstrip("/")
        if not origin:
            raise SecurityConfigError("CORS origin entries cannot be empty")
        if origin == "*":
            if allow_credentials:
                raise SecurityConfigError("CORS wildcard '*' is forbidden when allow_credentials=True")
            normalized.append("*")
            continue
        if not _ORIGIN_RE.match(origin):
            raise SecurityConfigError(f"invalid CORS origin syntax: {raw!r}")
        normalized.append(origin.lower())
    return tuple(dict.fromkeys(normalized))


def is_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: tuple[str, ...],
    allow_credentials: bool,
) -> bool:
    """Check whether an incoming Origin header matches the validated CORS allowlist."""
    if not origin:
        return False
    validated = validate_cors_origins(allowed_origins, allow_credentials=allow_credentials)
    candidate = origin.strip().rstrip("/").lower()
    if "*" in validated:
        return True
    return candidate in validated


def extract_client_ip(
    *,
    remote_addr: str,
    x_forwarded_for: str | None,
    trusted_proxy_cidrs: tuple[str, ...] = (),
) -> str:
    """Resolve client IP from X-Forwarded-For walking right-to-left across trusted proxy CIDRs only."""
    try:
        direct_ip = ipaddress.ip_address(remote_addr.strip())
    except ValueError as exc:
        raise SecurityConfigError(f"invalid remote_addr: {remote_addr!r}") from exc

    trusted_nets = [ipaddress.ip_network(cidr.strip(), strict=False) for cidr in trusted_proxy_cidrs]
    if not trusted_nets or not any(direct_ip in net for net in trusted_nets):
        return str(direct_ip)

    if not x_forwarded_for or not x_forwarded_for.strip():
        return str(direct_ip)

    chain: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for part in x_forwarded_for.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            chain.append(ipaddress.ip_address(candidate))
        except ValueError:
            # Stop trusting forged right-side header corruption if encountered
            return str(direct_ip)

    for ip_obj in reversed(chain):
        if any(ip_obj in net for net in trusted_nets):
            continue
        return str(ip_obj)

    return str(chain[0]) if chain else str(direct_ip)
