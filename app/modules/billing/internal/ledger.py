"""G05 HMAC webhook verification, idempotent subscription updates, and quota usage ledger."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType


class SubscriptionStatus(StrEnum):
    """Exact DDL CHECK values for `subscriptions.status`."""

    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class BillingError(ValueError):
    """Raised when webhook signature verification, replay guard, or quota checks fail."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SubscriptionPlanSpec:
    code: str  # FREE | STUDENT_PRO | TEACHER_PRO | SCHOOL_ENTERPRISE
    name: str
    price_vnd: int
    quotas: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    tenant_id: str
    membership_id: str
    plan_code: str
    status: SubscriptionStatus
    current_period_end: datetime


@dataclass(frozen=True, slots=True)
class PaymentWebhookCommand:
    webhook_id: str
    provider: str
    tenant_id: str
    membership_id: str
    plan_code: str
    event_timestamp: datetime
    raw_payload: bytes
    signature_hex: str
    period_extension_days: int = 30


@dataclass(frozen=True, slots=True)
class SubscriptionUpdatedEvent:
    event_type: str
    tenant_id: str
    membership_id: str
    entitlement_tier: str
    status: SubscriptionStatus
    current_period_end: datetime


@dataclass(frozen=True, slots=True)
class WebhookProcessingResult:
    is_duplicate: bool
    subscription: SubscriptionState
    emitted_event: SubscriptionUpdatedEvent | None


@dataclass(frozen=True, slots=True)
class UsageLedgerEntry:
    tenant_id: str
    membership_id: str
    resource_code: str
    delta: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class QuotaLedgerState:
    """Immutable in-memory representation of quota consumption & processed webhooks."""

    processed_webhook_ids: frozenset[str] = field(default_factory=frozenset)
    subscriptions: tuple[tuple[tuple[str, str], SubscriptionState], ...] = ()
    usage_by_member: tuple[tuple[tuple[str, str, str], int], ...] = ()
    seen_usage_keys: frozenset[str] = field(default_factory=frozenset)


def sign_webhook_payload(
    *,
    webhook_id: str,
    timestamp_unix: int,
    raw_payload: bytes,
    secret: str,
) -> str:
    """Compute HMAC-SHA256 hex digest matching `f'{webhook_id}.{timestamp_unix}.' + raw_payload`."""
    if not secret:
        raise ValueError("Webhook signing secret must be non-empty")
    message = f"{webhook_id}.{timestamp_unix}.".encode() + raw_payload
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    *,
    webhook_id: str,
    event_timestamp: datetime,
    raw_payload: bytes,
    signature_hex: str,
    secret: str,
    now: datetime,
    max_skew_seconds: int = 300,
) -> None:
    """
    Validate HMAC-SHA256 signature in constant time and reject stale/future replay attacks.

    Never includes the secret key anywhere in raised exceptions.
    """
    if not secret:
        raise BillingError("MISSING_WEBHOOK_SECRET", "Webhook verification secret is not configured")

    skew = abs((now - event_timestamp).total_seconds())
    if skew > max_skew_seconds:
        raise BillingError(
            "WEBHOOK_TIMESTAMP_OUT_OF_WINDOW",
            f"Webhook timestamp skew {skew:.1f}s exceeds allowed {max_skew_seconds}s",
        )

    expected = sign_webhook_payload(
        webhook_id=webhook_id,
        timestamp_unix=int(event_timestamp.timestamp()),
        raw_payload=raw_payload,
        secret=secret,
    )
    if not hmac.compare_digest(expected.lower(), signature_hex.strip().lower()):
        raise BillingError("INVALID_WEBHOOK_SIGNATURE", f"Webhook {webhook_id!r} signature mismatch")


