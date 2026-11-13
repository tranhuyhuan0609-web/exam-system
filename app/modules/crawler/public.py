"""Public entry point of the crawler module (A02)."""

from __future__ import annotations

from app.modules.crawler.internal.ssrf import (
    SSRFCheckResult,
    SSRFDNSRebindingError,
    check_resolved_addresses,
    is_public_address,
)

__all__ = [
    "SSRFCheckResult",
    "SSRFDNSRebindingError",
    "check_resolved_addresses",
    "is_public_address",
]
