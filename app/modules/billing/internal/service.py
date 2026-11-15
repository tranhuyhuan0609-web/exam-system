"""Database service layer for G05 Billing, HMAC Webhooks & Idempotent Usage Quota Ledger."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.event_catalog import SubscriptionUpdatedPayload, build_catalog_envelope
from app.modules.billing.internal.ledger import (
    BillingError,
    PaymentWebhookCommand,
    QuotaLedgerState,
    SubscriptionPlanSpec,
    SubscriptionState,
    SubscriptionStatus,
    UsageLedgerEntry,
    consume_quota_idempotent,
    parse_signed_grant,
    process_payment_webhook,
    verify_webhook_signature,
)
from app.modules.billing.internal.models import (
    PaymentWebhookRecord,
    SubscriptionPlanRecord,
    SubscriptionRecord,
    UsageLedgerRecord,
)
from app.modules.outbox.public import enqueue as enqueue_outbox


class BillingNotFoundError(LookupError):
    """Raised when a subscription plan or membership does not exist in tenant scope."""


@dataclass(frozen=True, slots=True)
class SubscriptionPlanView:
    code: str
    name: str
    price_vnd: int
    quotas: dict[str, int]


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    plan_code: str
    status: str
    current_period_end: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WebhookOutcomeView:
    is_duplicate: bool
    webhook_id: str
    provider: str
    emitted_outbox_event: bool
    subscription: SubscriptionView


@dataclass(frozen=True, slots=True)
class QuotaConsumptionView:
    was_charged_now: bool
    resource_code: str
    delta: int
    used_total: int
    remaining_quota: int
    plan_code: str


async def load_plan_specs(session: AsyncSession) -> dict[str, SubscriptionPlanSpec]:
    """Load all rows from `subscription_plans` into immutable `SubscriptionPlanSpec` map."""
    rows = (await session.execute(select(SubscriptionPlanRecord))).scalars()
    specs: dict[str, SubscriptionPlanSpec] = {}
    for row in rows:
        raw_quotas = {str(k): int(v) for k, v in (row.quotas_json or {}).items()}
        specs[row.code] = SubscriptionPlanSpec(
            code=row.code,
            name=row.name,
            price_vnd=int(row.price_vnd),
            quotas=MappingProxyType(raw_quotas),
        )
    return specs


async def list_subscription_plans(session: AsyncSession) -> tuple[SubscriptionPlanView, ...]:
    """Return available subscription plans ordered by price."""
    rows = (
        await session.execute(
            select(SubscriptionPlanRecord).order_by(
                SubscriptionPlanRecord.price_vnd.asc(),
                SubscriptionPlanRecord.code.asc(),
            )
        )
    ).scalars()
    return tuple(
        SubscriptionPlanView(
            code=r.code,
            name=r.name,
            price_vnd=int(r.price_vnd),
            quotas={str(k): int(v) for k, v in (r.quotas_json or {}).items()},
        )
        for r in rows
    )


def _to_sub_view(row: SubscriptionRecord) -> SubscriptionView:
    return SubscriptionView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        plan_code=row.plan_code,
        status=row.status,
        current_period_end=row.current_period_end,
        created_at=row.created_at,
    )


async def list_subscriptions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
) -> tuple[SubscriptionView, ...]:
    """List subscriptions inside current tenant RLS, optionally narrowed to one membership."""
    stmt = (
        select(SubscriptionRecord)
        .where(SubscriptionRecord.tenant_id == tenant_id)
        .order_by(SubscriptionRecord.current_period_end.desc(), SubscriptionRecord.created_at.desc())
    )
    if membership_id is not None:
        stmt = stmt.where(SubscriptionRecord.membership_id == membership_id)
    rows = (await session.execute(stmt)).scalars()
    return tuple(_to_sub_view(r) for r in rows)


def verify_and_extract_webhook_grant(
    *,
    webhook_id: str,
    provider: str,
    event_timestamp: datetime,
    raw_payload: bytes,
    signature_hex: str,
    secret: str,
    plans: Mapping[str, SubscriptionPlanSpec] | None = None,
    now: datetime | None = None,
    max_skew_seconds: int = 300,
) -> tuple[uuid.UUID, uuid.UUID, dict[str, Any]]:
    """Verify HMAC signature/replay window and extract `(tenant_id, membership_id, payload_dict)`."""
    if not provider or not provider.strip():
        raise BillingError("MISSING_WEBHOOK_PROVIDER", "provider must be non-empty")
    check_now = now or datetime.now(tz=UTC)
    verify_webhook_signature(
        webhook_id=webhook_id,
        event_timestamp=event_timestamp,
        raw_payload=raw_payload,
        signature_hex=signature_hex,
        secret=secret,
        now=check_now,
        max_skew_seconds=max_skew_seconds,
    )
    if plans is not None:
        grant = parse_signed_grant(raw_payload, plans=plans)
        raw_dict = json.loads(raw_payload.decode("utf-8"))
        try:
            tenant_uuid = uuid.UUID(grant.tenant_id)
            member_uuid = uuid.UUID(grant.membership_id)
        except ValueError as exc:
            raise BillingError("INVALID_WEBHOOK_PAYLOAD", "tenant_id and membership_id must be valid UUIDs") from exc
        return tenant_uuid, member_uuid, dict(raw_dict)

    try:
        raw_dict = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", "Signed payload is not valid UTF-8 JSON") from exc
    if not isinstance(raw_dict, dict):
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", "Signed payload must be a JSON object")
    try:
        tenant_uuid = uuid.UUID(str(raw_dict.get("tenant_id") or ""))
        member_uuid = uuid.UUID(str(raw_dict.get("membership_id") or ""))
    except ValueError as exc:
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", "tenant_id and membership_id must be valid UUIDs") from exc
    return tenant_uuid, member_uuid, raw_dict


async def process_verified_payment_webhook(
    session: AsyncSession,
    *,
    webhook_id: str,
    provider: str,
    event_timestamp: datetime,
    raw_payload: bytes,
    signature_hex: str,
    secret: str,
    now: datetime | None = None,
    max_skew_seconds: int = 300,
    correlation_id: str | None = None,
) -> WebhookOutcomeView:
    """Verify HMAC, dedup against `payment_webhooks`, upsert `subscriptions` & emit outbox event."""
    plans = await load_plan_specs(session)
    tenant_uuid, member_uuid, raw_dict = verify_and_extract_webhook_grant(
        webhook_id=webhook_id,
        provider=provider,
        event_timestamp=event_timestamp,
        raw_payload=raw_payload,
        signature_hex=signature_hex,
        secret=secret,
        plans=plans,
        now=now,
        max_skew_seconds=max_skew_seconds,
    )

    member_ok = (
        await session.execute(
            text("SELECT 1 FROM memberships WHERE id = :membership_id AND tenant_id = :tenant_id"),
            {"membership_id": member_uuid, "tenant_id": tenant_uuid},
        )
    ).first()
    if member_ok is None:
        raise BillingNotFoundError(
            f"membership {member_uuid} was not found in tenant {tenant_uuid}"
        )

    existing_sub = (
        await session.execute(
            select(SubscriptionRecord)
            .where(
                SubscriptionRecord.tenant_id == tenant_uuid,
                SubscriptionRecord.membership_id == member_uuid,
            )
            .order_by(SubscriptionRecord.current_period_end.desc(), SubscriptionRecord.created_at.desc())
            .with_for_update()
        )
    ).scalars().first()

    clean_wh_id = webhook_id.strip()
    clean_provider = provider.strip()

    try:
        async with session.begin_nested():
            session.add(
                PaymentWebhookRecord(
                    webhook_id=clean_wh_id,
                    provider=clean_provider,
                    raw_payload_json=raw_dict,
                )
            )
            await session.flush()
    except IntegrityError as exc:
        if existing_sub is None:
            raise BillingError(
                "WEBHOOK_ID_REUSED",
                f"Webhook id {clean_wh_id!r} was already recorded without a subscription for {member_uuid}",
            ) from exc
        return WebhookOutcomeView(
            is_duplicate=True,
            webhook_id=clean_wh_id,
            provider=clean_provider,
            emitted_outbox_event=False,
            subscription=_to_sub_view(existing_sub),
        )

    initial_subs: tuple[tuple[tuple[str, str], SubscriptionState], ...] = ()
    if existing_sub is not None:
        initial_subs = (
            (
                (str(tenant_uuid), str(member_uuid)),
                SubscriptionState(
                    tenant_id=str(tenant_uuid),
                    membership_id=str(member_uuid),
                    plan_code=existing_sub.plan_code,
                    status=SubscriptionStatus(existing_sub.status),
                    current_period_end=existing_sub.current_period_end,
                ),
            ),
        )

    check_now = now or datetime.now(tz=UTC)
    ledger = QuotaLedgerState(subscriptions=initial_subs)
    cmd = PaymentWebhookCommand(
        webhook_id=clean_wh_id,
        provider=clean_provider,
        event_timestamp=event_timestamp,
        raw_payload=raw_payload,
        signature_hex=signature_hex,
    )
    _, result = process_payment_webhook(
        ledger,
        cmd,
        secret=secret,
        plans=plans,
        now=check_now,
        max_skew_seconds=max_skew_seconds,
    )

    if existing_sub is None:
        sub_row = SubscriptionRecord(
            tenant_id=tenant_uuid,
            membership_id=member_uuid,
            plan_code=result.subscription.plan_code,
            status=result.subscription.status.value,
            current_period_end=result.subscription.current_period_end,
        )
        session.add(sub_row)
    else:
        existing_sub.plan_code = result.subscription.plan_code
        existing_sub.status = result.subscription.status.value
        existing_sub.current_period_end = result.subscription.current_period_end
        sub_row = existing_sub
    await session.flush()

    emitted = False
    if result.emitted_event is not None:
        envelope = build_catalog_envelope(
            event_type="subscription.updated",
            schema_version=1,
            aggregate_type="subscription",
            aggregate_id=sub_row.id,
            correlation_id=correlation_id,
            payload=SubscriptionUpdatedPayload(
                tenant_id=tenant_uuid,
                membership_id=member_uuid,
                plan_code=result.emitted_event.entitlement_tier,
                status=result.emitted_event.status.value,
                current_period_end=result.emitted_event.current_period_end,
            ),
        )
        await enqueue_outbox(session, envelope)
        emitted = True

    return WebhookOutcomeView(
        is_duplicate=False,
        webhook_id=clean_wh_id,
        provider=clean_provider,
        emitted_outbox_event=emitted,
        subscription=_to_sub_view(sub_row),
    )


async def consume_member_quota(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    resource_code: str,
    delta: int,
    idempotency_key: str,
    default_plan_code: str = "FREE",
    now: datetime | None = None,
) -> QuotaConsumptionView:
    """Charge quota into `usage_ledger` idempotently using Pure Core plan limits."""
    clean_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
    clean_resource = resource_code.strip() if isinstance(resource_code, str) else ""
    if not clean_key:
        raise BillingError("MISSING_IDEMPOTENCY_KEY", "idempotency_key must be non-empty")
    if not clean_resource:
        raise BillingError("INVALID_RESOURCE_CODE", "resource_code must be non-empty")

    plans = await load_plan_specs(session)
    sub_row = (
        await session.execute(
            select(SubscriptionRecord)
            .where(
                SubscriptionRecord.tenant_id == tenant_id,
                SubscriptionRecord.membership_id == membership_id,
            )
            .order_by(SubscriptionRecord.current_period_end.desc(), SubscriptionRecord.created_at.desc())
        )
    ).scalars().first()

    existing_entry = (
        await session.execute(
            select(UsageLedgerRecord).where(
                UsageLedgerRecord.tenant_id == tenant_id,
                UsageLedgerRecord.idempotency_key == clean_key,
            )
        )
    ).scalar_one_or_none()
    if existing_entry is not None and (
        existing_entry.membership_id != membership_id
        or existing_entry.resource_code != clean_resource
        or existing_entry.delta != delta
    ):
        raise BillingError(
            "IDEMPOTENCY_KEY_REUSED",
            "idempotency_key was already used with a different usage payload",
        )

    used_so_far = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(UsageLedgerRecord.delta), 0)).where(
                    UsageLedgerRecord.tenant_id == tenant_id,
                    UsageLedgerRecord.membership_id == membership_id,
                    UsageLedgerRecord.resource_code == clean_resource,
                )
            )
        ).scalar_one()
    )

    check_now = now or datetime.now(tz=UTC)
    subs_tuple: tuple[tuple[tuple[str, str], SubscriptionState], ...] = ()
    effective_plan = default_plan_code
    if sub_row is not None:
        sub_state = SubscriptionState(
            tenant_id=str(tenant_id),
            membership_id=str(membership_id),
            plan_code=sub_row.plan_code,
            status=SubscriptionStatus(sub_row.status),
            current_period_end=sub_row.current_period_end,
        )
        subs_tuple = (((str(tenant_id), str(membership_id)), sub_state),)
        if sub_state.status == SubscriptionStatus.ACTIVE and sub_state.current_period_end > check_now:
            effective_plan = sub_state.plan_code

    seen_keys = frozenset({(str(tenant_id), clean_key)}) if existing_entry is not None else frozenset()
    ledger = QuotaLedgerState(
        subscriptions=subs_tuple,
        usage_by_member=(((str(tenant_id), str(membership_id), clean_resource), used_so_far),),
        seen_usage_keys=seen_keys,
    )
    entry = UsageLedgerEntry(
        tenant_id=str(tenant_id),
        membership_id=str(membership_id),
        resource_code=clean_resource,
        delta=delta,
        idempotency_key=clean_key,
    )
    _, charged_now, remaining = consume_quota_idempotent(
        ledger,
        entry,
        plans=plans,
        now=check_now,
        default_plan_code=default_plan_code,
    )

    if charged_now:
        session.add(
            UsageLedgerRecord(
                tenant_id=tenant_id,
                membership_id=membership_id,
                resource_code=clean_resource,
                delta=delta,
                idempotency_key=clean_key,
            )
        )
        await session.flush()
        used_so_far += delta

    return QuotaConsumptionView(
        was_charged_now=charged_now,
        resource_code=clean_resource,
        delta=delta,
        used_total=used_so_far,
        remaining_quota=remaining,
        plan_code=effective_plan,
    )
