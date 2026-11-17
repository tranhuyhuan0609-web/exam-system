"""A02 fetch-and-import: one permitted page -> questions in the bank (DRAFT), with every crawl guard on the way.

1. the source's gates (ACTIVE, rights cleared, allowlist, path prefixes, https:443) and the URL's DNS answers
   (every address public, pinned), through `validate_crawl_target`; the host's robots.txt is fetched the same way
   and obeyed; the per-domain politeness interval applies;
2. the page is fetched from the pinned address only (no redirect is followed, the body is capped, and on a real
   socket the peer address is checked against the pin, so a DNS answer that changes between check and fetch is
   refused);
3. the questions are extracted (`html_extractor`), solved and tagged (`ai_solver`, through C01's
   `solve_and_ingest`), deduplicated on the content hash and stored as DRAFTs;
4. the fetch and its yield are recorded (`record_crawl_fetch_and_evaluate_drift`), which pauses a source whose parser
   keeps finding nothing.

Three phases, so no database connection is held while the network or the AI is slow: (1) a short transaction reads
the source and the topics; (2) DNS, robots.txt, the page, the extraction and the solving run with NO transaction
open (the AI at most `SOLVE_CONCURRENCY` questions at a time); (3) a short transaction records the fetch and stores
the solved questions. A failure in phase 3 loses the fetch record and the questions together, never half of them.

The network (`resolve_host`, `transport`) is injectable: tests run offline, production uses the system resolver.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.infrastructure.ai_solver import AiSolver
from app.modules.crawler.internal.html_extractor import ExtractionError, extract_questions
from app.modules.crawler.internal.politeness import DomainPolitenessLimiter
from app.modules.crawler.internal.redirects import (
    DEFAULT_USER_AGENT,
    CrawlPolicyError,
    RobotsPolicy,
    VerifiedHopResult,
    verify_redirect_chain,
)
from app.modules.crawler.internal.service import (
    CrawlerPolicyBlockedError,
    CrawlerValidationError,
    _build_policy,
    _load_crawler_source,
    record_crawl_fetch_and_evaluate_drift,
)
from app.modules.crawler.internal.ssrf import SSRFDNSRebindingError, is_public_address
from app.modules.questions.public import (
    RawQuestion,
    load_taxonomy_hints,
    solve_many,
    store_prepared,
)
from app.platform.tenancy import DbRole, tenant_transaction

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 256 * 1024
FETCH_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_QUESTIONS = 20
QUESTION_RIGHTS = frozenset({"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"})
SOLVE_CONCURRENCY = 4

HostResolver = Callable[[str], Sequence[str]]

# One limiter per process: the interval between two fetches of a domain holds across requests.
_POLITENESS = DomainPolitenessLimiter()


class CrawlFetchError(RuntimeError):
    """The page could not be fetched (network, status, size): nothing was imported."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    status: int
    content_type: str | None
    body: bytes
    resolved_ip: str
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class FetchImportResult:
    source_id: uuid.UUID
    url: str
    http_status: int
    fetched_bytes: int
    extracted_count: int
    created_question_ids: tuple[uuid.UUID, ...]
    duplicate_question_ids: tuple[uuid.UUID, ...]
    skipped: tuple[tuple[str, str], ...]  # (label, reason)
    drift_detected: bool
    source_policy_status: str
    fetch_id: uuid.UUID
    warnings: tuple[str, ...] = field(default_factory=tuple)


def system_resolver(host: str) -> list[str]:
    """Every address the system resolver gives for `host` (blocking: call it in a thread)."""
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _peer_ip(response: httpx.Response) -> str | None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    addr = stream.get_extra_info("server_addr")
    return None if not addr else str(addr[0])


