"""G05 HMAC webhook verification, idempotent subscription updates, and quota usage ledger."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any


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
    """Transport envelope only: what is granted (tenant, member, plan, period) is read from the signed payload."""

    webhook_id: str
    provider: str
    event_timestamp: datetime
    raw_payload: bytes
    signature_hex: str


@dataclass(frozen=True, slots=True)
class SignedPaymentGrant:
    """Fields parsed from the verified `raw_payload` (never from unsigned command fields)."""

    tenant_id: str
    membership_id: str
    plan_code: str
    period_extension_days: int


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

    # ((provider, webhook_id), (tenant_id, membership_id)) of every applied webhook.
    processed_webhooks: tuple[tuple[tuple[str, str], tuple[str, str]], ...] = ()
    subscriptions: tuple[tuple[tuple[str, str], SubscriptionState], ...] = ()
    usage_by_member: tuple[tuple[tuple[str, str, str], int], ...] = ()
    # (tenant_id, idempotency_key): a key is only unique inside one tenant.
    seen_usage_keys: frozenset[tuple[str, str]] = field(default_factory=frozenset)


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
    if not webhook_id or not webhook_id.strip():
        raise BillingError("MISSING_WEBHOOK_ID", "webhook_id must be non-empty")
    if event_timestamp.tzinfo is None or now.tzinfo is None:
        raise BillingError("WEBHOOK_TIMESTAMP_NOT_AWARE", "Webhook and verification clocks must be timezone-aware")
    if not isinstance(signature_hex, str):
        raise BillingError("INVALID_WEBHOOK_SIGNATURE", "Webhook signature must be a hex string")

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
    # Compare as bytes: str.compare_digest raises TypeError on non-ASCII input.
    if not hmac.compare_digest(expected.encode("ascii"), signature_hex.strip().lower().encode("utf-8")):
        raise BillingError("INVALID_WEBHOOK_SIGNATURE", f"Webhook {webhook_id!r} signature mismatch")


_ACCEPTED_EVENT_TYPES: frozenset[str] = frozenset({"payment.succeeded"})
_MAX_PERIOD_DAYS = 366


def _require_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", f"Signed payload field {key!r} must be a non-empty string")
    return value.strip()


def parse_signed_grant(raw_payload: bytes, *, plans: Mapping[str, SubscriptionPlanSpec]) -> SignedPaymentGrant:
    """Parse the grant out of an already signature-verified payload; fail closed on anything unexpected."""
    try:
        data = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", "Signed payload is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise BillingError("INVALID_WEBHOOK_PAYLOAD", "Signed payload must be a JSON object")
    event_type = data.get("event_type")
    if event_type not in _ACCEPTED_EVENT_TYPES:
        raise BillingError("UNSUPPORTED_WEBHOOK_EVENT", "Webhook event type does not grant a subscription")
    plan_code = _require_text(data, "plan_code")
    if plan_code not in plans:
        raise BillingError("UNKNOWN_SUBSCRIPTION_PLAN", f"Unknown plan_code: {plan_code!r}")
    days = data.get("period_extension_days", 30)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= _MAX_PERIOD_DAYS:
        raise BillingError(
            "INVALID_WEBHOOK_PAYLOAD", f"period_extension_days must be an integer in 1..{_MAX_PERIOD_DAYS}"
        )
    return SignedPaymentGrant(
        tenant_id=_require_text(data, "tenant_id"),
        membership_id=_require_text(data, "membership_id"),
        plan_code=plan_code,
        period_extension_days=days,
    )


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
    if not cmd.provider or not cmd.provider.strip():
        raise BillingError("MISSING_WEBHOOK_PROVIDER", "provider must be non-empty")
    verify_webhook_signature(
        webhook_id=cmd.webhook_id,
        event_timestamp=cmd.event_timestamp,
        raw_payload=cmd.raw_payload,
        signature_hex=cmd.signature_hex,
        secret=secret,
        now=now,
        max_skew_seconds=max_skew_seconds,
    )
    grant = parse_signed_grant(cmd.raw_payload, plans=plans)

    subs_map = dict(ledger.subscriptions)
    member_key = (grant.tenant_id, grant.membership_id)
    webhook_key = (cmd.provider, cmd.webhook_id)
    processed = dict(ledger.processed_webhooks)

    if webhook_key in processed:
        if processed[webhook_key] != member_key or member_key not in subs_map:
            raise BillingError("WEBHOOK_ID_REUSED", "Webhook id was already applied to a different subject")
        return ledger, WebhookProcessingResult(
            is_duplicate=True,
            subscription=subs_map[member_key],
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
    new_period_end = base_time + timedelta(days=grant.period_extension_days)
    updated_sub = SubscriptionState(
        tenant_id=grant.tenant_id,
        membership_id=grant.membership_id,
        plan_code=grant.plan_code,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=new_period_end,
    )
    subs_map[member_key] = updated_sub

    event = SubscriptionUpdatedEvent(
        event_type="subscription.updated",
        tenant_id=grant.tenant_id,
        membership_id=grant.membership_id,
        entitlement_tier=grant.plan_code,
        status=SubscriptionStatus.ACTIVE,
        current_period_end=new_period_end,
    )
    next_ledger = QuotaLedgerState(
        processed_webhooks=tuple(sorted({**processed, webhook_key: member_key}.items())),
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
    now: datetime,
    default_plan_code: str = "FREE",
) -> tuple[QuotaLedgerState, bool, int]:
    """
    Consume quota (`delta > 0`) for a member resource with strict `(tenant_id, idempotency_key)` deduplication.

    A subscription only lends its plan while `ACTIVE` and before `current_period_end`; afterwards the
    member falls back to `default_plan_code`.

    Returns `(next_ledger, was_charged_now, remaining_quota)`.
    """
    if isinstance(entry.delta, bool) or not isinstance(entry.delta, int) or entry.delta <= 0:
        raise BillingError("INVALID_USAGE_DELTA", "Usage delta must be a positive integer")
    if not entry.idempotency_key or not entry.idempotency_key.strip():
        raise BillingError("MISSING_IDEMPOTENCY_KEY", "idempotency_key must be non-empty")
    if not entry.tenant_id or not entry.membership_id:
        raise BillingError("MISSING_USAGE_SUBJECT", "tenant_id and membership_id must be non-empty")
    if now.tzinfo is None:
        raise BillingError("CLOCK_NOT_AWARE", "now must be timezone-aware")

    subs_map = dict(ledger.subscriptions)
    sub = subs_map.get((entry.tenant_id, entry.membership_id))
    plan_code = (
        sub.plan_code
        if sub is not None and sub.status == SubscriptionStatus.ACTIVE and sub.current_period_end > now
        else default_plan_code
    )
    plan = plans.get(plan_code)
    if plan is None:
        raise BillingError("UNKNOWN_SUBSCRIPTION_PLAN", f"Unknown plan_code: {plan_code!r}")
    limit = plan.quotas.get(entry.resource_code, 0)

    usage_map = dict(ledger.usage_by_member)
    usage_key = (entry.tenant_id, entry.membership_id, entry.resource_code)
    used_so_far = usage_map.get(usage_key, 0)
    dedup_key = (entry.tenant_id, entry.idempotency_key.strip())

    if dedup_key in ledger.seen_usage_keys:
        return ledger, False, max(0, limit - used_so_far)

    if used_so_far + entry.delta > limit:
        raise BillingError(
            "QUOTA_EXCEEDED",
            f"Quota exceeded for {entry.resource_code}: used={used_so_far}, requested={entry.delta}, limit={limit}",
        )

    new_used = used_so_far + entry.delta
    usage_map[usage_key] = new_used
    next_ledger = QuotaLedgerState(
        processed_webhooks=ledger.processed_webhooks,
        subscriptions=ledger.subscriptions,
        usage_by_member=tuple(sorted(usage_map.items())),
        seen_usage_keys=ledger.seen_usage_keys | {dedup_key},
    )
    return next_ledger, True, limit - new_used
