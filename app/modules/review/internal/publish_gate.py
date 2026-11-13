"""A09 hard publish gate mirroring PostgreSQL `chk_question_publish_gate` (fail-closed)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VerificationStatus(StrEnum):
    """Exact DDL CHECK values for `questions.verification_status`."""

    UNCHECKED = "UNCHECKED"
    AUTO_VERIFIED = "AUTO_VERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"
    REJECTED = "REJECTED"


class RightsStatus(StrEnum):
    """Exact DDL CHECK values for `sources.rights_status` and `questions.rights_status`."""

    PENDING_REVIEW = "PENDING_REVIEW"
    OWNED = "OWNED"
    LICENSED = "LICENSED"
    ALLOWED_DERIVATIVE = "ALLOWED_DERIVATIVE"
    RESTRICTED = "RESTRICTED"
    TAKEDOWN = "TAKEDOWN"


class PublicationStatus(StrEnum):
    """Exact DDL CHECK values for `questions.publication_status`."""

    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    TAKEN_DOWN = "TAKEN_DOWN"


PUBLISHABLE_RIGHTS_STATUSES: frozenset[RightsStatus] = frozenset(
    {
        RightsStatus.OWNED,
        RightsStatus.LICENSED,
        RightsStatus.ALLOWED_DERIVATIVE,
    }
)

BRANDING_ALLOWED_RIGHTS_STATUSES: frozenset[RightsStatus] = frozenset(
    {
        RightsStatus.OWNED,
        RightsStatus.ALLOWED_DERIVATIVE,
    }
)


class GovernancePolicyError(ValueError):
    """Domain policy violation raised with a deterministic machine-readable `code`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PublishGateDecision:
    """Result of evaluating whether an entity may transition to `PUBLISHED` or rebrand."""

    allowed_publish: bool
    allowed_branded_pdf: bool
    blocking_codes: tuple[str, ...]
    reasons: tuple[str, ...]


def evaluate_publish_gate(
    *,
    verification_status: VerificationStatus,
    rights_status: RightsStatus,
    publication_status: PublicationStatus,
    current_version_id: str | None,
) -> PublishGateDecision:
    """
    Evaluate both verification and copyright gates independently (A04 / A09).

    Mirrors DB CHECK `chk_question_publish_gate`:
    - `verification_status == 'VERIFIED'`
    - `rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')`
    - `current_version_id IS NOT NULL`
    - Cannot publish an entity already in `TAKEN_DOWN`.
    """
    codes: list[str] = []
    reasons: list[str] = []

    if publication_status == PublicationStatus.TAKEN_DOWN or rights_status == RightsStatus.TAKEDOWN:
        codes.append("PUBLISH_TAKEDOWN_ACTIVE")
        reasons.append("Entity is under active legal takedown and cannot be published")

    if verification_status != VerificationStatus.VERIFIED:
        codes.append("PUBLISH_NOT_VERIFIED")
        reasons.append(f"verification_status must be VERIFIED, got {verification_status.value}")

    if rights_status not in PUBLISHABLE_RIGHTS_STATUSES:
        codes.append("PUBLISH_RIGHTS_BLOCKED")
        reasons.append(f"rights_status={rights_status.value} does not permit publication")

    if not current_version_id:
        codes.append("PUBLISH_MISSING_VERSION")
        reasons.append("current_version_id must be present before publication")

    allowed_publish = len(codes) == 0
    allowed_branded = allowed_publish and rights_status in BRANDING_ALLOWED_RIGHTS_STATUSES
    return PublishGateDecision(
        allowed_publish=allowed_publish,
        allowed_branded_pdf=allowed_branded,
        blocking_codes=tuple(dict.fromkeys(codes)),
        reasons=tuple(reasons),
    )


def assert_publish_allowed(
    *,
    verification_status: VerificationStatus,
    rights_status: RightsStatus,
    publication_status: PublicationStatus,
    current_version_id: str | None,
) -> PublishGateDecision:
    """Enforce the A09 publish gate and raise `GovernancePolicyError` on any violation."""
    decision = evaluate_publish_gate(
        verification_status=verification_status,
        rights_status=rights_status,
        publication_status=publication_status,
        current_version_id=current_version_id,
    )
    if not decision.allowed_publish:
        raise GovernancePolicyError(decision.blocking_codes[0], "; ".join(decision.reasons))
    return decision
