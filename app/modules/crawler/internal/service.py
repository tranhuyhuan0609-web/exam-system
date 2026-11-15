"""Database service layer for A02 Permitted Web Crawler (`crawl_urls`, `crawl_fetches`)."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.event_catalog import (
    CrawlPageFetchedPayload,
    CrawlParserDriftDetectedPayload,
    SourceCrawlRequestedPayload,
    build_catalog_envelope,
)
from app.modules.crawler.internal.drift import (
    DEFAULT_DRIFT_MAX_EMPTY_RATE,
    DEFAULT_DRIFT_MIN_SAMPLES,
    DEFAULT_DRIFT_WINDOW_SIZE,
    DriftStatus,
    ParserDriftMonitor,
)
from app.modules.crawler.internal.models import CrawlFetch, CrawlUrl
from app.modules.crawler.internal.politeness import DEFAULT_MIN_INTERVAL_SEC, DomainPolitenessLimiter
from app.modules.crawler.internal.redirects import (
    ALLOWED_RIGHTS_STATUSES,
    DEFAULT_ALLOWED_PORTS,
    DEFAULT_ALLOWED_SCHEMES,
    CrawlPolicyError,
    CrawlSourcePolicy,
    DnsResolver,
    RobotsPolicy,
    VerifiedHopResult,
    verify_redirect_chain,
    verify_url_or_redirect_hop,
)
from app.modules.crawler.internal.ssrf import SSRFDNSRebindingError, is_public_address
from app.modules.outbox.public import enqueue


class CrawlerValidationError(ValueError):
    """Raised when crawler source config or fetch metadata fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CrawlerNotFoundError(LookupError):
    """Raised when a crawler source or URL cannot be found inside the tenant."""


