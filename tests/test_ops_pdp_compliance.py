"""Tests for Vietnam PDP Law consent, Vault ref, and crypto-shredding compliance (P60)."""

from __future__ import annotations

from datetime import date

import pytest

from app.ops.pdp_compliance import (
    PdpComplianceError,
    evaluate_crypto_shredding_erasure,
    evaluate_minor_consent,
    validate_vault_secret_ref,
)


def test_under_16_parental_and_b2b_consent_enforcement() -> None:
    ref = date(2026, 11, 13)
    # Born 2010-11-14 -> still 15 on 2026-11-13
    under_16_blocked = evaluate_minor_consent(
        birth_date=date(2010, 11, 14),
        reference_date=ref,
        consent_granted=False,
        consent_revoked=False,
    )
    assert under_16_blocked.is_minor_under_16 is True
    assert under_16_blocked.age_years == 15
    assert under_16_blocked.sensitive_profile_allowed is False
    assert under_16_blocked.reason_code == "PARENTAL_CONSENT_REQUIRED"

    under_16_ok = evaluate_minor_consent(
        birth_date=date(2010, 11, 14),
        reference_date=ref,
        consent_granted=True,
        consent_revoked=False,
        consent_channel="SCHOOL_B2B",
    )
    assert under_16_ok.sensitive_profile_allowed is True
    assert under_16_ok.reason_code == "MINOR_CONSENT_VERIFIED"

    # Born 2010-11-13 -> turns 16 today
    sixteen_ok = evaluate_minor_consent(
        birth_date=date(2010, 11, 13),
        reference_date=ref,
        consent_granted=False,
        consent_revoked=False,
    )
    assert sixteen_ok.age_years == 16
    assert sixteen_ok.is_minor_under_16 is False
    assert sixteen_ok.sensitive_profile_allowed is True


def test_vault_secret_ref_blocks_plaintext_keys() -> None:
    assert (
        validate_vault_secret_ref("vault://transit/keys/tenant-a-user-dek")
        == "vault://transit/keys/tenant-a-user-dek"
    )
    with pytest.raises(PdpComplianceError, match="plaintext"):
        validate_vault_secret_ref("sk-abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(PdpComplianceError, match="plaintext"):
        validate_vault_secret_ref("a" * 64)
    with pytest.raises(PdpComplianceError):
        validate_vault_secret_ref("vault://transit/../etc/passwd")


def test_crypto_shredding_erasure_verification() -> None:
    ok = evaluate_crypto_shredding_erasure(
        tenant_id="tenant-1",
        subject_id="user-99",
        vault_key_destroyed=True,
        remaining_plaintext_pii_fields=[],
        audit_trail_preserved=True,
    )
    assert ok.compliant is True
    assert len(ok.certificate_sha256) == 64

    bad = evaluate_crypto_shredding_erasure(
        tenant_id="tenant-1",
        subject_id="user-99",
        vault_key_destroyed=False,
        remaining_plaintext_pii_fields=["email", "phone"],
        audit_trail_preserved=False,
    )
    assert bad.compliant is False
    assert "VAULT_ENCRYPTION_KEY_NOT_DESTROYED" in bad.blocking_reasons
    assert "PLAINTEXT_PII_REMAINS:email,phone" in bad.blocking_reasons
    assert "IMMUTABLE_AUDIT_TRAIL_MISSING" in bad.blocking_reasons
