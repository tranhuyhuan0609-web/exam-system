"""FastAPI /api/v1 routes for A07: normalize an extraction run into candidates, read, split and merge them."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.extraction.internal.rubric_table import TableCellSpan, TableGridLimitError
from app.modules.extraction.internal.service import (
    MAX_MERGE,
    MAX_PARTS,
    CandidateConflictError,
    CandidateNotFoundError,
    ContentCandidateRecord,
    QuestionCandidateRecord,
    get_candidate,
    merge_candidates,
    normalize_extraction_run,
    split_candidate,
)
from app.modules.extraction.internal.splitter import PageSegmentInput
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["extraction"])


WRITE_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
MAX_PAGES = 500


class PageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_no: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 595.0, 842.0)


class CellIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=2000)
    rowspan: int = Field(default=1, ge=1, le=50)
    colspan: int = Field(default=1, ge=1, le=10)


class NormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_run_id: uuid.UUID
    title: str = Field(default="Chuyên đề", min_length=1, max_length=500)
    pages: list[PageIn] | None = Field(default=None, max_length=MAX_PAGES)
    rubric_rows: list[list[CellIn]] | None = Field(default=None, max_length=2000)
    rubric_expected_total: Decimal | None = Field(default=None, gt=0, le=1000)


class SplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[str] = Field(min_length=2, max_length=MAX_PARTS)


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge_candidate_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_MERGE)


def _problem(
    request: Request,
    correlation_id: str,
    code: PlatformErrorCode,
    detail: str,
    status_override: int | None = None,
    extensions: dict[str, Any] | None = None,
) -> JSONResponse:
    return problem_json(
        code=code,
        detail=detail,
        instance=request.url.path,
        correlation_id=correlation_id,
        status_override=status_override,
        extensions=extensions,
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, *, write: bool
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    try:
        actor = verify_actor(request, credentials)
    except AuthTokenError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.UNAUTHORIZED, str(exc))
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(request, correlation_id, PlatformErrorCode.FORBIDDEN, str(exc))
    if write and not ({r.upper() for r in actor.roles} & WRITE_ROLES):
        return (
            None,
            correlation_id,
            _problem(
                request, correlation_id, PlatformErrorCode.FORBIDDEN, "a content role is required to change candidates"
            ),
        )
    return actor, correlation_id, None


def _body(record: QuestionCandidateRecord | ContentCandidateRecord) -> dict[str, Any]:
    kind = "QUESTION" if isinstance(record, QuestionCandidateRecord) else "CONTENT"
    return {"candidate_kind": kind, **jsonable_encoder(record)}


def _conflict(request: Request, correlation_id: str, exc: CandidateConflictError) -> JSONResponse:
    return _problem(
        request,
        correlation_id,
        PlatformErrorCode.VALIDATION_ERROR,
        str(exc),
        status_override=status.HTTP_409_CONFLICT,
        extensions={"reason": exc.code},
    )


@router.post("/candidates/normalize", status_code=status.HTTP_201_CREATED)
async def normalize_endpoint(request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = NormalizeRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    pages = None
    if payload.pages is not None:
        pages = [PageSegmentInput(page_no=p.page_no, text=p.text, bbox=p.bbox) for p in payload.pages]
    rubric = None
    if payload.rubric_rows is not None:
        rubric = [
            [TableCellSpan(text=c.text, rowspan=c.rowspan, colspan=c.colspan) for c in row]
            for row in payload.rubric_rows
        ]
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await normalize_extraction_run(
                session,
                tenant_id=actor.tenant_id,
                extraction_run_id=payload.extraction_run_id,
                pages=pages,
                title=payload.title,
                rubric_rows=rubric,
                rubric_expected_total=payload.rubric_expected_total,
                correlation_id=correlation_id,
            )
    except CandidateNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except CandidateConflictError as exc:
        return _conflict(request, correlation_id, exc)
    except TableGridLimitError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(
            {
                "content_candidate": None if result.content is None else _body(result.content),
                "question_candidates": [_body(q) for q in result.questions],
                "unassigned_exercise_text": list(result.unassigned_exercise_text),
                "warnings": list(result.warnings),
                "rubric_points_total": None if result.rubric_points_total is None else str(result.rubric_points_total),
            }
        ),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.get("/candidates/{candidate_id}")
async def get_candidate_endpoint(
    candidate_id: uuid.UUID, request: Request, credentials: OptionalBearer = None
) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=False)
    if err is not None:
        return err
    assert actor is not None
    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        record = await get_candidate(session, candidate_id)
    if record is None:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, "candidate not found")
    return JSONResponse(content=jsonable_encoder(_body(record)), headers={"X-Correlation-Id": correlation_id})


@router.post("/candidates/{candidate_id}/split")
async def split_endpoint(candidate_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = SplitRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            parent, children = await split_candidate(
                session, candidate_id, parts=payload.parts, correlation_id=correlation_id
            )
    except CandidateNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except CandidateConflictError as exc:
        return _conflict(request, correlation_id, exc)
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(
        content=jsonable_encoder({"superseded": _body(parent), "children": [_body(c) for c in children]}),
        headers={"X-Correlation-Id": correlation_id},
    )


@router.post("/candidates/{candidate_id}/merge")
async def merge_endpoint(candidate_id: uuid.UUID, request: Request, credentials: OptionalBearer = None) -> JSONResponse:
    actor, correlation_id, err = _authenticate(request, credentials, write=True)
    if err is not None:
        return err
    assert actor is not None
    try:
        payload = MergeRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            merged = await merge_candidates(session, candidate_id, merge_ids=payload.merge_candidate_ids)
    except CandidateNotFoundError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.NOT_FOUND, str(exc))
    except CandidateConflictError as exc:
        return _conflict(request, correlation_id, exc)
    except ValueError as exc:
        return _problem(request, correlation_id, PlatformErrorCode.VALIDATION_ERROR, str(exc))
    return JSONResponse(content=jsonable_encoder(_body(merged)), headers={"X-Correlation-Id": correlation_id})
