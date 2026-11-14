"""E03 AI Question Generation (Matrix Gap Filler) core rules.

Grounded source anchors, mandatory AI_GENERATED provenance, and zero direct-to-PUBLISHED bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from app.contracts.enums import (
    AcquisitionMethod,
    PublicationStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)

ALLOWED_ANCHOR_RIGHTS: frozenset[RightsStatus] = frozenset(
    {RightsStatus.OWNED, RightsStatus.LICENSED, RightsStatus.ALLOWED_DERIVATIVE}
)


class GenerationStatus(StrEnum):
    REQUESTED = "REQUESTED"
    GENERATED = "GENERATED"
    SYMPY_VERIFIED = "SYMPY_VERIFIED"
    QUEUED_FOR_REVIEW = "QUEUED_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class GenerationPolicyError(ValueError):
    """Raised when grounded generation rules or human-review publish gates are violated."""


@dataclass(frozen=True)
class MatrixGapCell:
    taxonomy_node_id: str
    difficulty: str
    question_type: QuestionItemType
    missing_count: int


@dataclass(frozen=True)
class SourceAnchor:
    anchor_id: str
    document_version_id: str
    excerpt: str
    publication_status: PublicationStatus
    rights_status: RightsStatus
    # Owning tenant of the anchor; None means a SYSTEM_PUBLIC anchor. Required: no silent default.
    owner_tenant_id: str | None


@dataclass(frozen=True)
class GenerationRequest:
    request_id: str
    tenant_id: str
    cell: MatrixGapCell
    anchors: tuple[SourceAnchor, ...]
    prompt_version: str
    requested_count: int
    status: GenerationStatus = GenerationStatus.REQUESTED


@dataclass(frozen=True)
class GeneratedCandidate:
    candidate_id: str
    request_id: str
    tenant_id: str
    question_type: QuestionItemType
    taxonomy_node_id: str
    difficulty: str
    stem: str
    anchor_ids: tuple[str, ...]
    acquisition_method: AcquisitionMethod = AcquisitionMethod.AI_GENERATED
    verification_status: VerificationStatus = VerificationStatus.UNCHECKED
    publication_status: PublicationStatus = PublicationStatus.DRAFT
    status: GenerationStatus = GenerationStatus.GENERATED
    review_decision_id: str | None = None
    reviewer_id: str | None = None


def create_gap_fill_request(
    *,
    request_id: str,
    tenant_id: str,
    cell: MatrixGapCell,
    anchors: tuple[SourceAnchor, ...],
    prompt_version: str,
    max_per_request: int = 20,
) -> GenerationRequest:
    """Create a grounded E03 generation request backed only by PUBLISHED & rights-cleared anchors."""
    if not isinstance(request_id, str) or not isinstance(tenant_id, str):
        raise GenerationPolicyError("request_id and tenant_id must be strings")
    if not request_id.strip() or not tenant_id.strip():
        raise GenerationPolicyError("request_id and tenant_id are required")
    if isinstance(max_per_request, bool) or not isinstance(max_per_request, int) or max_per_request <= 0:
        raise GenerationPolicyError("max_per_request must be a positive integer")
    if isinstance(cell.missing_count, bool) or not isinstance(cell.missing_count, int) or cell.missing_count <= 0:
        raise GenerationPolicyError("missing_count must be a positive integer")
    if not anchors:
        raise GenerationPolicyError("at least one source_anchor is required for grounded generation")
    if not prompt_version.strip():
        raise GenerationPolicyError("prompt_version is required")

    for anchor in anchors:
        if anchor.owner_tenant_id is not None and anchor.owner_tenant_id != tenant_id.strip():
            # Never reveal whether a foreign anchor exists: same error as an unusable anchor.
            raise GenerationPolicyError(f"anchor {anchor.anchor_id!r} does not belong to tenant {tenant_id.strip()!r}")
        if anchor.publication_status != PublicationStatus.PUBLISHED:
            raise GenerationPolicyError(
                f"anchor {anchor.anchor_id!r} must be PUBLISHED (got {anchor.publication_status})"
            )
        if anchor.rights_status not in ALLOWED_ANCHOR_RIGHTS:
            raise GenerationPolicyError(f"anchor {anchor.anchor_id!r} has non-permissive rights {anchor.rights_status}")
        if not isinstance(anchor.excerpt, str) or not anchor.excerpt.strip():
            raise GenerationPolicyError(f"anchor {anchor.anchor_id!r} has empty excerpt")

    capped_count = min(cell.missing_count, max_per_request)
    return GenerationRequest(
        request_id=request_id.strip(),
        tenant_id=tenant_id.strip(),
        cell=cell,
        anchors=anchors,
        prompt_version=prompt_version.strip(),
        requested_count=capped_count,
    )


def register_generated_candidate(
    request: GenerationRequest,
    *,
    candidate_id: str,
    stem: str,
    used_anchor_ids: tuple[str, ...],
) -> GeneratedCandidate:
    """Register an AI-generated candidate forced into DRAFT + AI_GENERATED + UNCHECKED provenance."""
    if not candidate_id.strip() or not stem.strip():
        raise GenerationPolicyError("candidate_id and non-empty stem are required")
    valid_anchors = {a.anchor_id for a in request.anchors}
    if not used_anchor_ids or not set(used_anchor_ids).issubset(valid_anchors):
        raise GenerationPolicyError("candidate must cite valid anchor_ids from its generation request")

    return GeneratedCandidate(
        candidate_id=candidate_id.strip(),
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        question_type=request.cell.question_type,
        taxonomy_node_id=request.cell.taxonomy_node_id,
        difficulty=request.cell.difficulty,
        stem=stem.strip(),
        anchor_ids=tuple(dict.fromkeys(used_anchor_ids)),
        acquisition_method=AcquisitionMethod.AI_GENERATED,
        verification_status=VerificationStatus.UNCHECKED,
        publication_status=PublicationStatus.DRAFT,
        status=GenerationStatus.GENERATED,
    )


def record_sympy_verification_and_queue_review(
    candidate: GeneratedCandidate,
    *,
    verification_status: VerificationStatus,
) -> GeneratedCandidate:
    """Attach C02 SymPy+LLM verification result and place AI candidate into A09 review queue."""
    if verification_status not in (
        VerificationStatus.VERIFIED,
        VerificationStatus.AUTO_VERIFIED,
        VerificationStatus.DISPUTED,
    ):
        raise GenerationPolicyError(
            f"cannot advance AI candidate to A09 queue with verification_status {verification_status}"
        )
    return replace(
        candidate,
        verification_status=verification_status,
        status=GenerationStatus.QUEUED_FOR_REVIEW,
        publication_status=PublicationStatus.DRAFT,
    )


def approve_and_publish_ai_candidate(
    candidate: GeneratedCandidate,
    *,
    review_decision_id: str,
    human_reviewer_id: str,
) -> GeneratedCandidate:
    """Promote an AI-generated candidate to PUBLISHED ONLY after C02 pass and human A09 approval."""
    if candidate.acquisition_method != AcquisitionMethod.AI_GENERATED:
        raise GenerationPolicyError("candidate must remain tagged AI_GENERATED")
    if candidate.verification_status not in (VerificationStatus.VERIFIED, VerificationStatus.AUTO_VERIFIED):
        raise GenerationPolicyError(
            f"C02 SymPy/LLM verification must pass before publish (got {candidate.verification_status})"
        )
    if candidate.status != GenerationStatus.QUEUED_FOR_REVIEW:
        raise GenerationPolicyError(
            f"candidate must pass through QUEUED_FOR_REVIEW before publishing (got {candidate.status})"
        )
    clean_decision = review_decision_id.strip()
    clean_reviewer = human_reviewer_id.strip()
    if not clean_decision or not clean_reviewer or clean_reviewer.upper() in {"AI", "SYSTEM", "BOT"}:
        raise GenerationPolicyError("explicit human A09 review_decision_id and reviewer_id are mandatory")

    return replace(
        candidate,
        status=GenerationStatus.APPROVED,
        publication_status=PublicationStatus.PUBLISHED,
        review_decision_id=clean_decision,
        reviewer_id=clean_reviewer,
    )
