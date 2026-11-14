"""Vietnam PDP Law (91/2025/QH15 & ND 13/2023/ND-CP) & GATE-09 compliance helpers.

Pure offline compliance helpers in ``app.ops`` covering:
- Under-16 parental / school B2B consent enforcement (F04),
- KMS / Vault URI reference validation blocking accidental plaintext secret storage,
- Crypto-shredding erasure verification preserving immutable audit & ledger records.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime

MIN_ADULT_CONSENT_AGE = 16
MAX_PLAUSIBLE_AGE = 130

_VAULT_URI_RE = re.compile(r"^(vault|kms)://([a-zA-Z0-9._-]+)/([a-zA-Z0-9._/-]{3,200})$")
_PLAINTEXT_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"BEGIN\s+(?:RSA|EC|OPENSSH\s+)?PRIVATE\s+KEY", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"^[0-9a-fA-F]{32,}$"),
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
    if not isinstance(ref, str):
        raise PdpComplianceError("secret reference must be a string")
    cleaned = ref.strip()
    if any(pat.search(cleaned) for pat in _PLAINTEXT_SECRET_PATTERNS):
        raise PdpComplianceError("plaintext cryptographic secret rejected; store a vault:// or kms:// URI")
    match = _VAULT_URI_RE.match(cleaned)
    if not match:
        raise PdpComplianceError("invalid vault/kms secret reference URI")
    mount, path = match.group(2), match.group(3)
    segments = [mount, *path.split("/")]
    if any(seg in {"", ".", ".."} for seg in segments):
        raise PdpComplianceError("vault/kms URI must not contain empty or dot path segments")
    if any(any(pat.search(seg) for pat in _PLAINTEXT_SECRET_PATTERNS) for seg in segments):
        raise PdpComplianceError("plaintext cryptographic secret embedded in URI path rejected")
    return cleaned


def _require_calendar_date(value: object, name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PdpComplianceError(f"{name} must be a calendar date (not a datetime)")
    return value


def compute_exact_age_years(birth_date: date, reference_date: date) -> int:
    b_date = _require_calendar_date(birth_date, "birth_date")
    r_date = _require_calendar_date(reference_date, "reference_date")
    if r_date < b_date:
        raise PdpComplianceError("reference_date must not precede birth_date")
    years = r_date.year - b_date.year
    if (r_date.month, r_date.day) < (b_date.month, b_date.day):
        years -= 1
    if years > MAX_PLAUSIBLE_AGE:
        raise PdpComplianceError(f"computed age {years} exceeds plausible limit ({MAX_PLAUSIBLE_AGE})")
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
    if not isinstance(consent_granted, bool) or not isinstance(consent_revoked, bool):
        raise PdpComplianceError("consent_granted and consent_revoked must be booleans")
    age = compute_exact_age_years(birth_date, reference_date)
    is_minor = age < MIN_ADULT_CONSENT_AGE
    if not is_minor:
        if consent_revoked:
            return MinorConsentDecision(
                age_years=age,
                is_minor_under_16=False,
                sensitive_profile_allowed=False,
                reason_code="CONSENT_REVOKED",
            )
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=False,
            sensitive_profile_allowed=True,
            reason_code="ADULT_OR_16_PLUS",
        )

    valid_channel = isinstance(consent_channel, str) and consent_channel.strip().upper() in {
        "PARENT_GUARDIAN",
        "SCHOOL_B2B",
    }
    if consent_revoked:
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=True,
            sensitive_profile_allowed=False,
            reason_code="PARENTAL_CONSENT_REVOKED",
        )
    if consent_granted and valid_channel:
        return MinorConsentDecision(
            age_years=age,
            is_minor_under_16=True,
            sensitive_profile_allowed=True,
            reason_code="MINOR_CONSENT_VERIFIED",
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
    remaining_plaintext_pii_fields: Collection[str],
    audit_trail_preserved: bool,
) -> ErasureComplianceResult:
    """Verify F04 crypto-shredding erasure invariants and emit a deterministic digest."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise PdpComplianceError("tenant_id must be a non-empty string")
    if not isinstance(subject_id, str) or not subject_id.strip():
        raise PdpComplianceError("subject_id must be a non-empty string")
    if not isinstance(vault_key_destroyed, bool) or not isinstance(audit_trail_preserved, bool):
        raise PdpComplianceError("vault_key_destroyed and audit_trail_preserved must be booleans")
    if isinstance(remaining_plaintext_pii_fields, (str, bytes)) or not isinstance(
        remaining_plaintext_pii_fields, Collection
    ):
        raise PdpComplianceError("remaining_plaintext_pii_fields must be a collection of field names")

    cleaned_fields: list[str] = []
    for item in remaining_plaintext_pii_fields:
        if not isinstance(item, str) or not item.strip():
            raise PdpComplianceError("each remaining PII field name must be a non-empty string")
        cleaned_fields.append(item.strip())

    errors: list[str] = []
    if not vault_key_destroyed:
        errors.append("VAULT_ENCRYPTION_KEY_NOT_DESTROYED")
    if cleaned_fields:
        joined = ",".join(sorted(set(cleaned_fields)))
        errors.append(f"PLAINTEXT_PII_REMAINS:{joined}")
    if not audit_trail_preserved:
        errors.append("IMMUTABLE_AUDIT_TRAIL_MISSING")

    canonical = (
        f"tenant={tenant_id.strip()}|subject={subject_id.strip()}"
        f"|key_destroyed={vault_key_destroyed}|remaining={','.join(sorted(set(cleaned_fields)))}"
        f"|audit={audit_trail_preserved}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ErasureComplianceResult(
        compliant=len(errors) == 0,
        certificate_sha256=digest,
        blocking_reasons=tuple(errors),
    )
