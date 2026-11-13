"""Pure unit tests for G05 Billing, HMAC Webhook Verification & Usage Ledger (P35)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.modules.billing.public import (
    BillingError,
    PaymentWebhookCommand,
    QuotaLedgerState,
    SubscriptionPlanSpec,
    SubscriptionStatus,
    UsageLedgerEntry,
    consume_quota_idempotent,
    process_payment_webhook,
    sign_webhook_payload,
)

_PLANS = {
    "FREE": SubscriptionPlanSpec("FREE", "Miễn phí", 0, {"BRANDED_PDF_EXPORT": 1, "AI_TUTOR_MESSAGE": 5}),
    "STUDENT_PRO": SubscriptionPlanSpec(
        "STUDENT_PRO", "Học sinh Pro", 99_000, {"BRANDED_PDF_EXPORT": 20, "AI_TUTOR_MESSAGE": 200}
    ),
}
_SECRET = "whsec_live_super_secret_999"


def test_subscription_status_matches_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for status in SubscriptionStatus:
        assert f"'{status.value}'" in ddl


def test_hmac_webhook_blocks_tampering_and_stale_replay_never_leaks_secret() -> None:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    payload = b'{"amount_vnd":99000}'
    sig = sign_webhook_payload(
        webhook_id="wh-1", timestamp_unix=int(now.timestamp()), raw_payload=payload, secret=_SECRET
    )

    # Forged signature fails cleanly without leaking _SECRET
    forged = PaymentWebhookCommand(
        webhook_id="wh-1",
        provider="VIETQR",
        tenant_id="t1",
        membership_id="m1",
        plan_code="STUDENT_PRO",
        event_timestamp=now,
        raw_payload=payload,
        signature_hex="0" * 64,
    )
    with pytest.raises(BillingError) as bad_sig:
        process_payment_webhook(QuotaLedgerState(), forged, secret=_SECRET, plans=_PLANS, now=now)
    assert bad_sig.value.code == "INVALID_WEBHOOK_SIGNATURE"
    assert _SECRET not in str(bad_sig.value)

    # Stale timestamp outside 5-minute window fails replay guard
    stale = PaymentWebhookCommand(
        webhook_id="wh-1",
        provider="VIETQR",
        tenant_id="t1",
        membership_id="m1",
        plan_code="STUDENT_PRO",
        event_timestamp=now - timedelta(minutes=10),
        raw_payload=payload,
        signature_hex=sig,
    )
    with pytest.raises(BillingError) as stale_err:
        process_payment_webhook(QuotaLedgerState(), stale, secret=_SECRET, plans=_PLANS, now=now)
    assert stale_err.value.code == "WEBHOOK_TIMESTAMP_OUT_OF_WINDOW"
    assert _SECRET not in str(stale_err.value)


def test_duplicate_webhook_extends_subscription_exactly_once_and_quota_ledger_idempotent() -> None:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    payload = b'{"order_id":"ORD-1"}'
    sig = sign_webhook_payload(
        webhook_id="wh-ord-1", timestamp_unix=int(now.timestamp()), raw_payload=payload, secret=_SECRET
    )
    cmd = PaymentWebhookCommand(
        webhook_id="wh-ord-1",
        provider="PAYOS",
        tenant_id="t1",
        membership_id="m1",
        plan_code="STUDENT_PRO",
        event_timestamp=now,
        raw_payload=payload,
        signature_hex=sig,
        period_extension_days=30,
    )

    ledger = QuotaLedgerState()
    ledger, first = process_payment_webhook(ledger, cmd, secret=_SECRET, plans=_PLANS, now=now)
    assert first.is_duplicate is False
    assert first.emitted_event is not None
    assert first.emitted_event.entitlement_tier == "STUDENT_PRO"
    expected_end = now + timedelta(days=30)
    assert first.subscription.current_period_end == expected_end

    # Replay same webhook 3 times -> period does NOT extend to 60/90/120 days and 0 extra events emitted
    for _ in range(3):
        ledger, dup = process_payment_webhook(ledger, cmd, secret=_SECRET, plans=_PLANS, now=now)
        assert dup.is_duplicate is True
        assert dup.emitted_event is None
        assert dup.subscription.current_period_end == expected_end

    # Usage ledger idempotent debit against active STUDENT_PRO (limit=20 PDFs)
    ledger, charged, rem = consume_quota_idempotent(
        ledger,
        UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 5, "exp-req-1"),
        plans=_PLANS,
    )
    assert charged is True and rem == 15
    ledger, charged_again, rem_again = consume_quota_idempotent(
        ledger,
        UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 5, "exp-req-1"),
        plans=_PLANS,
    )
    assert charged_again is False and rem_again == 15

    # Exceeding remaining 15 raises QUOTA_EXCEEDED
    with pytest.raises(BillingError) as q_err:
        consume_quota_idempotent(
            ledger,
            UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 16, "exp-req-2"),
            plans=_PLANS,
        )
    assert q_err.value.code == "QUOTA_EXCEEDED"
