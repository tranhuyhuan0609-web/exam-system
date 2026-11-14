"""FastAPI /api/v1 routes for D04 Flashcards & Spaced Repetition (FSRS v5)."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Request, Security, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.flashcards.internal.service import (
    FlashcardDeckView,
    FlashcardNotFoundError,
    FlashcardReviewView,
    FlashcardValidationError,
    NewFlashcardInput,
    RevisionScheduleItemView,
    create_flashcard_deck,
    get_revision_schedule,
    review_flashcard,
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

router = APIRouter(prefix="/api/v1", tags=["flashcards"])

_bearer_scheme = HTTPBearer(auto_error=False)
OptionalBearer = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_scheme)]


class FlashcardCreateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front_md: str = Field(min_length=1, max_length=10_000)
    back_md: str = Field(min_length=1, max_length=10_000)
    source_question_version_id: uuid.UUID | None = None


class CreateDeckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    cards: list[FlashcardCreateItem] = Field(default_factory=list, max_length=100)


class ReviewFlashcardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=4)
    reviewed_at: datetime | None = None


class FlashcardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    deck_id: uuid.UUID
    front_md: str
    back_md: str
    source_question_version_id: uuid.UUID | None
    created_at: datetime


class FlashcardDeckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    title: str
    created_at: datetime
    cards: list[FlashcardResponse]


class FlashcardReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: uuid.UUID
    flashcard_id: uuid.UUID
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    rating: int
    scheduler_version: str
    stability: Decimal
    difficulty: Decimal
    retrievability: Decimal
    reps: int
    lapses: int
    interval_days: int
    last_review_at: datetime
    due_at: datetime
    next_review_date: date


class RevisionScheduleItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: uuid.UUID
    flashcard_id: uuid.UUID
    deck_id: uuid.UUID
    deck_title: str
    front_md: str
    back_md: str
    scheduler_version: str
    stability: Decimal
    difficulty: Decimal
    retrievability: Decimal
    reps: int
    lapses: int
    last_review_at: datetime | None
    due_at: datetime
    next_review_date: date
    is_due: bool


class RevisionScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str
    items: list[RevisionScheduleItemResponse]


def _problem_response(
    *,
    code: PlatformErrorCode,
    detail: str,
    status_code: int,
    instance: str,
    correlation_id: str,
) -> JSONResponse:
    problem = build_problem_detail(
        code=code,
        detail=detail,
        status_override=status_code,
        instance=instance,
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=problem.status,
        content=problem.to_dict(),
        media_type="application/problem+json",
        headers={"X-Correlation-ID": correlation_id},
    )


def _authenticate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    correlation_id: str,
) -> ActorContext | JSONResponse:
    settings = get_settings()
    raw_auth = request.headers.get("authorization")
    if not raw_auth and credentials is not None:
        raw_auth = f"{credentials.scheme} {credentials.credentials}"
    if not raw_auth and request.cookies.get("ulp_access_token"):
        raw_auth = f"Bearer {request.cookies['ulp_access_token']}"
    try:
        token = extract_bearer_token(raw_auth)
        actor = decode_and_verify_actor_token(
            token,
            secret=settings.secret_key,
            now_epoch=int(time.time()),
        )
        guard_no_tenant_spoofing(
            actor,
            claimed_tenant_header=request.headers.get("x-tenant-id"),
            claimed_membership_header=request.headers.get("x-membership-id"),
        )
        return actor
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


def _serialize_deck(deck: FlashcardDeckView) -> FlashcardDeckResponse:
    return FlashcardDeckResponse(
        id=deck.id,
        tenant_id=deck.tenant_id,
        membership_id=deck.membership_id,
        title=deck.title,
        created_at=deck.created_at,
        cards=[
            FlashcardResponse(
                id=c.id,
                tenant_id=c.tenant_id,
                deck_id=c.deck_id,
                front_md=c.front_md,
                back_md=c.back_md,
                source_question_version_id=c.source_question_version_id,
                created_at=c.created_at,
            )
            for c in deck.cards
        ],
    )


def _serialize_review(res: FlashcardReviewView) -> FlashcardReviewResponse:
    return FlashcardReviewResponse(
        schedule_id=res.schedule_id,
        flashcard_id=res.flashcard_id,
        membership_id=res.membership_id,
        tenant_id=res.tenant_id,
        rating=res.rating,
        scheduler_version=res.scheduler_version,
        stability=res.stability,
        difficulty=res.difficulty,
        retrievability=res.retrievability,
        reps=res.reps,
        lapses=res.lapses,
        interval_days=res.interval_days,
        last_review_at=res.last_review_at,
        due_at=res.due_at,
        next_review_date=res.next_review_date,
    )


def _serialize_schedule_item(item: RevisionScheduleItemView) -> RevisionScheduleItemResponse:
    return RevisionScheduleItemResponse(
        schedule_id=item.schedule_id,
        flashcard_id=item.flashcard_id,
        deck_id=item.deck_id,
        deck_title=item.deck_title,
        front_md=item.front_md,
        back_md=item.back_md,
        scheduler_version=item.scheduler_version,
        stability=item.stability,
        difficulty=item.difficulty,
        retrievability=item.retrievability,
        reps=item.reps,
        lapses=item.lapses,
        last_review_at=item.last_review_at,
        due_at=item.due_at,
        next_review_date=item.next_review_date,
        is_due=item.is_due,
    )


@router.post("/flashcard-decks", response_model=FlashcardDeckResponse, status_code=status.HTTP_201_CREATED)
async def create_flashcard_deck_endpoint(
    payload: CreateDeckRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            deck = await create_flashcard_deck(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                title=payload.title,
                cards=[
                    NewFlashcardInput(
                        front_md=c.front_md,
                        back_md=c.back_md,
                        source_question_version_id=c.source_question_version_id,
                    )
                    for c in payload.cards
                ],
            )
    except FlashcardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(_serialize_deck(deck)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/flashcards/{flashcard_id}/review", response_model=FlashcardReviewResponse)
async def review_flashcard_endpoint(
    flashcard_id: uuid.UUID,
    payload: ReviewFlashcardRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            reviewed = await review_flashcard(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                flashcard_id=flashcard_id,
                rating=payload.rating,
                reviewed_at=payload.reviewed_at,
            )
    except FlashcardNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except FlashcardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_review(reviewed)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/revision-schedule", response_model=RevisionScheduleResponse)
async def get_revision_schedule_endpoint(
    request: Request,
    due_only: Annotated[bool, Query()] = True,
    as_of: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("X-Correlation-ID"))
    auth = _authenticate(request, credentials, correlation_id)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        async with tenant_transaction(auth.tenant_id, role=DbRole.APP_RW) as session:
            items = await get_revision_schedule(
                session,
                tenant_id=auth.tenant_id,
                membership_id=auth.membership_id,
                due_only=due_only,
                as_of=as_of,
                limit=limit,
            )
    except FlashcardValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(
            RevisionScheduleResponse(
                timezone="Asia/Ho_Chi_Minh",
                items=[_serialize_schedule_item(i) for i in items],
            )
        ),
        headers={"X-Correlation-ID": correlation_id},
    )
