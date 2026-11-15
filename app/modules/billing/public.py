"""Public interface for module billing (G05)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from app.modules.billing.internal.ledger import (
    BillingError,
    PaymentWebhookCommand,
    QuotaLedgerState,
    SignedPaymentGrant,
    SubscriptionPlanSpec,
    SubscriptionState,
    SubscriptionStatus,
    SubscriptionUpdatedEvent,
    UsageLedgerEntry,
    WebhookProcessingResult,
    consume_quota_idempotent,
    parse_signed_grant,
    process_payment_webhook,
    sign_webhook_payload,
    verify_webhook_signature,
)

if TYPE_CHECKING:
    from app.modules.billing.internal.models import (
        PaymentWebhookRecord,
        SubscriptionPlanRecord,
        SubscriptionRecord,
        UsageLedgerRecord,
    )
    from app.modules.billing.internal.service import (
        BillingNotFoundError,
        QuotaConsumptionView,
        SubscriptionPlanView,
        SubscriptionView,
        WebhookOutcomeView,
        consume_member_quota,
        list_subscription_plans,
        list_subscriptions,
        load_plan_specs,
        process_verified_payment_webhook,
        verify_and_extract_webhook_grant,
    )
    from app.modules.billing.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "PaymentWebhookRecord": ("app.modules.billing.internal.models", "PaymentWebhookRecord"),
    "SubscriptionPlanRecord": ("app.modules.billing.internal.models", "SubscriptionPlanRecord"),
    "SubscriptionRecord": ("app.modules.billing.internal.models", "SubscriptionRecord"),
    "UsageLedgerRecord": ("app.modules.billing.internal.models", "UsageLedgerRecord"),
    "BillingNotFoundError": ("app.modules.billing.internal.service", "BillingNotFoundError"),
    "QuotaConsumptionView": ("app.modules.billing.internal.service", "QuotaConsumptionView"),
    "SubscriptionPlanView": ("app.modules.billing.internal.service", "SubscriptionPlanView"),
    "SubscriptionView": ("app.modules.billing.internal.service", "SubscriptionView"),
    "WebhookOutcomeView": ("app.modules.billing.internal.service", "WebhookOutcomeView"),
    "consume_member_quota": ("app.modules.billing.internal.service", "consume_member_quota"),
    "list_subscription_plans": ("app.modules.billing.internal.service", "list_subscription_plans"),
    "list_subscriptions": ("app.modules.billing.internal.service", "list_subscriptions"),
    "load_plan_specs": ("app.modules.billing.internal.service", "load_plan_specs"),
    "process_verified_payment_webhook": (
        "app.modules.billing.internal.service",
        "process_verified_payment_webhook",
    ),
    "verify_and_extract_webhook_grant": (
        "app.modules.billing.internal.service",
        "verify_and_extract_webhook_grant",
    ),
    "router": ("app.modules.billing.router", "router"),
}

__all__ = [
    "BillingError",
    "BillingNotFoundError",
    "PaymentWebhookCommand",
    "PaymentWebhookRecord",
    "QuotaConsumptionView",
    "QuotaLedgerState",
    "SignedPaymentGrant",
    "SubscriptionPlanRecord",
    "SubscriptionPlanSpec",
    "SubscriptionPlanView",
    "SubscriptionRecord",
    "SubscriptionState",
    "SubscriptionStatus",
    "SubscriptionUpdatedEvent",
    "SubscriptionView",
    "UsageLedgerEntry",
    "UsageLedgerRecord",
    "WebhookOutcomeView",
    "WebhookProcessingResult",
    "consume_member_quota",
    "consume_quota_idempotent",
    "list_subscription_plans",
    "list_subscriptions",
    "load_plan_specs",
    "parse_signed_grant",
    "process_payment_webhook",
    "process_verified_payment_webhook",
    "router",
    "sign_webhook_payload",
    "verify_and_extract_webhook_grant",
    "verify_webhook_signature",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    value = getattr(import_module(module_path), attr_name)
    globals()[name] = value
    return value
