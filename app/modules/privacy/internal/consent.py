"""F04 parental consent rules (Law 91/2025/QH15, Decree 13/2023): pure functions, no database.

Minority follows the database exactly: `is_user_minor(birth_year)` in chapter 4 compares calendar years
(`EXTRACT(YEAR FROM today) - birth_year < 16`), so `v_users_status.is_minor` and these functions never
disagree. A learner whose birth year is unknown is treated as an adult by that function; the API asks
for the birth year of every learner it creates (see router).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MINOR_AGE_LIMIT = 16
VERIFICATION_METHODS = ("OTP_EMAIL", "SIGNED_SCHOOL_AGREEMENT")
GUARDIAN_RELATIONSHIPS = ("PARENT", "LEGAL_GUARDIAN", "SCHOOL_CONTRACT")


class ParentalConsentRequiredError(PermissionError):
    """Sensitive personal data of a learner under 16 without an active consent."""

    code = "PARENT_CONSENT_REQUIRED"


@dataclass(frozen=True, slots=True)
class ConsentState:
    is_minor: bool
    has_active_consent: bool

    @property
    def may_store_sensitive_profile(self) -> bool:
        return not self.is_minor or self.has_active_consent


def is_user_minor(birth_year: int | None, today: date) -> bool:
    """Same rule as the SQL function `is_user_minor(p_birth_year, p_ref_date)`."""
    return birth_year is not None and (today.year - birth_year) < MINOR_AGE_LIMIT


def initial_user_status(birth_year: int | None, today: date) -> str:
    """A new minor waits for consent; everyone else starts active."""
    return "PENDING_PARENTAL_CONSENT" if is_user_minor(birth_year, today) else "ACTIVE"


def require_sensitive_profile_allowed(state: ConsentState) -> None:
    """Raise unless sensitive personal data (name, phone, learning profile) may be stored."""
    if not state.may_store_sensitive_profile:
        raise ParentalConsentRequiredError(
            "learners under 16 need an active parental or school consent before personal data is stored"
        )


def validate_consent_evidence(guardian_relationship: str, verification_method: str) -> None:
    """Check the enumerations the DDL and the module specification fix."""
    if guardian_relationship not in GUARDIAN_RELATIONSHIPS:
        raise ValueError(f"guardian_relationship must be one of {', '.join(GUARDIAN_RELATIONSHIPS)}")
    if verification_method not in VERIFICATION_METHODS:
        raise ValueError(f"verification_method must be one of {', '.join(VERIFICATION_METHODS)}")
