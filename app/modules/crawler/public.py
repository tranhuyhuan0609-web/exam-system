"""Public entry point of the crawler module (A02)."""

from __future__ import annotations

from app.modules.crawler.internal.drift import (
    DEFAULT_DRIFT_MAX_EMPTY_RATE,
    DEFAULT_DRIFT_MIN_SAMPLES,
    DEFAULT_DRIFT_WINDOW_SIZE,
    DriftStatus,
    ParserDriftMonitor,
)
from app.modules.crawler.internal.politeness import (
    DEFAULT_MIN_INTERVAL_SEC,
    DomainPolitenessLimiter,
    PolitenessDecision,
)
from app.modules.crawler.internal.redirects import (
    ALLOWED_RIGHTS_STATUSES,
    DEFAULT_ALLOWED_PATH_PREFIXES,
    DEFAULT_ALLOWED_PORTS,
    DEFAULT_ALLOWED_SCHEMES,
    DEFAULT_MAX_REDIRECTS,
    CrawlPolicyError,
    CrawlSourcePolicy,
    DnsResolver,
    VerifiedHopResult,
    verify_redirect_chain,
    verify_url_or_redirect_hop,
)
from app.modules.crawler.internal.ssrf import (
    SSRFCheckResult,
    SSRFDNSRebindingError,
    check_resolved_addresses,
    is_public_address,
)

__all__ = [
    "ALLOWED_RIGHTS_STATUSES",
    "DEFAULT_ALLOWED_PATH_PREFIXES",
    "DEFAULT_ALLOWED_PORTS",
    "DEFAULT_ALLOWED_SCHEMES",
    "DEFAULT_DRIFT_MAX_EMPTY_RATE",
    "DEFAULT_DRIFT_MIN_SAMPLES",
    "DEFAULT_DRIFT_WINDOW_SIZE",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MIN_INTERVAL_SEC",
    "CrawlPolicyError",
    "CrawlSourcePolicy",
    "DnsResolver",
    "DomainPolitenessLimiter",
    "DriftStatus",
    "ParserDriftMonitor",
    "PolitenessDecision",
    "SSRFCheckResult",
    "SSRFDNSRebindingError",
    "VerifiedHopResult",
    "check_resolved_addresses",
    "is_public_address",
    "verify_redirect_chain",
    "verify_url_or_redirect_hop",
]