async def _get(
    client: httpx.AsyncClient, url: str, *, pinned: Sequence[str], max_bytes: int, offline: bool
) -> FetchedPage:
    try:
        async with client.stream("GET", url, headers={"User-Agent": DEFAULT_USER_AGENT}) as response:
            peer = _peer_ip(response)
            if peer is None and not offline:
                raise CrawlerPolicyBlockedError("SSRF_BLOCKED", "the connection's peer address could not be read")
            if peer is not None:
                normalized = str(ipaddress.ip_address(peer))
                if normalized not in {str(ipaddress.ip_address(p)) for p in pinned} or not is_public_address(peer):
                    raise CrawlerPolicyBlockedError("SSRF_BLOCKED", f"connected to {peer}, not to the checked address")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise CrawlFetchError(f"the page is larger than {max_bytes} bytes")
            return FetchedPage(
                url=url,
                status=response.status_code,
                content_type=response.headers.get("content-type"),
                body=bytes(body),
                resolved_ip=peer or pinned[0],
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
    except httpx.HTTPError as exc:
        raise CrawlFetchError(f"{type(exc).__name__}: {exc}") from exc


async def _robots(client: httpx.AsyncClient, url: str, *, pinned: Sequence[str], offline: bool) -> RobotsPolicy:
    parts = urlsplit(url)
    try:
        page = await _get(
            client, f"{parts.scheme}://{parts.netloc}/robots.txt", pinned=pinned, max_bytes=MAX_ROBOTS_BYTES,
            offline=offline,
        )  # fmt: skip
    except CrawlFetchError:
        return RobotsPolicy.unavailable()
    if page.status in (404, 410):
        return RobotsPolicy.not_found()
    if page.status != 200:
        return RobotsPolicy.unavailable()
    return RobotsPolicy.with_content(page.body.decode("utf-8", errors="replace"))


def _verify(policy: Any, host: str, url: str, addresses: Sequence[str], robots: RobotsPolicy) -> VerifiedHopResult:
    """The pure crawl gates (allowlist, https, path prefix, robots, SSRF on the DNS answers) for one URL."""
    try:
        return verify_redirect_chain(
            policy,
            [url],
            lambda h: list(addresses) if h.lower().rstrip(".") == host else [],
            robots_by_host={host: robots},
        )[0]
    except SSRFDNSRebindingError as exc:
        raise CrawlerPolicyBlockedError("SSRF_BLOCKED", str(exc)) from exc
    except CrawlPolicyError as exc:
        raise CrawlerPolicyBlockedError(exc.reason_code, str(exc)) from exc


async def fetch_and_import(
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    url: str,
    created_by: uuid.UUID,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    solver: AiSolver | None = None,
    resolve_host: HostResolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    politeness: DomainPolitenessLimiter | None = None,
) -> FetchImportResult:
    """Fetch one permitted page and put the questions it holds into the tenant's bank (see the module docstring)."""
    if not 1 <= max_questions <= 100:
        raise CrawlerValidationError("INVALID_MAX_QUESTIONS", "max_questions must be within 1..100")
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        raise CrawlerValidationError("INVALID_URL", "the URL has no host")

    # phase 1: the source and the topics (short transaction)
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        source = await _load_crawler_source(session, tenant_id=tenant_id, source_id=source_id)
        policy = _build_policy(source)
        hints = await load_taxonomy_hints(session)
    rights = source.rights_status if source.rights_status in QUESTION_RIGHTS else "PENDING_REVIEW"

    # phase 2: network, extraction and AI, with no transaction open
    resolver = resolve_host or system_resolver
    try:
        addresses = list(await asyncio.to_thread(resolver, host))
    except OSError as exc:
        raise CrawlFetchError(f"DNS lookup of {host} failed: {exc}") from exc
    offline = transport is not None
    _verify(policy, host, url, addresses, RobotsPolicy.not_found())  # no request leaves for a bad address
    async with httpx.AsyncClient(transport=transport, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client:
        pinned = [_verify(policy, host, url, addresses, RobotsPolicy.not_found()).ssrf_result.pinned_ip]
        robots = await _robots(client, url, pinned=pinned, offline=offline)
        hop = _verify(policy, host, url, pinned, robots)
        decision = (politeness or _POLITENESS).try_acquire(hop.host)
        if not decision.allowed_now:
            raise CrawlerPolicyBlockedError(
                "POLITENESS_RATE_LIMITED", f"wait {decision.wait_seconds:.2f}s before crawling {decision.domain}"
            )
        page = await _get(client, url, pinned=pinned, max_bytes=MAX_PAGE_BYTES, offline=offline)

    if 300 <= page.status < 400:
        raise CrawlFetchError(f"HTTP {page.status}: redirects are not followed, crawl the target URL itself")
    warnings: list[str] = []
    found: list[RawQuestion] = []
    if page.status == 200:
        try:
            found = extract_questions(page.body, page.content_type)
        except ExtractionError as exc:
            warnings.append(f"EXTRACTION_FAILED:{exc}")
    active = solver or AiSolver(numeric_checker=_numeric_checker())
    prepared = await solve_many(active, found[:max_questions], hints, concurrency=SOLVE_CONCURRENCY)
    if len(found) > max_questions:
        warnings.append(f"TRUNCATED:{len(found)}>{max_questions}")

    # phase 3: record the fetch and store the questions (short transaction)
    created: list[uuid.UUID] = []
    duplicates: list[uuid.UUID] = []
    skipped: list[tuple[str, str]] = []
    prefix = f"CRAWL-{str(source_id)[:8].upper()}-{hashlib.sha256(url.encode()).hexdigest()[:6].upper()}"
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        outcome = await record_crawl_fetch_and_evaluate_drift(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            url=url,
            http_status=page.status,
            resolved_ip=page.resolved_ip,
            parse_yield_count=len(found),
            sha256=hashlib.sha256(page.body).hexdigest(),
            etag=page.etag,
            last_modified_header=page.last_modified,
            dns_resolver=lambda _h: [page.resolved_ip],
            robots_by_host={host: RobotsPolicy.not_found()},
        )
        if page.status == 200:
            for index, item in enumerate(prepared, start=1):
                result = await store_prepared(
                    session,
                    prepared=item,
                    tenant_id=tenant_id,
                    created_by=created_by,
                    stable_code=f"{prefix}-{index:03d}",
                    rights_status=rights,
                    provenance={"source_id": str(source_id), "url": url, "label": item.raw.source_label},
                )
                if result.ingested is None:
                    skipped.append((item.raw.source_label or f"#{index}", result.skipped_reason or "SKIPPED"))
                elif result.ingested.created:
                    created.append(result.ingested.question.id)
                else:
                    duplicates.append(result.ingested.question.id)
    if page.status != 200:
        warnings.append(f"UPSTREAM_STATUS:{page.status}")
    return FetchImportResult(
        source_id=source_id,
        url=url,
        http_status=page.status,
        fetched_bytes=len(page.body),
        extracted_count=len(found),
        created_question_ids=tuple(created),
        duplicate_question_ids=tuple(dict.fromkeys(duplicates)),
        skipped=tuple(skipped),
        drift_detected=outcome.drift_status.is_drifted,
        source_policy_status=outcome.source_policy_status,
        fetch_id=outcome.fetch_id,
        warnings=tuple(warnings),
    )


def _numeric_checker() -> Any:
    from app.modules.questions.public import default_numeric_checker

    return default_numeric_checker()
