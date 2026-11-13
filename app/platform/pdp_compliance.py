"""Vietnam PDP Law (91/2025/QH15 & ND 13/2023/ND-CP) & GATE-09 compliance helpers.

Pure kernel helpers in ``app.platform`` covering:
- Under-16 parental / school B2B consent enforcement (F04),
- KMS / Vault URI reference validation blocking accidental plaintext secret storage,
- Crypto-shredding erasure verification preserving immutable audit & ledger records.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

MIN_ADULT_CONSENT_AGE = 16

_VAULT_URI_RE = re.compile(r"^(vault|kms)://[a-zA-Z0-9._-]+/[a-zA-Z0-9._/-]{3,200}$")
_PLAINTEXT_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"BEGIN\s+(?:RSA|EC|OPENSSH\s+)?PRIVATE\s+KEY", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"^[0-9a-fA-F]{64}$"),
)


class PdpComplianceError(ValueError):
    """Raised when a secret reference or PDP compliance input violates policy."""


@dataclass(frozen=True)
class MinorConsentDecision:
    age_years: int
    is_minor_under_16: bool
    sensitive_profile_allowed: bool
    reason_code: str


@dataclass(frozen=True)
class ErasureComplianceResult:
    compliant: bool
    certificate_sha256: str
    blocking_reasons: tuple[str, ...]


def validate_vault_secret_ref(ref: str) -> str:
    """Validate that stored KMS/Vault key references are URIs, never plaintext secrets."""
    cleaned = ref.strip()
    if any(pat.search(cleaned) for pat in _PLAINTEXT_SECRET_PATTERNS):
        raise PdpComplianceError("plaintext cryptographic secret rejected; store a vault:// or kms:// URI")
    if not _VAULT_URI_RE.match(cleaned) or ".." in cleaned:
        raise PdpComplianceError(f"invalid vault/kms secret reference URI: {ref!r}")
    return cleaned


def compute_exact_age_years(birth_date: date, reference_date: date) -> int:
    if reference_date < birth_date:
        raise PdpComplianceError("reference_date must not precede birth_date")
    years = reference_date.year - birth_date.year
    if (reference_date.month, reference_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def evaluate_minor_consent(
    *,
    birth_date: date,
    reference_date: date,
    consent_granted: bool,
    consent_revoked: bool,
    consent_channel: str | None = None,
) -> MinorConsentDecision:
    """Enforce F04 & Vietnam Law 91/2025/QH15: under-16 requires active parental/B2B consent."""
    age = compute_exact_age_years(birth_date, reference_date)
    is_minor = age < MIN_ADULT_CONSENT_AGE
    if not is_minor:
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=False,
            sensitive_profile_allowed=True,
            reason_code="ADULT_OR_16_PLUS",
        )

    valid_channel = (consent_channel or "").strip().upper() in {"PARENT_GUARDIAN", "SCHOOL_B2B"}
    if consent_granted and not consent_revoked and valid_channel:
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=True,
            sensitive_profile_allowed=True,
            reason_code="MINOR_CONSENT_VERIFIED",
        )
    if consent_revoked:
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=True,
            sensitive_profile_allowed=False,
            reason_code="PARENTAL_CONSENT_REVOKED",
        )
    return MinorConsentDecision(
        age_years=age,
        is_minor_under_16=True,
        sensitive_profile_allowed=False,
        reason_code="PARENTAL_CONSENT_REQUIRED",
    )


def evaluate_crypto_shredding_erasure(
    *,
    tenant_id: str,
    subject_id: str,
    vault_key_destroyed: bool,
    remaining_plaintext_pii_fields: tuple[str, ...] | list[str],
    audit_trail_preserved: bool,
) -> ErasureComplianceResult:
    """Verify F04 crypto-shredding erasure invariants and emit a deterministic digest."""
    errors: list[str] = []
    if not vault_key_destroyed:
        errors.append("VAULT_ENCRYPTION_KEY_NOT_DESTROYED")
    if remaining_plaintext_pii_fields:
        joined = ",".join(sorted(remaining_plaintext_pii_fields))
        errors.append(f"PLAINTEXT_PII_REMAINS:{joined}")
    if not audit_trail_preserved:
        errors.append("IMMUTABLE_AUDIT_TRAIL_MISSING")

    canonical = (
        f"tenant={tenant_id.strip()}|subject={subject_id.strip()}"
        f"|key_destroyed={vault_key_destroyed}|audit={audit_trail_preserved}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ErasureComplianceResult(
        compliant=len(errors) == 0,
        certificate_sha256=digest,
        blocking_reasons=tuple(errors),
    )