def process_payment_webhook(
    ledger: QuotaLedgerState,
    cmd: PaymentWebhookCommand,
    *,
    secret: str,
    plans: Mapping[str, SubscriptionPlanSpec],
    now: datetime,
    max_skew_seconds: int = 300,
) -> tuple[QuotaLedgerState, WebhookProcessingResult]:
    """
    Verify HMAC signature, guard replay, and idempotently activate/extend a tenant subscription.

    Duplicate `webhook_id` calls perform zero mutation and emit zero duplicate events.
    """
    verify_webhook_signature(
        webhook_id=cmd.webhook_id,
        event_timestamp=cmd.event_timestamp,
        raw_payload=cmd.raw_payload,
        signature_hex=cmd.signature_hex,
        secret=secret,
        now=now,
        max_skew_seconds=max_skew_seconds,
    )

    if cmd.plan_code not in plans:
        raise BillingError("UNKNOWN_SUBSCRIPTION_PLAN", f"Unknown plan_code: {cmd.plan_code!r}")

    subs_map = dict(ledger.subscriptions)
    member_key = (cmd.tenant_id, cmd.membership_id)

    if cmd.webhook_id in ledger.processed_webhook_ids:
        existing_sub = subs_map[member_key]
        return ledger, WebhookProcessingResult(
            is_duplicate=True,
            subscription=existing_sub,
            emitted_event=None,
        )

    existing_sub = subs_map.get(member_key)
    base_time = (
        existing_sub.current_period_end
        if existing_sub is not None
        and existing_sub.status == SubscriptionStatus.ACTIVE
        and existing_sub.current_period_end > now
        else now
    )
    new_period_end = base_time + timedelta(days=cmd.period_extension_days)
    updated_sub = SubscriptionState(
        tenant_id=cmd.tenant_id,
        membership_id=cmd.membership_id,
        plan_code=cmd.plan_code,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=new_period_end,
    )
    subs_map[member_key] = updated_sub

    event = SubscriptionUpdatedEvent(
        event_type="subscription.updated",
        tenant_id=cmd.tenant_id,
        membership_id=cmd.membership_id,
        entitlement_tier=cmd.plan_code,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=new_period_end,
    )
    next_ledger = QuotaLedgerState(
        processed_webhook_ids=ledger.processed_webhook_ids | {cmd.webhook_id},
        subscriptions=tuple(sorted(subs_map.items())),
        usage_by_member=ledger.usage_by_member,
        seen_usage_keys=ledger.seen_usage_keys,
    )
    return next_ledger, WebhookProcessingResult(
        is_duplicate=False,
        subscription=updated_sub,
        emitted_event=event,
    )


def consume_quota_idempotent(
    ledger: QuotaLedgerState,
    entry: UsageLedgerEntry,
    *,
    plans: Mapping[str, SubscriptionPlanSpec],
    default_plan_code: str = "FREE",
) -> tuple[QuotaLedgerState, bool, int]:
    """
    Consume quota (`delta > 0`) for a member resource with strict `idempotency_key` deduplication.

    Returns `(next_ledger, was_charged_now, remaining_quota)`.
    """
    if entry.delta <= 0:
        raise ValueError("Usage delta must be > 0")
    if not entry.idempotency_key:
        raise ValueError("idempotency_key must be non-empty")

    subs_map = dict(ledger.subscriptions)
    sub = subs_map.get((entry.tenant_id, entry.membership_id))
    plan_code = (
        sub.plan_code
        if sub is not None and sub.status == SubscriptionStatus.ACTIVE
        else default_plan_code
    )
    plan = plans[plan_code]
    limit = plan.quotas.get(entry.resource_code, 0)

    usage_map = dict(ledger.usage_by_member)
    usage_key = (entry.tenant_id, entry.membership_id, entry.resource_code)
    used_so_far = usage_map.get(usage_key, 0)

    if entry.idempotency_key in ledger.seen_usage_keys:
        return ledger, False, max(0, limit - used_so_far)

    if used_so_far + entry.delta > limit:
        raise BillingError(
            "QUOTA_EXCEEDED",
            f"Quota exceeded for {entry.resource_code}: used={used_so_far}, requested={entry.delta}, limit={limit}",
        )

    new_used = used_so_far + entry.delta
    usage_map[usage_key] = new_used
    next_ledger = QuotaLedgerState(
        processed_webhook_ids=ledger.processed_webhook_ids,
        subscriptions=ledger.subscriptions,
        usage_by_member=tuple(sorted(usage_map.items())),
        seen_usage_keys=ledger.seen_usage_keys | {entry.idempotency_key},
    )
    return next_ledger, True, limit - new_used
