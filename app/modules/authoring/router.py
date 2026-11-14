"""FastAPI /api/v1 routes for B02 authoring: working drafts and Block Studio validation.

The tenant comes only from the signed JWT. A draft belongs to its author; an administrator of the tenant
may also change it. Another tenant's item or draft answers 404.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.authoring.internal.service import (
    DraftForbiddenError,
    DraftNotFoundError,
    DraftRecord,
    create_draft,
    update_draft,
    validate_draft,
)
from app.modules.content.public import (
    ContentBlockDraft,
    ContentBlockType,
    ContentConflictError,
    ContentNotFoundError,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["authoring"])

EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
ADMIN_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})
_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_order: int = Field(ge=0, le=100_000)
    block_type: Literal[
        "THEORY_SUMMARY", "FORMULA_BOX", "METHOD_STEPS", "TIP_WARNING",
        "WORKED_EXAMPLE", "INTERACTIVE_QUESTION_EMBED", "HTML_TABLE",
    ]  # fmt: skip
    payload_json: dict[str, Any] = Field(default_factory=dict)


class CreateDraftRequest(BaseModel):
    """Without `blocks` the draft starts as a copy of the newest version's blocks."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[BlockIn] | None = Field(default=None, max_length=500)


class PatchDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[BlockIn] = Field(max_length=500)


class ValidateDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apply_to_version: bool = False


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    content_item_id: uuid.UUID
    author_id: uuid.UUID
    working_blocks: list[dict[str, Any]]
    validation_errors: list[dict[str, Any]]
    updated_at: datetime


class ValidateDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: DraftResponse
    valid: bool
    issues: list[dict[str, Any]]
    applied_version_id: uuid.UUID | None


def _draft_response(draft: DraftRecord) -> DraftResponse:
    return DraftResponse(
        id=draft.id,
        tenant_id=draft.tenant_id,
        content_item_id=draft.content_item_id,
        author_id=draft.author_id,
        working_blocks=list(draft.working_blocks),
        validation_errors=list(draft.validation_errors),
        updated_at=draft.updated_at,
    )


def _blocks(blocks: list[BlockIn]) -> tuple[ContentBlockDraft, ...]:
    return tuple(ContentBlockDraft(b.block_order, ContentBlockType(b.block_type), b.payload_json) for b in blocks)


def _problem(
    code: PlatformErrorCode, detail: str, request: Request, correlation_id: str, status_override: int | None = None
) -> JSONResponse:
    built = build_problem_detail(
        code=code, detail=detail, instance=request.url.path, correlation_id=correlation_id,
        status_override=status_override,
    )  # fmt: skip
    return JSONResponse(
        status_code=built.status,
        content=built.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-Id": correlation_id},
    )


def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> tuple[ActorContext | None, str, JSONResponse | None]:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    try:
        actor = decode_and_verify_actor_token(
            extract_bearer_token(raw_auth), secret=get_settings().secret_key, now_epoch=int(time.time())
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
    except AuthTokenError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.UNAUTHORIZED, str(exc), request, correlation_id)
    except TenantSpoofingError as exc:
        return None, correlation_id, _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    if not ({r.upper() for r in actor.roles} & EDITOR_ROLES):
        return (
            None,
            correlation_id,
            _problem(
                PlatformErrorCode.FORBIDDEN, "an author or administrator role is required", request, correlation_id
            ),
        )
    return actor, correlation_id, None


def _is_admin(actor: ActorContext) -> bool:
    return bool({r.upper() for r in actor.roles} & ADMIN_ROLES)


@router.post("/content/{content_id}/drafts", response_model=DraftResponse, status_code=status.HTTP_201_CREATED)
async def post_content_draft(
    content_id: uuid.UUID,
    body: CreateDraftRequest,
    request: Request,
    response: Response,
    credentials: OptionalBearer = None,
) -> Any:
    """Open the caller's working draft of an item (201), or return the one they already have (200)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            draft, created = await create_draft(
                session,
                tenant_id=actor.tenant_id,
                content_item_id=content_id,
                author_id=actor.user_id,
                blocks=None if body.blocks is None else _blocks(body.blocks),
            )
    except (DraftNotFoundError, ContentNotFoundError) as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return _draft_response(draft)


@router.patch("/drafts/{draft_id}", response_model=DraftResponse)
async def patch_draft(
    draft_id: uuid.UUID, body: PatchDraftRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Replace the working blocks of a draft (the recorded validation errors are cleared)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            draft = await update_draft(
                session,
                draft_id,
                actor_user_id=actor.user_id,
                actor_is_admin=_is_admin(actor),
                blocks=_blocks(body.blocks),
            )
    except DraftNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except DraftForbiddenError as exc:
        return _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    return _draft_response(draft)


@router.post("/drafts/{draft_id}/validate", response_model=ValidateDraftResponse)
async def post_validate_draft(
    draft_id: uuid.UUID, body: ValidateDraftRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Validate the working blocks (KaTeX, forbidden commands, assets); optionally apply a valid draft."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            outcome = await validate_draft(
                session,
                draft_id,
                actor_user_id=actor.user_id,
                actor_is_admin=_is_admin(actor),
                apply_to_version=body.apply_to_version,
            )
    except (DraftNotFoundError, ContentNotFoundError) as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except DraftForbiddenError as exc:
        return _problem(PlatformErrorCode.FORBIDDEN, str(exc), request, correlation_id)
    except ContentConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    return ValidateDraftResponse(
        draft=_draft_response(outcome.draft),
        valid=outcome.valid,
        issues=list(outcome.issues),
        applied_version_id=outcome.applied_version_id,
    )