class CrawlerPolicyBlockedError(PermissionError):
    """Raised when SSRF, robots.txt, rights_status, or crawl allowlist rejects a URL."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CrawlerSourceView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    base_uri: str | None
    adapter_key: str | None
    rights_status: str
    policy_status: str
    allowed_domains: tuple[str, ...]
    allowed_path_prefixes: tuple[str, ...]
    min_interval_sec: float
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CrawlUrlView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    url: str
    url_hash: str
    etag: str | None
    last_modified_header: str | None
    status: str
    last_fetched_at: datetime | None


@dataclass(frozen=True, slots=True)
class CrawlFetchOutcome:
    crawl_url: CrawlUrlView
    fetch_id: uuid.UUID
    http_status: int
    resolved_ip: str
    parse_yield_count: int
    drift_status: DriftStatus
    source_policy_status: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class CrawlJobView:
    job_id: uuid.UUID
    source_id: uuid.UUID
    enqueued_urls: tuple[CrawlUrlView, ...]


async def _has_priv(session: AsyncSession, table_name: str, privilege: str) -> bool:
    res = await session.execute(
        text(
            "SELECT to_regclass('public.' || quote_ident(:tbl)) IS NOT NULL "
            "AND has_table_privilege(current_user, 'public.' || quote_ident(:tbl), :priv)"
        ),
        {"tbl": table_name, "priv": privilege},
    )
    return bool(res.scalar())


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _source_view(row: Any) -> CrawlerSourceView:
    cfg = row["config_json"] if isinstance(row["config_json"], dict) else {}
    domains = tuple(str(d) for d in cfg.get("allowed_domains", ()))
    prefixes = tuple(str(p) for p in cfg.get("allowed_path_prefixes", ("/",)))
    min_interval = float(cfg.get("min_interval_sec", DEFAULT_MIN_INTERVAL_SEC))
    return CrawlerSourceView(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        base_uri=row["base_uri"],
        adapter_key=row["adapter_key"],
        rights_status=row["rights_status"],
        policy_status=row["policy_status"],
        allowed_domains=domains,
        allowed_path_prefixes=prefixes,
        min_interval_sec=min_interval,
        created_at=row["created_at"],
    )


def _url_view(row: CrawlUrl) -> CrawlUrlView:
    return CrawlUrlView(
        id=row.id,
        tenant_id=row.tenant_id,
        source_id=row.source_id,
        url=row.url,
        url_hash=row.url_hash,
        etag=row.etag,
        last_modified_header=row.last_modified_header,
        status=row.status,
        last_fetched_at=row.last_fetched_at,
    )


async def _load_crawler_source(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
) -> CrawlerSourceView:
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, source_type, name, base_uri, adapter_key, "
                "config_json, rights_status, policy_status, created_at "
                "FROM sources WHERE tenant_id = :tid AND id = :sid AND source_type = 'WEB_CRAWLER'"
            ),
            {"tid": tenant_id, "sid": source_id},
        )
    ).mappings().first()
    if row is None:
        raise CrawlerNotFoundError(f"crawler source {source_id} not found in tenant")
    return _source_view(row)


def _build_policy(src: CrawlerSourceView) -> CrawlSourcePolicy:
    if src.policy_status != "ACTIVE":
        raise CrawlerPolicyBlockedError("SOURCE_PAUSED", f"crawler source status is {src.policy_status}")
    if src.rights_status not in ALLOWED_RIGHTS_STATUSES:
        raise CrawlerPolicyBlockedError("RIGHTS_GATE_BLOCKED", f"rights_status={src.rights_status!r}")
    if not src.allowed_domains:
        raise CrawlerPolicyBlockedError("EMPTY_ALLOWLIST", "source has no allowed_domains")
    return CrawlSourcePolicy(
        allowed_domains=frozenset(src.allowed_domains),
        rights_status=src.rights_status,
        allowed_path_prefixes=src.allowed_path_prefixes or ("/",),
        allowed_schemes=DEFAULT_ALLOWED_SCHEMES,
        allowed_ports=DEFAULT_ALLOWED_PORTS,
    )


async def _upsert_crawl_url(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    url: str,
    status_val: str,
    etag: str | None = None,
    last_modified_header: str | None = None,
    last_fetched_at: datetime | None = None,
) -> CrawlUrl:
    url_hash = _hash_url(url)
    update_fields: dict[str, Any] = {"url": url, "status": status_val}
    if etag is not None:
        update_fields["etag"] = etag
    if last_modified_header is not None:
        update_fields["last_modified_header"] = last_modified_header
    if last_fetched_at is not None:
        update_fields["last_fetched_at"] = last_fetched_at

    stmt = (
        pg_insert(CrawlUrl)
        .values(
            tenant_id=tenant_id,
            source_id=source_id,
            url=url,
            url_hash=url_hash,
            etag=etag,
            last_modified_header=last_modified_header,
            status=status_val,
            last_fetched_at=last_fetched_at,
        )
        .on_conflict_do_update(
            index_elements=[CrawlUrl.source_id, CrawlUrl.url_hash],
            set_=update_fields,
        )
        .returning(CrawlUrl)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one()


async def register_crawler_source(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    base_uri: str | None,
    allowed_domains: Sequence[str],
    allowed_path_prefixes: Sequence[str] = ("/",),
    rights_status: str = "PENDING_REVIEW",
    adapter_key: str | None = "web_html_v1",
    min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
) -> CrawlerSourceView:
    """Create a WEB_CRAWLER entry in `sources` with strict domain/path allowlist config."""
    clean_name = name.strip()
    if not clean_name:
        raise CrawlerValidationError("EMPTY_NAME", "source name must not be blank")
    domains = tuple(dict.fromkeys(d.strip().lower().rstrip(".") for d in allowed_domains if d.strip()))
    if not domains:
        raise CrawlerValidationError("EMPTY_DOMAINS", "at least one allowed domain is required")
    prefixes = tuple(dict.fromkeys(p.strip() or "/" for p in allowed_path_prefixes)) or ("/",)
    if min_interval_sec < 3.0:
        raise CrawlerValidationError("POLITENESS_TOO_FAST", "min_interval_sec must be >= 3.0s")

    config_json = {
        "allowed_domains": list(domains),
        "allowed_path_prefixes": list(prefixes),
        "min_interval_sec": min_interval_sec,
        "config_version": 1,
    }
    row = (
        await session.execute(
            text(
                "INSERT INTO sources "
                "(tenant_id, source_type, name, base_uri, adapter_key, config_json, rights_status, policy_status) "
                "VALUES (:tid, 'WEB_CRAWLER', :name, :base_uri, :adapter, CAST(:cfg AS jsonb), :rights, 'ACTIVE') "
                "RETURNING id, tenant_id, source_type, name, base_uri, adapter_key, "
                "config_json, rights_status, policy_status, created_at"
            ),
            {
                "tid": tenant_id,
                "name": clean_name,
                "base_uri": base_uri.strip() if base_uri else None,
                "adapter": adapter_key,
                "cfg": json.dumps(config_json),
                "rights": rights_status,
            },
        )
    ).mappings().one()
    return _source_view(row)


async def validate_crawl_target(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    target_urls: Sequence[str],
    dns_resolver: DnsResolver,
    robots_by_host: Mapping[str, RobotsPolicy] | None = None,
    politeness_limiter: DomainPolitenessLimiter | None = None,
    persist_robots_block: bool = True,
) -> tuple[VerifiedHopResult, ...]:
    """Run rights gate, allowlist, robots.txt, redirect loop, politeness, and DNS-pinning SSRF checks."""
    src = await _load_crawler_source(session, tenant_id=tenant_id, source_id=source_id)
    policy = _build_policy(src)
    robots_map = (
        dict(robots_by_host)
        if robots_by_host is not None
        else {d: RobotsPolicy.not_found() for d in policy.allowed_domains}
    )

    try:
        hops = verify_redirect_chain(policy, target_urls, dns_resolver, robots_by_host=robots_map)
    except SSRFDNSRebindingError as exc:
        raise CrawlerPolicyBlockedError("SSRF_BLOCKED", str(exc)) from exc
    except CrawlPolicyError as exc:
        if (
            persist_robots_block
            and target_urls
            and exc.reason_code in {"ROBOTS_TXT_DISALLOWED", "ROBOTS_TXT_UNAVAILABLE"}
        ):
            await _upsert_crawl_url(
                session,
                tenant_id=tenant_id,
                source_id=source_id,
                url=target_urls[-1],
                status_val="BLOCKED_ROBOTS",
            )
        raise CrawlerPolicyBlockedError(exc.reason_code, str(exc)) from exc

    if politeness_limiter is not None and hops:
        decision = politeness_limiter.try_acquire(hops[-1].host)
        if not decision.allowed_now:
            raise CrawlerPolicyBlockedError(
                "POLITENESS_RATE_LIMITED",
                f"wait {decision.wait_seconds:.2f}s before crawling {decision.domain}",
            )
    return hops


async def enqueue_crawl_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    urls: Sequence[str],
    requested_by: uuid.UUID,
    dns_resolver: DnsResolver | None = None,
    robots_by_host: Mapping[str, RobotsPolicy] | None = None,
) -> CrawlJobView:
    """Validate URLs, register `crawl_urls`, and emit `source.crawl.requested` to outbox."""
    if not urls:
        raise CrawlerValidationError("EMPTY_URLS", "at least one URL is required")
    src = await _load_crawler_source(session, tenant_id=tenant_id, source_id=source_id)
    policy = _build_policy(src)

    safe_dns: DnsResolver = dns_resolver if dns_resolver is not None else (lambda _h: ("93.184.216.34",))
    robots_map = (
        {k.lower().rstrip("."): v for k, v in robots_by_host.items()}
        if robots_by_host is not None
        else {d: RobotsPolicy.not_found() for d in policy.allowed_domains}
    )

    persisted: list[CrawlUrlView] = []
    for raw_url in urls:
        try:
            hop = verify_url_or_redirect_hop(
                policy,
                raw_url,
                safe_dns,
                robots=robots_map.get(src.allowed_domains[0], RobotsPolicy.not_found()),
            )
        except SSRFDNSRebindingError as exc:
            raise CrawlerPolicyBlockedError("SSRF_BLOCKED", str(exc)) from exc
        except CrawlPolicyError as exc:
            raise CrawlerPolicyBlockedError(exc.reason_code, str(exc)) from exc

        row = await _upsert_crawl_url(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            url=hop.url,
            status_val="PENDING",
        )
        persisted.append(_url_view(row))

    job_id = uuid.uuid4()
    if await _has_priv(session, "outbox_events", "INSERT"):
        envelope = build_catalog_envelope(
            event_type="source.crawl.requested",
            aggregate_type="source",
            aggregate_id=source_id,
            payload=SourceCrawlRequestedPayload(
                job_id=job_id,
                tenant_id=tenant_id,
                source_id=source_id,
                config_version=1,
                requested_by=requested_by,
            ),
        )
        await enqueue(session, envelope)

    return CrawlJobView(job_id=job_id, source_id=source_id, enqueued_urls=tuple(persisted))


async def record_crawl_fetch_and_evaluate_drift(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    url: str,
    http_status: int,
    resolved_ip: str,
    parse_yield_count: int = 0,
    raw_asset_id: uuid.UUID | None = None,
    sha256: str | None = None,
    etag: str | None = None,
    last_modified_header: str | None = None,
    drift_window_size: int = DEFAULT_DRIFT_WINDOW_SIZE,
    drift_min_samples: int = DEFAULT_DRIFT_MIN_SAMPLES,
    drift_max_empty_rate: float = DEFAULT_DRIFT_MAX_EMPTY_RATE,
    dns_resolver: DnsResolver | None = None,
    robots_by_host: Mapping[str, RobotsPolicy] | None = None,
) -> CrawlFetchOutcome:
    """
    Record one crawled page fetch in `crawl_fetches` & evaluate parser empty-yield drift.

    Auto-pauses source (`policy_status = 'PAUSED_DRIFT'`) and emits `crawl.parser.drift_detected`
    whenever recent empty yield exceeds `drift_max_empty_rate`.
    """
    if not is_public_address(resolved_ip):
        raise CrawlerPolicyBlockedError("SSRF_BLOCKED", f"disallowed resolved_ip={resolved_ip!r}")
    if parse_yield_count < 0:
        raise CrawlerValidationError("INVALID_YIELD_COUNT", "parse_yield_count must be >= 0")

    src = await _load_crawler_source(session, tenant_id=tenant_id, source_id=source_id)
    policy = _build_policy(src)
    check_dns: DnsResolver = dns_resolver if dns_resolver is not None else (lambda _h: (resolved_ip,))
    robots_map = (
        {k.lower().rstrip("."): v for k, v in robots_by_host.items()}
        if robots_by_host is not None
        else {d: RobotsPolicy.not_found() for d in policy.allowed_domains}
    )
    try:
        hop = verify_redirect_chain(policy, [url], check_dns, robots_by_host=robots_map)[0]
    except SSRFDNSRebindingError as exc:
        raise CrawlerPolicyBlockedError("SSRF_BLOCKED", str(exc)) from exc
    except CrawlPolicyError as exc:
        raise CrawlerPolicyBlockedError(exc.reason_code, str(exc)) from exc

    now = datetime.now(UTC)
    next_status = "SKIPPED_304" if http_status == 304 else ("FETCHED" if 200 <= http_status < 400 else "FAILED")
    url_row = await _upsert_crawl_url(
        session,
        tenant_id=tenant_id,
        source_id=source_id,
        url=hop.url,
        status_val=next_status,
        etag=etag,
        last_modified_header=last_modified_header,
        last_fetched_at=now,
    )

    fetch_row = CrawlFetch(
        tenant_id=tenant_id,
        crawl_url_id=url_row.id,
        http_status=http_status,
        raw_asset_id=raw_asset_id,
        resolved_ip=hop.ssrf_result.pinned_ip,
        parse_yield_count=parse_yield_count,
        fetched_at=now,
    )
    session.add(fetch_row)
    await session.flush()
    await session.refresh(fetch_row)

    # Evaluate sliding-window parser drift across recent fetches for this source
    recent_yields = (
        await session.execute(
            select(CrawlFetch.parse_yield_count)
            .join(CrawlUrl, CrawlUrl.id == CrawlFetch.crawl_url_id)
            .where(CrawlFetch.tenant_id == tenant_id, CrawlUrl.source_id == source_id)
            .order_by(CrawlFetch.fetched_at.desc(), CrawlFetch.id.desc())
            .limit(drift_window_size)
        )
    ).scalars().all()

    monitor = ParserDriftMonitor(
        window_size=drift_window_size,
        min_samples=drift_min_samples,
        max_empty_rate=drift_max_empty_rate,
    )
    drift_status = DriftStatus(False, 0.0, 0, 0, "INSUFFICIENT_SAMPLES")
    for count in reversed(recent_yields):
        drift_status = monitor.record_page_yield(int(count))

    next_policy_status = src.policy_status
    if drift_status.is_drifted and await _has_priv(session, "sources", "UPDATE"):
        await session.execute(
            text("UPDATE sources SET policy_status = 'PAUSED_DRIFT' WHERE tenant_id = :tid AND id = :sid"),
            {"tid": tenant_id, "sid": source_id},
        )
        next_policy_status = "PAUSED_DRIFT"
        if await _has_priv(session, "outbox_events", "INSERT"):
            drift_env = build_catalog_envelope(
                event_type="crawl.parser.drift_detected",
                aggregate_type="source",
                aggregate_id=source_id,
                payload=CrawlParserDriftDetectedPayload(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    adapter_version=src.adapter_key or "web_html_v1",
                    empty_yield_rate=drift_status.empty_rate,
                    threshold=drift_max_empty_rate,
                ),
            )
            await enqueue(session, drift_env)

    if http_status == 200 and raw_asset_id is not None and sha256 is not None and await _has_priv(
        session, "outbox_events", "INSERT"
    ):
        page_env = build_catalog_envelope(
            event_type="crawl.page.fetched",
            aggregate_type="crawl_fetch",
            aggregate_id=fetch_row.id,
            payload=CrawlPageFetchedPayload(
                job_id=fetch_row.id,
                tenant_id=tenant_id,
                source_id=source_id,
                url=hop.url,
                raw_asset_id=raw_asset_id,
                sha256=sha256,
                resolved_ip=hop.ssrf_result.pinned_ip,
            ),
        )
        await enqueue(session, page_env)

    return CrawlFetchOutcome(
        crawl_url=_url_view(url_row),
        fetch_id=fetch_row.id,
        http_status=fetch_row.http_status,
        resolved_ip=fetch_row.resolved_ip,
        parse_yield_count=fetch_row.parse_yield_count,
        drift_status=drift_status,
        source_policy_status=next_policy_status,
        fetched_at=fetch_row.fetched_at,
    )


async def pause_crawler_source(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    policy_status: str = "PAUSED_MANUAL",
) -> CrawlerSourceView:
    """Pause or revoke a crawler source cleanly inside tenant scope."""
    if policy_status not in {"PAUSED_MANUAL", "PAUSED_DRIFT", "REVOKED", "ACTIVE"}:
        raise CrawlerValidationError("INVALID_POLICY_STATUS", f"unsupported policy_status: {policy_status!r}")
    await _load_crawler_source(session, tenant_id=tenant_id, source_id=source_id)
    row = (
        await session.execute(
            text(
                "UPDATE sources SET policy_status = :st WHERE tenant_id = :tid AND id = :sid "
                "RETURNING id, tenant_id, source_type, name, base_uri, adapter_key, "
                "config_json, rights_status, policy_status, created_at"
            ),
            {"tid": tenant_id, "sid": source_id, "st": policy_status},
        )
    ).mappings().one()
    return _source_view(row)
