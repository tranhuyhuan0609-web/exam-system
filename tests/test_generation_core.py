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
from app.modules.generation.public import (
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
        owner_tenant_id="tenant-a",
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
        owner_tenant_id="tenant-a",
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


def _req(anchors=None, **overrides):
    kwargs = dict(
        request_id="req-x",
        tenant_id="tenant-a",
        cell=MatrixGapCell("MATH.PROB.BAYES", "VD", QuestionItemType.SHORT_NUMERIC, missing_count=3),
        anchors=anchors if anchors is not None else (_valid_anchor(),),
        prompt_version="e03-v1",
    )
    kwargs.update(overrides)
    return create_gap_fill_request(**kwargs)


def test_anchor_owned_by_another_tenant_is_rejected() -> None:
    # Regression: anchor ownership was never compared with request.tenant_id.
    foreign = SourceAnchor(
        "anc-foreign", "dv-9", "text", PublicationStatus.PUBLISHED, RightsStatus.OWNED, owner_tenant_id="tenant-b"
    )
    with pytest.raises(GenerationPolicyError, match="does not belong"):
        _req(anchors=(_valid_anchor(), foreign))


def test_system_public_anchor_with_no_owner_is_usable_by_any_tenant() -> None:
    public = SourceAnchor(
        "anc-pub", "dv-9", "text", PublicationStatus.PUBLISHED, RightsStatus.LICENSED, owner_tenant_id=None
    )
    assert _req(anchors=(public,), tenant_id="tenant-z").tenant_id == "tenant-z"


def test_owner_tenant_id_is_required_on_anchors() -> None:
    with pytest.raises(TypeError):
        SourceAnchor("a", "d", "t", PublicationStatus.PUBLISHED, RightsStatus.OWNED)  # type: ignore[call-arg]


@pytest.mark.parametrize("rights", [RightsStatus.PENDING_REVIEW, RightsStatus.RESTRICTED, RightsStatus.TAKEDOWN])
def test_non_permissive_rights_block_generation(rights: RightsStatus) -> None:
    anchor = SourceAnchor("a", "d", "text", PublicationStatus.PUBLISHED, rights, owner_tenant_id="tenant-a")
    with pytest.raises(GenerationPolicyError, match="rights"):
        _req(anchors=(anchor,))


@pytest.mark.parametrize("count", [0, -3, True, 2.5])
def test_missing_count_must_be_a_positive_integer(count: object) -> None:
    cell = MatrixGapCell("N", "VD", QuestionItemType.SHORT_NUMERIC, missing_count=count)  # type: ignore[arg-type]
    with pytest.raises(GenerationPolicyError):
        _req(cell=cell)


def test_requested_count_is_capped_and_cap_must_be_positive() -> None:
    big = MatrixGapCell("N", "VD", QuestionItemType.SHORT_NUMERIC, missing_count=500)
    assert _req(cell=big).requested_count == 20
    assert _req(cell=big, max_per_request=5).requested_count == 5
    for bad in (0, -1, True):
        with pytest.raises(GenerationPolicyError):
            _req(cell=big, max_per_request=bad)


@pytest.mark.parametrize("field", ["request_id", "tenant_id", "prompt_version"])
def test_blank_identifiers_and_empty_excerpt_are_rejected(field: str) -> None:
    with pytest.raises(GenerationPolicyError):
        _req(**{field: "   "})
    blank = SourceAnchor("a", "d", "   ", PublicationStatus.PUBLISHED, RightsStatus.OWNED, owner_tenant_id="tenant-a")
    with pytest.raises(GenerationPolicyError, match="excerpt"):
        _req(anchors=(blank,))


def test_candidate_must_cite_known_anchors_and_nonblank_stem() -> None:
    req = _req()
    for cited in ((), ("unknown",), ("anc-1", "unknown")):
        with pytest.raises(GenerationPolicyError):
            register_generated_candidate(req, candidate_id="c1", stem="Q", used_anchor_ids=cited)
    for cid, stem in (("", "Q"), ("c1", "  ")):
        with pytest.raises(GenerationPolicyError):
            register_generated_candidate(req, candidate_id=cid, stem=stem, used_anchor_ids=("anc-1",))
    cand = register_generated_candidate(req, candidate_id="c1", stem="Q", used_anchor_ids=("anc-1", "anc-1"))
    assert cand.anchor_ids == ("anc-1",)
    assert cand.tenant_id == req.tenant_id


def test_unchecked_or_rejected_verification_cannot_enter_review_queue() -> None:
    cand = register_generated_candidate(_req(), candidate_id="c1", stem="Q", used_anchor_ids=("anc-1",))
    for status in (VerificationStatus.UNCHECKED, "WEIRD", None):
        with pytest.raises(GenerationPolicyError):
            record_sympy_verification_and_queue_review(cand, verification_status=status)  # type: ignore[arg-type]


def test_disputed_candidate_is_queued_but_can_never_be_published() -> None:
    cand = register_generated_candidate(_req(), candidate_id="c1", stem="Q", used_anchor_ids=("anc-1",))
    queued = record_sympy_verification_and_queue_review(cand, verification_status=VerificationStatus.DISPUTED)
    with pytest.raises(GenerationPolicyError, match="C02"):
        approve_and_publish_ai_candidate(queued, review_decision_id="rev-1", human_reviewer_id="reviewer-1")


@pytest.mark.parametrize("reviewer", ["", "  ", "ai", "System", "BOT"])
def test_publish_requires_a_human_reviewer(reviewer: str) -> None:
    cand = register_generated_candidate(_req(), candidate_id="c1", stem="Q", used_anchor_ids=("anc-1",))
    queued = record_sympy_verification_and_queue_review(cand, verification_status=VerificationStatus.VERIFIED)
    with pytest.raises(GenerationPolicyError):
        approve_and_publish_ai_candidate(queued, review_decision_id="rev-1", human_reviewer_id=reviewer)
    with pytest.raises(GenerationPolicyError):
        approve_and_publish_ai_candidate(queued, review_decision_id="  ", human_reviewer_id="reviewer-1")
