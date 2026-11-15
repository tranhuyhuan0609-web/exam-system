"""FastAPI /api/v1 routes for G05 Billing, HMAC Payment Webhooks & Usage Quota Consumption."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.modules.billing.internal.ledger import BillingError
from app.modules.billing.internal.service import (
    BillingNotFoundError,
    QuotaConsumptionView,
    SubscriptionPlanView,
    SubscriptionView,
    WebhookOutcomeView,
    consume_member_quota,
    list_subscription_plans,
    list_subscriptions,
    process_verified_payment_webhook,
    verify_and_extract_webhook_grant,
)
from app.platform.auth_context import (
    ActorContext,
    AuthTokenError,
    TenantSpoofingError,
)
from app.platform.errors import PlatformErrorCode
from app.platform.http_auth import OptionalBearer, problem_json, verify_actor
from app.platform.pagination import resolve_correlation_id
from app.platform.tenancy import DbRole, tenant_transaction

router = APIRouter(prefix="/api/v1", tags=["billing"])

_ADMIN_ROLES = frozenset({"ADMIN", "PLATFORM_ADMIN", "SUPER_ADMIN", "TENANT_ADMIN"})


class ConsumeUsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_code: str = Field(min_length=1, max_length=60)
    delta: int = Field(default=1, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SubscriptionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    price_vnd: int
    quotas: dict[str, int]


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    plan_code: str
    status: str
    current_period_end: datetime
    created_at: datetime


class WebhookOutcomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_duplicate: bool
    webhook_id: str
    provider: str
    emitted_outbox_event: bool
    subscription: SubscriptionResponse


class QuotaConsumptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    was_charged_now: bool
    resource_code: str
    delta: int
    used_total: int
    remaining_quota: int
    plan_code: str


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


def _serialize_plan(plan: SubscriptionPlanView) -> SubscriptionPlanResponse:
    return SubscriptionPlanResponse(
        code=plan.code,
        name=plan.name,
        price_vnd=plan.price_vnd,
        quotas=plan.quotas,
    )


def _serialize_sub(sub: SubscriptionView) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=sub.id,
        tenant_id=sub.tenant_id,
        membership_id=sub.membership_id,
        plan_code=sub.plan_code,
        status=sub.status,
        current_period_end=sub.current_period_end,
        created_at=sub.created_at,
    )


def _serialize_webhook_outcome(outcome: WebhookOutcomeView) -> WebhookOutcomeResponse:
    return WebhookOutcomeResponse(
        is_duplicate=outcome.is_duplicate,
        webhook_id=outcome.webhook_id,
        provider=outcome.provider,
        emitted_outbox_event=outcome.emitted_outbox_event,
        subscription=_serialize_sub(outcome.subscription),
    )


def _serialize_quota(view: QuotaConsumptionView) -> QuotaConsumptionResponse:
    return QuotaConsumptionResponse(
        was_charged_now=view.was_charged_now,
        resource_code=view.resource_code,
        delta=view.delta,
        used_total=view.used_total,
        remaining_quota=view.remaining_quota,
        plan_code=view.plan_code,
    )


def _webhook_error_to_problem(exc: BillingError, *, instance: str, correlation_id: str) -> JSONResponse:
    if exc.code == "INVALID_WEBHOOK_SIGNATURE":
        return _problem_response(
            code=PlatformErrorCode.UNAUTHORIZED,
            detail=str(exc),
            status_code=401,
            instance=instance,
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )
    if exc.code == "QUOTA_EXCEEDED":
        return _problem_response(
            code=PlatformErrorCode.RATE_LIMIT_EXCEEDED,
            detail=str(exc),
            status_code=429,
            instance=instance,
            correlation_id=correlation_id,
            extensions={"error_code": exc.code},
        )
    return _problem_response(
        code=PlatformErrorCode.VALIDATION_ERROR,
        detail=str(exc),
        status_code=422,
        instance=instance,
        correlation_id=correlation_id,
        extensions={"error_code": exc.code},
    )


def _resolve_webhook_secret() -> str | None:
    """The webhook HMAC secret; outside dev/test only a dedicated secret counts (never `secret_key`)."""
    settings = get_settings()
    dedicated = getattr(settings, "payment_webhook_secret", None) or os.environ.get("PAYMENT_WEBHOOK_SECRET")
    if dedicated:
        return dedicated
    if settings.app_env.lower() in ("dev", "development", "test", "testing"):
        return settings.secret_key
    return None


def _parse_webhook_timestamp(raw_ts: str | None) -> datetime:
    if not raw_ts or not raw_ts.strip():
        raise BillingError("MISSING_WEBHOOK_TIMESTAMP", "X-Webhook-Timestamp header is required")
    clean = raw_ts.strip()
    try:
        epoch = int(clean)
    except ValueError:
        epoch = None
    if epoch is not None:
        return datetime.fromtimestamp(epoch, tz=UTC)
    try:
        dt = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BillingError(
            "INVALID_WEBHOOK_TIMESTAMP",
            "X-Webhook-Timestamp must be unix epoch or ISO-8601",
        ) from exc
    if dt.tzinfo is None:
        raise BillingError("WEBHOOK_TIMESTAMP_NOT_AWARE", "Webhook timestamp must include timezone offset")
    return dt


@router.get("/plans")
async def list_plans_endpoint(
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        plans = await list_subscription_plans(session)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"items": [jsonable_encoder(_serialize_plan(p)) for p in plans]},
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/subscriptions")
async def list_subscriptions_endpoint(
    request: Request,
    membership_id: uuid.UUID | None = None,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    is_admin = any(r.upper() in _ADMIN_ROLES for r in actor.roles)
    target_member = membership_id if (membership_id is not None and is_admin) else actor.membership_id
    if membership_id is not None and membership_id != actor.membership_id and not is_admin:
        return _problem_response(
            code=PlatformErrorCode.FORBIDDEN,
            detail="Learners may only view their own subscription",
            status_code=403,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
        subs = await list_subscriptions(
            session,
            tenant_id=actor.tenant_id,
            membership_id=target_member if not is_admin or membership_id is not None else None,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"items": [jsonable_encoder(_serialize_sub(s)) for s in subs]},
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/usage/consume")
async def consume_usage_endpoint(
    payload: ConsumeUsageRequest,
    request: Request,
    credentials: OptionalBearer = None,
) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    actor = _authenticate(request, credentials, correlation_id)
    if isinstance(actor, JSONResponse):
        return actor

    try:
        async with tenant_transaction(actor.tenant_id, role=DbRole.APP_RW) as session:
            result = await consume_member_quota(
                session,
                tenant_id=actor.tenant_id,
                membership_id=actor.membership_id,
                resource_code=payload.resource_code,
                delta=payload.delta,
                idempotency_key=payload.idempotency_key,
            )
    except BillingError as exc:
        return _webhook_error_to_problem(exc, instance=str(request.url.path), correlation_id=correlation_id)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_quota(result)),
        headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/webhooks/payment")
async def payment_webhook_endpoint(request: Request) -> JSONResponse:
    correlation_id = resolve_correlation_id(request.headers.get("x-correlation-id"))
    raw_payload = await request.body()
    webhook_id = request.headers.get("x-webhook-id", "")
    provider = request.headers.get("x-webhook-provider", "")
    signature_hex = request.headers.get("x-webhook-signature", "")
    secret = _resolve_webhook_secret()
    if secret is None:
        return _problem_response(
            code=PlatformErrorCode.INTERNAL_ERROR,
            detail="PAYMENT_WEBHOOK_SECRET is not configured",
            status_code=503,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    try:
        event_ts = _parse_webhook_timestamp(request.headers.get("x-webhook-timestamp"))
        tenant_uuid, _, _ = verify_and_extract_webhook_grant(
            webhook_id=webhook_id,
            provider=provider,
            event_timestamp=event_ts,
            raw_payload=raw_payload,
            signature_hex=signature_hex,
            secret=secret,
        )
        async with tenant_transaction(tenant_uuid, role=DbRole.APP_RW) as session:
            outcome = await process_verified_payment_webhook(
                session,
                webhook_id=webhook_id,
                provider=provider,
                event_timestamp=event_ts,
                raw_payload=raw_payload,
                signature_hex=signature_hex,
                secret=secret,
                correlation_id=correlation_id,
            )
    except BillingError as exc:
        return _webhook_error_to_problem(exc, instance=str(request.url.path), correlation_id=correlation_id)
    except BillingNotFoundError as exc:
        return _problem_response(
            code=PlatformErrorCode.NOT_FOUND,
            detail=str(exc),
            status_code=404,
            instance=str(request.url.path),
            correlation_id=correlation_id,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_serialize_webhook_outcome(outcome)),
        headers={"X-Correlation-ID": correlation_id},
    )
