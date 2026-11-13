"""Pure unit tests for G05 Billing, HMAC Webhook Verification & Usage Ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.modules.billing.public import (
    BillingError,
    PaymentWebhookCommand,
    QuotaLedgerState,
    SubscriptionPlanSpec,
    SubscriptionState,
    SubscriptionStatus,
    UsageLedgerEntry,
    consume_quota_idempotent,
    process_payment_webhook,
    sign_webhook_payload,
    verify_webhook_signature,
)

_PLANS = {
    "FREE": SubscriptionPlanSpec("FREE", "Miễn phí", 0, {"BRANDED_PDF_EXPORT": 1, "AI_TUTOR_MESSAGE": 5}),
    "STUDENT_PRO": SubscriptionPlanSpec(
        "STUDENT_PRO", "Học sinh Pro", 99_000, {"BRANDED_PDF_EXPORT": 20, "AI_TUTOR_MESSAGE": 200}
    ),
}
_SECRET = "whsec_live_super_secret_999"
_NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _payload(**overrides: object) -> bytes:
    body: dict[str, object] = {
        "event_type": "payment.succeeded",
        "tenant_id": "t1",
        "membership_id": "m1",
        "plan_code": "STUDENT_PRO",
        "period_extension_days": 30,
    }
    body.update(overrides)
    return json.dumps(body).encode()


def _cmd(
    payload: bytes | None = None,
    *,
    webhook_id: str = "wh-1",
    provider: str = "PAYOS",
    at: datetime = _NOW,
) -> PaymentWebhookCommand:
    raw = _payload() if payload is None else payload
    sig = sign_webhook_payload(
        webhook_id=webhook_id, timestamp_unix=int(at.timestamp()), raw_payload=raw, secret=_SECRET
    )
    return PaymentWebhookCommand(webhook_id, provider, at, raw, sig)


def _process(ledger: QuotaLedgerState, cmd: PaymentWebhookCommand, now: datetime = _NOW):
    return process_payment_webhook(ledger, cmd, secret=_SECRET, plans=_PLANS, now=now)


def test_subscription_status_matches_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for status in SubscriptionStatus:
        assert f"'{status.value}'" in ddl


def test_hmac_webhook_blocks_tampering_and_stale_replay_never_leaks_secret() -> None:
    forged = PaymentWebhookCommand("wh-1", "VIETQR", _NOW, _payload(), "0" * 64)
    with pytest.raises(BillingError) as bad_sig:
        _process(QuotaLedgerState(), forged)
    assert bad_sig.value.code == "INVALID_WEBHOOK_SIGNATURE"
    assert _SECRET not in str(bad_sig.value)

    stale = _cmd(at=_NOW - timedelta(minutes=10))
    with pytest.raises(BillingError) as stale_err:
        _process(QuotaLedgerState(), stale)
    assert stale_err.value.code == "WEBHOOK_TIMESTAMP_OUT_OF_WINDOW"
    assert _SECRET not in str(stale_err.value)


def test_future_timestamp_outside_window_and_boundary_skew() -> None:
    with pytest.raises(BillingError) as err:
        _process(QuotaLedgerState(), _cmd(at=_NOW + timedelta(minutes=6)))
    assert err.value.code == "WEBHOOK_TIMESTAMP_OUT_OF_WINDOW"
    _, result = _process(QuotaLedgerState(), _cmd(at=_NOW - timedelta(seconds=300)))
    assert result.is_duplicate is False


def test_naive_datetimes_fail_closed_with_billing_error() -> None:
    naive = datetime(2026, 11, 13, 12, 0)
    with pytest.raises(BillingError) as err:
        verify_webhook_signature(
            webhook_id="w", event_timestamp=naive, raw_payload=b"{}", signature_hex="00", secret=_SECRET, now=_NOW
        )
    assert err.value.code == "WEBHOOK_TIMESTAMP_NOT_AWARE"


def test_missing_secret_and_empty_ids_are_rejected() -> None:
    with pytest.raises(BillingError) as err:
        verify_webhook_signature(
            webhook_id="w", event_timestamp=_NOW, raw_payload=b"{}", signature_hex="00", secret="", now=_NOW
        )
    assert err.value.code == "MISSING_WEBHOOK_SECRET"
    with pytest.raises(BillingError) as err2:
        verify_webhook_signature(
            webhook_id=" ", event_timestamp=_NOW, raw_payload=b"{}", signature_hex="00", secret=_SECRET, now=_NOW
        )
    assert err2.value.code == "MISSING_WEBHOOK_ID"
    with pytest.raises(BillingError) as err3:
        _process(QuotaLedgerState(), _cmd(provider=" "))
    assert err3.value.code == "MISSING_WEBHOOK_PROVIDER"


def test_non_ascii_or_wrong_type_signature_raises_billing_error_not_type_error() -> None:
    for bad in ("é" * 64, "Đ" * 10, "​"):
        cmd = PaymentWebhookCommand("wh-1", "PAYOS", _NOW, _payload(), bad)
        with pytest.raises(BillingError) as err:
            _process(QuotaLedgerState(), cmd)
        assert err.value.code == "INVALID_WEBHOOK_SIGNATURE"
    cmd = PaymentWebhookCommand("wh-1", "PAYOS", _NOW, _payload(), None)  # type: ignore[arg-type]
    with pytest.raises(BillingError):
        _process(QuotaLedgerState(), cmd)


def test_uppercase_hex_signature_with_whitespace_is_accepted() -> None:
    cmd = _cmd()
    padded = PaymentWebhookCommand(
        cmd.webhook_id, cmd.provider, _NOW, cmd.raw_payload, " " + cmd.signature_hex.upper() + " "
    )
    _, result = _process(QuotaLedgerState(), padded)
    assert result.subscription.plan_code == "STUDENT_PRO"


def test_signature_binds_webhook_id_and_timestamp() -> None:
    cmd = _cmd()
    other_id = PaymentWebhookCommand("wh-2", cmd.provider, _NOW, cmd.raw_payload, cmd.signature_hex)
    with pytest.raises(BillingError) as err:
        _process(QuotaLedgerState(), other_id)
    assert err.value.code == "INVALID_WEBHOOK_SIGNATURE"
    shifted = PaymentWebhookCommand(
        "wh-1", cmd.provider, _NOW + timedelta(seconds=1), cmd.raw_payload, cmd.signature_hex
    )
    with pytest.raises(BillingError) as err2:
        _process(QuotaLedgerState(), shifted)
    assert err2.value.code == "INVALID_WEBHOOK_SIGNATURE"


def test_grant_comes_only_from_signed_payload_blocker() -> None:
    """BLOCKER: tenant/member/plan/period are read from the signed bytes."""
    raw = _payload(tenant_id="t7", membership_id="m7", period_extension_days=7)
    ledger, result = _process(QuotaLedgerState(), _cmd(raw))
    assert result.subscription.tenant_id == "t7"
    assert result.subscription.membership_id == "m7"
    assert result.subscription.current_period_end == _NOW + timedelta(days=7)
    assert ("t7", "m7") in dict(ledger.subscriptions)

    original = _cmd()
    for tampered in (
        _payload(tenant_id="attacker"),
        _payload(plan_code="FREE"),
        _payload(period_extension_days=3650),
        _payload(membership_id="m9"),
    ):
        forged = PaymentWebhookCommand("wh-1", "PAYOS", _NOW, tampered, original.signature_hex)
        with pytest.raises(BillingError) as err:
            _process(QuotaLedgerState(), forged)
        assert err.value.code == "INVALID_WEBHOOK_SIGNATURE"


def test_signed_payload_with_invalid_grant_is_rejected() -> None:
    cases = {
        "UNSUPPORTED_WEBHOOK_EVENT": [
            _payload(event_type="payment.refunded"),
            _payload(event_type=None),
            _payload(event_type=""),
        ],
        "UNKNOWN_SUBSCRIPTION_PLAN": [_payload(plan_code="GOD_MODE")],
        "INVALID_WEBHOOK_PAYLOAD": [
            _payload(plan_code=""),
            _payload(plan_code=None),
            _payload(tenant_id=""),
            _payload(tenant_id="  "),
            _payload(membership_id=5),
            _payload(period_extension_days=0),
            _payload(period_extension_days=-1),
            _payload(period_extension_days=367),
            _payload(period_extension_days=True),
            _payload(period_extension_days=1.5),
            _payload(period_extension_days="30"),
            b"not json",
            b"\xff\xfe",
            b"[1, 2]",
            b"null",
        ],
    }
    for code, payloads in cases.items():
        for raw in payloads:
            with pytest.raises(BillingError) as err:
                _process(QuotaLedgerState(), _cmd(raw))
            assert err.value.code == code, raw


def test_refund_event_never_activates_or_extends_a_plan() -> None:
    ledger, _ = _process(QuotaLedgerState(), _cmd())
    with pytest.raises(BillingError) as err:
        _process(ledger, _cmd(_payload(event_type="payment.refunded"), webhook_id="wh-refund"))
    assert err.value.code == "UNSUPPORTED_WEBHOOK_EVENT"


def test_duplicate_webhook_extends_subscription_exactly_once() -> None:
    cmd = _cmd(webhook_id="wh-ord-1", provider="PAYOS")
    ledger, first = _process(QuotaLedgerState(), cmd)
    assert first.is_duplicate is False
    assert first.emitted_event is not None
    assert first.emitted_event.entitlement_tier == "STUDENT_PRO"
    expected_end = _NOW + timedelta(days=30)
    assert first.subscription.current_period_end == expected_end

    for _ in range(3):
        same, dup = _process(ledger, cmd)
        assert same is ledger
        assert dup.is_duplicate is True
        assert dup.emitted_event is None
        assert dup.subscription.current_period_end == expected_end


def test_replayed_webhook_id_for_another_member_is_a_billing_error_not_key_error() -> None:
    ledger, _ = _process(QuotaLedgerState(), _cmd(_payload(membership_id="m1"), webhook_id="wh-x"))
    other = _cmd(_payload(membership_id="m2"), webhook_id="wh-x")
    with pytest.raises(BillingError) as err:
        _process(ledger, other)
    assert err.value.code == "WEBHOOK_ID_REUSED"


def test_same_webhook_id_from_another_provider_is_a_distinct_event() -> None:
    ledger, _ = _process(QuotaLedgerState(), _cmd(webhook_id="wh-1", provider="PAYOS"))
    ledger, second = _process(ledger, _cmd(webhook_id="wh-1", provider="VIETQR"))
    assert second.is_duplicate is False
    assert second.subscription.current_period_end == _NOW + timedelta(days=60)


def test_renewal_extends_from_future_period_end_but_expired_restarts_from_now() -> None:
    ledger, first = _process(QuotaLedgerState(), _cmd(webhook_id="a"))
    ledger, second = _process(ledger, _cmd(webhook_id="b"))
    assert second.subscription.current_period_end == first.subscription.current_period_end + timedelta(days=30)

    later = _NOW + timedelta(days=100)
    ledger, third = _process(ledger, _cmd(webhook_id="c", at=later), now=later)
    assert third.subscription.current_period_end == later + timedelta(days=30)


def test_ledger_input_is_not_mutated() -> None:
    ledger = QuotaLedgerState()
    _process(ledger, _cmd())
    assert ledger == QuotaLedgerState()


def _active_ledger() -> QuotaLedgerState:
    ledger, _ = _process(QuotaLedgerState(), _cmd())
    return ledger


def test_quota_ledger_is_idempotent_and_enforces_limit() -> None:
    ledger = _active_ledger()
    entry = UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 5, "exp-req-1")
    ledger, charged, rem = consume_quota_idempotent(ledger, entry, plans=_PLANS, now=_NOW)
    assert charged is True and rem == 15
    ledger, charged_again, rem_again = consume_quota_idempotent(ledger, entry, plans=_PLANS, now=_NOW)
    assert charged_again is False and rem_again == 15

    with pytest.raises(BillingError) as q_err:
        consume_quota_idempotent(
            ledger, UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 16, "exp-req-2"), plans=_PLANS, now=_NOW
        )
    assert q_err.value.code == "QUOTA_EXCEEDED"
    _, ok, rem_zero = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 15, "exp-req-3"), plans=_PLANS, now=_NOW
    )
    assert ok and rem_zero == 0


def test_expired_active_plan_falls_back_to_free_quota() -> None:
    ledger = _active_ledger()
    after_expiry = _NOW + timedelta(days=31)
    with pytest.raises(BillingError) as err:
        consume_quota_idempotent(
            ledger, UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 2, "k1"), plans=_PLANS, now=after_expiry
        )
    assert err.value.code == "QUOTA_EXCEEDED"
    _, charged, rem = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 1, "k2"), plans=_PLANS, now=after_expiry
    )
    assert charged and rem == 0
    just_before = _NOW + timedelta(days=30) - timedelta(seconds=1)
    _, _, rem_pro = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t1", "m1", "BRANDED_PDF_EXPORT", 2, "k3"), plans=_PLANS, now=just_before
    )
    assert rem_pro == 18


def test_non_active_subscription_uses_default_plan() -> None:
    sub = SubscriptionState("t1", "m1", "STUDENT_PRO", SubscriptionStatus.PAST_DUE, _NOW + timedelta(days=5))
    ledger = QuotaLedgerState(subscriptions=((("t1", "m1"), sub),))
    _, _, rem = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t1", "m1", "AI_TUTOR_MESSAGE", 1, "k"), plans=_PLANS, now=_NOW
    )
    assert rem == 4


def test_usage_idempotency_key_is_scoped_per_tenant() -> None:
    ledger = _active_ledger()
    ledger, a_charged, _ = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t1", "m1", "AI_TUTOR_MESSAGE", 1, "shared-key"), plans=_PLANS, now=_NOW
    )
    ledger, b_charged, b_rem = consume_quota_idempotent(
        ledger, UsageLedgerEntry("t2", "mx", "AI_TUTOR_MESSAGE", 1, "shared-key"), plans=_PLANS, now=_NOW
    )
    assert a_charged is True
    assert b_charged is True and b_rem == 4


def test_invalid_usage_entries_are_rejected_with_billing_error() -> None:
    ledger = QuotaLedgerState()
    res = "AI_TUTOR_MESSAGE"
    bad_entries = [
        (UsageLedgerEntry("t1", "m1", res, 0, "k"), "INVALID_USAGE_DELTA"),
        (UsageLedgerEntry("t1", "m1", res, -3, "k"), "INVALID_USAGE_DELTA"),
        (UsageLedgerEntry("t1", "m1", res, True, "k"), "INVALID_USAGE_DELTA"),  # type: ignore[arg-type]
        (UsageLedgerEntry("t1", "m1", res, 1.5, "k"), "INVALID_USAGE_DELTA"),  # type: ignore[arg-type]
        (UsageLedgerEntry("t1", "m1", res, 1, ""), "MISSING_IDEMPOTENCY_KEY"),
        (UsageLedgerEntry("t1", "m1", res, 1, "   "), "MISSING_IDEMPOTENCY_KEY"),
        (UsageLedgerEntry("", "m1", res, 1, "k"), "MISSING_USAGE_SUBJECT"),
    ]
    for entry, code in bad_entries:
        with pytest.raises(BillingError) as err:
            consume_quota_idempotent(ledger, entry, plans=_PLANS, now=_NOW)
        assert err.value.code == code


def test_unknown_resource_has_zero_quota_and_unknown_default_plan_fails_closed() -> None:
    with pytest.raises(BillingError) as err:
        consume_quota_idempotent(
            QuotaLedgerState(), UsageLedgerEntry("t1", "m1", "NOT_A_RESOURCE", 1, "k"), plans=_PLANS, now=_NOW
        )
    assert err.value.code == "QUOTA_EXCEEDED"
    entry = UsageLedgerEntry("t1", "m1", "AI_TUTOR_MESSAGE", 1, "k")
    with pytest.raises(BillingError) as err2:
        consume_quota_idempotent(QuotaLedgerState(), entry, plans=_PLANS, now=_NOW, default_plan_code="NOPE")
    assert err2.value.code == "UNKNOWN_SUBSCRIPTION_PLAN"
    with pytest.raises(BillingError) as err3:
        consume_quota_idempotent(QuotaLedgerState(), entry, plans=_PLANS, now=datetime(2026, 11, 13))
    assert err3.value.code == "CLOCK_NOT_AWARE"
