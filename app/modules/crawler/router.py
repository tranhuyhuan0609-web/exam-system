"""FastAPI /api/v1 routes for A02 Permitted Web Crawler."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.infrastructure.ai_solver import AiSolver
from app.modules.crawler.internal.fetch_import import CrawlFetchError, HostResolver, fetch_and_import
from app.modules.crawler.internal.politeness import DEFAULT_MIN_INTERVAL_SEC
from app.modules.crawler.internal.redirects import RobotsPolicy
from app.modules.crawler.internal.service import (
    CrawlerNotFoundError,
    CrawlerPolicyBlockedError,
    CrawlerSourceView,
    CrawlerValidationError,
    enqueue_crawl_job,
    pause_crawler_source,
    register_crawler_source,
    validate_crawl_target,
)
from app.modules.questions.public import default_numeric_checker
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1/crawler/sources", tags=["crawler"])

# Crawling fills the question bank: authors, reviewers and administrators only (a teacher never feeds the bank).
_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "REVIEWER", "CONTENT_AUTHOR"})
_IMPORT_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "CONTENT_AUTHOR"})


@dataclass(frozen=True, slots=True)
class CrawlNetwork:
    """How a fetch reaches the web: the DNS resolver and the HTTP transport (None: the real ones)."""

    resolve_host: HostResolver | None = None
    transport: httpx.AsyncBaseTransport | None = None


def get_crawl_network() -> CrawlNetwork:
    """The real network; tests override this dependency with an offline resolver and transport."""
    return CrawlNetwork()


def get_crawl_solver() -> AiSolver:
    """The configured AI solver (Gemini / Groq / OpenRouter keys of the settings); tests override it."""
    return AiSolver(numeric_checker=default_numeric_checker())


class CreateCrawlerSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    base_uri: str | None = Field(default=None, max_length=512)
    allowed_domains: list[str] = Field(min_length=1)
    allowed_path_prefixes: list[str] = Field(default_factory=lambda: ["/"])
    rights_status: str = Field(default="PENDING_REVIEW", max_length=40)
    adapter_key: str | None = Field(default="web_html_v1", max_length=80)
    min_interval_sec: float = Field(default=DEFAULT_MIN_INTERVAL_SEC, ge=3.0)


class ValidateCrawlTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(min_length=1)
    dns_observations: dict[str, list[str]] = Field(default_factory=dict)
    robots_txt: dict[str, str | None] | None = None


class CreateCrawlJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(min_length=1)
    dns_observations: dict[str, list[str]] = Field(default_factory=dict)
    robots_txt: dict[str, str | None] | None = None


class FetchAndImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2048)
    max_questions: int = Field(default=20, ge=1, le=100)


class FetchAndImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    url: str
    http_status: int
    fetched_bytes: int
    extracted_count: int
    created_count: int
    duplicate_count: int
    created_question_ids: list[uuid.UUID]
    duplicate_question_ids: list[uuid.UUID]
    skipped: list[dict[str, str]]
    drift_detected: bool
    source_policy_status: str
    fetch_id: uuid.UUID
    warnings: list[str]


class PauseCrawlerSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_status: str = Field(default="PAUSED_MANUAL", max_length=30)


class CrawlerSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    base_uri: str | None
    adapter_key: str | None
    rights_status: str
    policy_status: str
    allowed_domains: list[str]
    allowed_path_prefixes: list[str]
    min_interval_sec: float
    created_at: datetime


class ValidateCrawlTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    hops: list[dict[str, Any]]


class CrawlJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    source_id: uuid.UUID
    enqueued_count: int
    urls: list[dict[str, Any]]


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=instance,
        correlation_id=correlation_id,
        status_override=status_code,
        extensions=extensions,
    )


def _authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    correlation_id: str,
    roles: frozenset[str] = _STAFF_ROLES,
) -> ActorContext | JSONResponse:
    get_settings()
    try:
        actor = verify_actor(request, credentials, allow_cookie=True)
    except TenantSpoofingError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except AuthTokenError as exc:
        return _problem_response(
            code=PlatformErrorCode.UNAUTHORIZED,
            detail=str(exc),
            status_code=401,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    if not any(r.upper() in roles for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Staff or admin role required for crawler operations",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    return actor


def _serialize_source(view: CrawlerSourceView) -> CrawlerSourceResponse:
    return CrawlerSourceResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        name=view.name,
        base_uri=view.base_uri,
        adapter_key=view.adapter_key,
        rights_status=view.rights_status,
        policy_status=view.policy_status,
        allowed_domains=list(view.allowed_domains),
        allowed_path_prefixes=list(view.allowed_path_prefixes),
        min_interval_sec=view.min_interval_sec,
        created_at=view.created_at,
    )


def _build_robots_map(raw: dict[str, str | None] | None) -> dict[str, RobotsPolicy] | None:
    if raw is None:
        return None
    out: dict[str, RobotsPolicy] = {}
    for host, content in raw.items():
        out[host] = RobotsPolicy.not_found() if content is None else RobotsPolicy.with_content(content)
    return out


@router.post("", response_model=CrawlerSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source_endpoint(
    payload: CreateCrawlerSourceRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            created = await register_crawler_source(
                session,
                tenant_id=auth.tenant_id,
                name=payload.name,
                base_uri=payload.base_uri,
                allowed_domains=payload.allowed_domains,
                allowed_path_prefixes=payload.allowed_path_prefixes,
                rights_status=payload.rights_status,
                adapter_key=payload.adapter_key,
                min_interval_sec=payload.min_interval_sec,
            )
    except CrawlerValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_source(created)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{source_id}/validate", response_model=ValidateCrawlTargetResponse)
async def validate_source_endpoint(
    source_id: uuid.UUID,
    payload: ValidateCrawlTargetRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    dns_map = {k.lower().rstrip("."): v for k, v in payload.dns_observations.items()}
    robots_map = _build_robots_map(payload.robots_txt)
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            hops = await validate_crawl_target(
                session,
                tenant_id=auth.tenant_id,
                source_id=source_id,
                target_urls=payload.urls,
                dns_resolver=lambda h: dns_map.get(h, ()),
                robots_by_host=robots_map,
            )
    except CrawlerNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except CrawlerPolicyBlockedError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    body = ValidateCrawlTargetResponse(
        valid=True,
        hops=[
            {
                "url": h.url,
                "host": h.host,
                "port": h.port,
                "pinned_ip": h.ssrf_result.pinned_ip,
                "hop_count": h.hop_count,
            }
            for h in hops
        ],
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{source_id}/crawl-jobs", response_model=CrawlJobResponse, status_code=status.HTTP_201_CREATED)
async def enqueue_crawl_job_endpoint(
    source_id: uuid.UUID,
    payload: CreateCrawlJobRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    dns_map = {k.lower().rstrip("."): v for k, v in payload.dns_observations.items()}
    resolver = (lambda h: dns_map.get(h, ())) if dns_map else None
    robots_map = _build_robots_map(payload.robots_txt)
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            job = await enqueue_crawl_job(
                session,
                tenant_id=auth.tenant_id,
                source_id=source_id,
                urls=payload.urls,
                requested_by=auth.user_id,
                dns_resolver=resolver,
                robots_by_host=robots_map,
            )
    except CrawlerNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except CrawlerPolicyBlockedError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )
    except CrawlerValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    body = CrawlJobResponse(
        job_id=job.job_id,
        source_id=job.source_id,
        enqueued_count=len(job.enqueued_urls),
        urls=[{"id": str(u.id), "url": u.url, "status": u.status} for u in job.enqueued_urls],
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{source_id}/pause", response_model=CrawlerSourceResponse)
async def pause_source_endpoint(
    source_id: uuid.UUID,
    request: Request,
    payload: PauseCrawlerSourceRequest | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    target_status = (payload or PauseCrawlerSourceRequest()).policy_status
    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            updated = await pause_crawler_source(
                session,
                tenant_id=auth.tenant_id,
                source_id=source_id,
                policy_status=target_status,
            )
    except CrawlerNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except CrawlerValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"policy_code": exc.code},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_source(updated)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/{source_id}/fetch-and-import", response_model=FetchAndImportResponse)
async def fetch_and_import_endpoint(
    source_id: uuid.UUID,
    payload: FetchAndImportRequest,
    request: Request,
    credentials: OptionalBearer = None,
    network: CrawlNetwork = Depends(get_crawl_network),  # noqa: B008
    solver: AiSolver = Depends(get_crawl_solver),  # noqa: B008
) -> JSONResponse:
    """Fetch one permitted page and add the questions on it to the bank as solved, tagged DRAFTs (dedup by hash).

    403 `policy_code` for a gate (SSRF_BLOCKED, ROBOTS_TXT_DISALLOWED, RIGHTS_GATE_BLOCKED, SOURCE_PAUSED, ...),
    502 when the page cannot be fetched or answers another status than 200 (the fetch is still recorded).
    """
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id, _IMPORT_ROLES)
    if isinstance(auth, JSONResponse):
        return auth
    instance = str(request.url.path)
    try:
        # no transaction here: the function opens short ones around the database steps, never around network or AI
        result = await fetch_and_import(
            tenant_id=auth.tenant_id,
            source_id=source_id,
            url=payload.url,
            created_by=auth.user_id,
            max_questions=payload.max_questions,
            solver=solver,
            resolve_host=network.resolve_host,
            transport=network.transport,
        )
    except CrawlerNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND, detail=str(exc), status_code=404, instance=instance,
            correlation_id=correlation_id,
        )  # fmt: skip
    except CrawlerPolicyBlockedError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN, detail=str(exc), status_code=403, instance=instance,
            correlation_id=correlation_id, extensions={"policy_code": exc.code},
        )  # fmt: skip
    except CrawlerValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR, detail=str(exc), status_code=422, instance=instance,
            correlation_id=correlation_id, extensions={"policy_code": exc.code},
        )  # fmt: skip
    except CrawlFetchError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR, detail=str(exc), status_code=502, instance=instance,
            correlation_id=correlation_id, extensions={"policy_code": "UPSTREAM_FETCH_FAILED"},
        )  # fmt: skip
    body = FetchAndImportResponse(
        source_id=result.source_id,
        url=result.url,
        http_status=result.http_status,
        fetched_bytes=result.fetched_bytes,
        extracted_count=result.extracted_count,
        created_count=len(result.created_question_ids),
        duplicate_count=len(result.duplicate_question_ids),
        created_question_ids=list(result.created_question_ids),
        duplicate_question_ids=list(result.duplicate_question_ids),
        skipped=[{"label": label, "reason": reason} for label, reason in result.skipped],
        drift_detected=result.drift_detected,
        source_policy_status=result.source_policy_status,
        fetch_id=result.fetch_id,
        warnings=list(result.warnings),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if result.http_status == 200 else status.HTTP_502_BAD_GATEWAY,
        content=jsonable_encoder(body),
        headers={"X-Correlation-ID": correlation_id},
    )
