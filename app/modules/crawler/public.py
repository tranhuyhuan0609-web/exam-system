"""Public entry point of the crawler module (A02)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

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
    RobotsPolicy,
    RobotsStatus,
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

if TYPE_CHECKING:
    from app.modules.crawler.internal.service import (
        CrawlerNotFoundError,
        CrawlerPolicyBlockedError,
        CrawlerSourceView,
        CrawlerValidationError,
        CrawlFetchOutcome,
        CrawlJobView,
        CrawlUrlView,
        enqueue_crawl_job,
        pause_crawler_source,
        record_crawl_fetch_and_evaluate_drift,
        register_crawler_source,
        validate_crawl_target,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "CrawlFetchOutcome",
        "CrawlJobView",
        "CrawlUrlView",
        "CrawlerNotFoundError",
        "CrawlerPolicyBlockedError",
        "CrawlerSourceView",
        "CrawlerValidationError",
        "enqueue_crawl_job",
        "pause_crawler_source",
        "record_crawl_fetch_and_evaluate_drift",
        "register_crawler_source",
        "validate_crawl_target",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.crawler.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "CrawlFetchOutcome",
    "CrawlJobView",
    "CrawlPolicyError",
    "CrawlSourcePolicy",
    "CrawlUrlView",
    "CrawlerNotFoundError",
    "CrawlerPolicyBlockedError",
    "CrawlerSourceView",
    "CrawlerValidationError",
    "DnsResolver",
    "DomainPolitenessLimiter",
    "DriftStatus",
    "ParserDriftMonitor",
    "PolitenessDecision",
    "RobotsPolicy",
    "RobotsStatus",
    "SSRFCheckResult",
    "SSRFDNSRebindingError",
    "VerifiedHopResult",
    "check_resolved_addresses",
    "enqueue_crawl_job",
    "is_public_address",
    "pause_crawler_source",
    "record_crawl_fetch_and_evaluate_drift",
    "register_crawler_source",
    "validate_crawl_target",
    "verify_redirect_chain",
    "verify_url_or_redirect_hop",
]
