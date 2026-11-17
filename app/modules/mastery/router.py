"""FastAPI /api/v1 routes for D06 Mastery & Adaptive IRT."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.mastery.internal.irt_tier import IrtCalibrationMetrics
from app.modules.mastery.internal.service import (
    MasteryEstimateView,
    MasteryEvidenceItem,
    MasteryNotFoundError,
    MasteryScopeError,
    MasteryValidationError,
    QuestionCalibrationView,
    calibrate_question_irt,
    list_learner_mastery,
    recompute_learner_mastery,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.roles import is_platform_admin
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["mastery"])

_STAFF_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN", "TEACHER", "REVIEWER"})


class ObservationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_correct: bool
    question_version_id: uuid.UUID | None = None
    weight: float = Field(default=1.0, gt=0.0)


class RecomputeMasteryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxonomy_node_id: uuid.UUID
    membership_id: uuid.UUID | None = None
    observations: list[ObservationPayload] | None = None
    override_se_theta: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("9.999"))


class CalibrateIrtRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID
    sample_size: int = Field(ge=0)
    se_b: float = Field(ge=0.0)
    learner_count: int = Field(default=1000, ge=0)
    ability_sd: float = Field(default=0.80, ge=0.0)
    converged: bool = True
    infit_msq: float = Field(default=1.00, ge=0.0)
    outfit_msq: Decimal = Field(default=Decimal("1.000"), ge=Decimal("0"))
    max_abs_dif: float = Field(default=0.10, ge=0.0)
    difficulty_b: Decimal = Decimal("0.0000")
    discrimination_a: Decimal = Field(default=Decimal("1.0000"), gt=Decimal("0"))
    guessing_c: Decimal = Field(default=Decimal("0.0000"), ge=Decimal("0"), le=Decimal("0.5000"))


class MasteryEstimateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    model_code: str
    status: str
    evidence_count: int
    standard_error: Decimal
    theta_estimate: Decimal | None
    mastery_probability: Decimal | None
    updated_at: datetime


class CalibrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_version_id: uuid.UUID
    persisted: bool
    selected_tier: str
    next_tier: str | None
    failed_next_tier_criteria: list[str]
    calibration_run_id: uuid.UUID | None
    difficulty_b: Decimal | None
    discrimination_a: Decimal | None
    guessing_c: Decimal | None
    se_difficulty: Decimal | None
    calibrated_at: datetime | None


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
    return actor


def _serialize_estimate(view: MasteryEstimateView) -> MasteryEstimateResponse:
    return MasteryEstimateResponse(
        id=view.id,
        tenant_id=view.tenant_id,
        membership_id=view.membership_id,
        taxonomy_node_id=view.taxonomy_node_id,
        model_code=view.model_code,
        status=view.status,
        evidence_count=view.evidence_count,
        standard_error=view.standard_error,
        theta_estimate=view.theta_estimate,
        mastery_probability=view.mastery_probability,
        updated_at=view.updated_at,
    )


def _serialize_calibration(view: QuestionCalibrationView) -> CalibrationResponse:
    return CalibrationResponse(
        question_version_id=view.question_version_id,
        persisted=view.persisted,
        selected_tier=view.selected_tier,
        next_tier=view.next_tier,
        failed_next_tier_criteria=list(view.failed_next_tier_criteria),
        calibration_run_id=view.calibration_run_id,
        difficulty_b=view.difficulty_b,
        discrimination_a=view.discrimination_a,
        guessing_c=view.guessing_c,
        se_difficulty=view.se_difficulty,
        calibrated_at=view.calibrated_at,
    )


@router.get("/learners/me/mastery")
async def get_my_mastery_endpoint(
    request: Request,
    taxonomy_node_id: uuid.UUID | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        rows = await list_learner_mastery(
            session,
            tenant_id=actor.tenant_id,
            membership_id=actor.membership_id,
            taxonomy_node_id=taxonomy_node_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"items": jsonable_encoder([_serialize_estimate(r) for r in rows])},
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/mastery/recompute")
async def recompute_mastery_endpoint(
    payload: RecomputeMasteryRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    target_member = payload.membership_id or actor.membership_id
    if target_member != actor.membership_id and not any(r.upper() in _STAFF_ROLES for r in actor.roles):
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Learners may recompute mastery only for their own membership",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    obs = (
        [
            MasteryEvidenceItem(
                is_correct=o.is_correct,
                question_version_id=o.question_version_id,
                weight=o.weight,
            )
            for o in payload.observations
        ]
        if payload.observations is not None
        else None
    )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await recompute_learner_mastery(
                session,
                tenant_id=actor.tenant_id,
                membership_id=target_member,
                taxonomy_node_id=payload.taxonomy_node_id,
                observations=obs,
                override_se_theta=payload.override_se_theta,
            )
    except MasteryNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except MasteryValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_estimate(view)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/mastery/calibration")
async def calibrate_irt_endpoint(
    payload: CalibrateIrtRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor
    if not is_platform_admin(actor.roles):  # the calibration is shared by every tenant using the question version
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Only a platform administrator may calibrate IRT parameters",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    metrics = IrtCalibrationMetrics(
        sample_size=payload.sample_size,
        se_b=payload.se_b,
        learner_count=payload.learner_count,
        ability_sd=payload.ability_sd,
        converged=payload.converged,
        infit_msq=payload.infit_msq,
        max_abs_dif=payload.max_abs_dif,
    )
    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            view = await calibrate_question_irt(
                session,
                tenant_id=actor.tenant_id,
                question_version_id=payload.question_version_id,
                metrics=metrics,
                difficulty_b=payload.difficulty_b,
                discrimination_a=payload.discrimination_a,
                guessing_c=payload.guessing_c,
                outfit_msq=payload.outfit_msq,
                approved_by=actor.user_id,
            )
    except MasteryNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except MasteryScopeError as exc:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail=str(exc),
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )
    except MasteryValidationError as exc:
        return _problem_response(
            code=PlatformErrorCode.VALIDATION_ERROR,
            detail=str(exc),
            status_code=422,
            instance=str(request.url.path),
            correlation_id=correlation_id,
            extensions={"validation_code": exc.code},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_calibration(view)),
        headers={"X-Correlation-ID": correlation_id},
    )
