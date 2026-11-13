"""Tests for E03 AI question generation (matrix gap filler) core rules."""

from __future__ import annotations

import pytest

from app.contracts.enums import (
    AcquisitionMethod,
    PublicationStatus,
    QuestionItemType,
    RightsStatus,
    VerificationStatus,
)
from app.modules.ai_generation.public import (
    GenerationPolicyError,
    GenerationStatus,
    MatrixGapCell,
    SourceAnchor,
    approve_and_publish_ai_candidate,
    create_gap_fill_request,
    record_sympy_verification_and_queue_review,
    register_generated_candidate,
)


def _valid_anchor() -> SourceAnchor:
    return SourceAnchor(
        anchor_id="anc-1",
        document_version_id="dv-1",
        excerpt="Cho định lý Bayes với hai biến cố xung khắc...",
        publication_status=PublicationStatus.PUBLISHED,
        rights_status=RightsStatus.OWNED,
    )


def test_blocks_ungrounded_unpublished_or_restricted_anchors() -> None:
    cell = MatrixGapCell("MATH.PROB.BAYES", "VD", QuestionItemType.SHORT_NUMERIC, missing_count=3)

    with pytest.raises(GenerationPolicyError, match="source_anchor"):
        create_gap_fill_request(
            request_id="req-0",
            tenant_id="tenant-a",
            cell=cell,
            anchors=(),
            prompt_version="e03-v1",
        )

    unpublished_anchor = SourceAnchor(
        anchor_id="anc-draft",
        document_version_id="dv-2",
        excerpt="draft text",
        publication_status=PublicationStatus.DRAFT,
        rights_status=RightsStatus.OWNED,
    )
    with pytest.raises(GenerationPolicyError, match="PUBLISHED"):
        create_gap_fill_request(
            request_id="req-1",
            tenant_id="tenant-a",
            cell=cell,
            anchors=(unpublished_anchor,),
            prompt_version="e03-v1",
        )


def test_ai_generated_candidate_cannot_bypass_c02_or_a09_human_review() -> None:
    cell = MatrixGapCell("MATH.PROB.BAYES", "VD", QuestionItemType.SINGLE_CHOICE, missing_count=2)
    req = create_gap_fill_request(
        request_id="req-2",
        tenant_id="tenant-a",
        cell=cell,
        anchors=(_valid_anchor(),),
        prompt_version="e03-v1",
    )
    cand = register_generated_candidate(
        req,
        candidate_id="cand-1",
        stem="Tính $P(A|B)$ biết ...",
        used_anchor_ids=("anc-1",),
    )
    assert cand.acquisition_method == AcquisitionMethod.AI_GENERATED
    assert cand.publication_status == PublicationStatus.DRAFT

    with pytest.raises(GenerationPolicyError, match="C02"):
        approve_and_publish_ai_candidate(cand, review_decision_id="rev-1", human_reviewer_id="teacher-1")

    queued = record_sympy_verification_and_queue_review(
        cand,
        verification_status=VerificationStatus.AUTO_VERIFIED,
    )
    assert queued.status == GenerationStatus.QUEUED_FOR_REVIEW
    assert queued.publication_status == PublicationStatus.DRAFT

    with pytest.raises(GenerationPolicyError, match="human"):
        approve_and_publish_ai_candidate(queued, review_decision_id="rev-1", human_reviewer_id="AI")

    published = approve_and_publish_ai_candidate(
        queued,
        review_decision_id="rev-1",
        human_reviewer_id="reviewer-42",
    )
    assert published.publication_status == PublicationStatus.PUBLISHED
    assert published.acquisition_method == AcquisitionMethod.AI_GENERATED
    assert published.review_decision_id == "rev-1"
