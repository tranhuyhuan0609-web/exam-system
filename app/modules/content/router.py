"""FastAPI /api/v1 routes for B01 content: create an item, fork a new version, publish a version.

The tenant comes only from the signed JWT. Publishing validates the version with the B02 Block Studio
(KaTeX, forbidden commands, embedded assets the tenant may use) and then freezes it; the publish gate
(VERIFIED, cleared rights) is checked by the service and by the database.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.authoring.public import collect_asset_references, load_asset_catalog, validate_content_version
from app.modules.content.internal.service import (
    ContentConflictError,
    ContentItemRecord,
    ContentNotFoundError,
    ContentValidationFailedError,
    ContentVersionRecord,
    PublishGateBlockedError,
    blocks_from_json,
    create_content_item,
    create_next_version,
    get_content_version,
    publish_version,
)
from app.modules.content.internal.versioning import ContentBlockDraft, ContentBlockType, ContentVersionState
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
    decode_and_verify_actor_token,
    extract_bearer_token,
    guard_no_tenant_spoofing,
)
from app.platform.errors import PlatformErrorCode, build_problem_detail
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["content"])

EDITOR_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "CONTENT_AUTHOR"})
SLUG_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
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


class CreateContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: Literal["ARTICLE", "LESSON", "VIDEO", "DOCUMENT", "FLASHCARD_SET", "VOCABULARY_SET"]
    slug: str = Field(min_length=1, max_length=255, pattern=SLUG_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    summary_md: str | None = Field(default=None, max_length=100_000)
    blocks: list[BlockIn] = Field(default_factory=list, max_length=500)
    curriculum_node_id: uuid.UUID | None = None
    primary_taxonomy_node_id: uuid.UUID | None = None
    visibility_scope: Literal["SYSTEM_PUBLIC", "TENANT_PRIVATE", "SHARED_PARTNER"] = "TENANT_PRIVATE"
    access_tier: Literal["FREE", "STUDENT_PRO", "TEACHER_PRO"] = "FREE"
    # What the author may claim; LICENSED and the rest come from a rights review (A09).
    rights_status: Literal["PENDING_REVIEW", "OWNED"] = "PENDING_REVIEW"


class CreateVersionRequest(BaseModel):
    """Fields left out keep the value of the version being forked."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    summary_md: str | None = Field(default=None, max_length=100_000)
    blocks: list[BlockIn] | None = Field(default=None, max_length=500)


class ContentItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    visibility_scope: str
    content_type: str
    slug: str
    curriculum_node_id: uuid.UUID | None
    primary_taxonomy_node_id: uuid.UUID | None
    current_version_id: uuid.UUID | None
    acquisition_status: str
    verification_status: str
    rights_status: str
    publication_status: str
    access_tier: str
    created_at: datetime


class ContentVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    content_item_id: uuid.UUID
    version_no: int
    title: str
    summary_md: str | None
    blocks: list[dict[str, Any]]
    status: str
    is_immutable: bool
    created_by: uuid.UUID | None
    created_at: datetime


class CreateContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: ContentItemResponse
    version: ContentVersionResponse


class PublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: ContentItemResponse
    version: ContentVersionResponse
    publication_id: uuid.UUID
    event_id: uuid.UUID
    linked_asset_ids: list[uuid.UUID]


def _item_response(item: ContentItemRecord) -> ContentItemResponse:
    return ContentItemResponse(**{f: getattr(item, f) for f in ContentItemResponse.model_fields})


def _version_response(version: ContentVersionRecord) -> ContentVersionResponse:
    values = {f: getattr(version, f) for f in ContentVersionResponse.model_fields if f != "blocks"}
    return ContentVersionResponse(blocks=list(version.blocks), **values)


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


def _drafts(blocks: list[BlockIn] | None) -> tuple[ContentBlockDraft, ...] | None:
    if blocks is None:
        return None
    return tuple(ContentBlockDraft(b.block_order, ContentBlockType(b.block_type), b.payload_json) for b in blocks)


@router.post("/content", response_model=CreateContentResponse, status_code=status.HTTP_201_CREATED)
async def post_content(body: CreateContentRequest, request: Request, credentials: OptionalBearer = None) -> Any:
    """Create a content item with its first version (DRAFT)."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    if body.visibility_scope == "SYSTEM_PUBLIC" and actor.tenant_id != SYSTEM_PUBLIC_TENANT_ID:
        return _problem(
            PlatformErrorCode.VALIDATION_ERROR,
            "SYSTEM_PUBLIC content can only be created in the system tenant",
            request,
            correlation_id,
        )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            item, version = await create_content_item(
                session,
                tenant_id=actor.tenant_id,
                created_by=actor.user_id,
                content_type=body.content_type,
                slug=body.slug,
                title=body.title,
                summary_md=body.summary_md,
                blocks=_drafts(body.blocks) or (),
                curriculum_node_id=body.curriculum_node_id,
                primary_taxonomy_node_id=body.primary_taxonomy_node_id,
                visibility_scope=body.visibility_scope,
                access_tier=body.access_tier,
                rights_status=body.rights_status,
            )
    except ContentNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except ContentConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    return CreateContentResponse(item=_item_response(item), version=_version_response(version))


@router.post(
    "/content/{content_id}/versions", response_model=ContentVersionResponse, status_code=status.HTTP_201_CREATED
)
async def post_content_version(
    content_id: uuid.UUID, body: CreateVersionRequest, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Start the next version (`version_no + 1`, DRAFT) of a published item: published versions never change."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            version = await create_next_version(
                session,
                content_id,
                created_by=actor.user_id,
                title=body.title,
                summary_md=body.summary_md,
                blocks=_drafts(body.blocks),
            )
    except ContentNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except ContentConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    return _version_response(version)


@router.post("/content-versions/{version_id}/publish", response_model=PublishResponse)
async def post_publish_content_version(
    version_id: uuid.UUID, request: Request, credentials: OptionalBearer = None
) -> Any:
    """Validate (B02) and publish a DRAFT version; the version becomes immutable."""
    actor, correlation_id, err = _authenticate(request, credentials)
    if err is not None:
        return err
    assert actor is not None
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            existing = await get_content_version(session, version_id)
            if existing is None:
                return _problem(PlatformErrorCode.NOT_FOUND, "content version not found", request, correlation_id)
            refs = collect_asset_references(blocks_from_json(existing.blocks))
            catalog = await load_asset_catalog(session, refs)

            def validator(state: ContentVersionState) -> Any:
                return validate_content_version(state, asset_catalog=catalog)

            own = [uuid.UUID(k) for k, entry in catalog.items() if entry.tenant_id == str(actor.tenant_id)]
            result = await publish_version(
                session,
                version_id,
                published_by=actor.user_id,
                validator=validator,
                own_asset_ids=own,
                correlation_id=correlation_id,
            )
    except ContentNotFoundError as exc:
        return _problem(PlatformErrorCode.NOT_FOUND, str(exc), request, correlation_id)
    except ContentConflictError as exc:
        return _problem(PlatformErrorCode.VALIDATION_ERROR, str(exc), request, correlation_id, status_override=409)
    except ContentValidationFailedError as exc:
        return _problem(PlatformErrorCode.PUBLISH_GATE_BLOCKED, str(exc), request, correlation_id)
    except PublishGateBlockedError as exc:
        return _problem(PlatformErrorCode.PUBLISH_GATE_BLOCKED, str(exc), request, correlation_id)
    return PublishResponse(
        item=_item_response(result.item),
        version=_version_response(result.version),
        publication_id=result.publication_id,
        event_id=result.event_id,
        linked_asset_ids=list(result.linked_asset_ids),
    )
