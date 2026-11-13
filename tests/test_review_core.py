"""Pure unit tests for A09 Review/Rights Publish Gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.review.public import (
    PUBLISHABLE_RIGHTS_STATUSES,
    GovernancePolicyError,
    PublicationStatus,
    ReviewDecisionType,
    ReviewEntityType,
    ReviewSeverity,
    ReviewTaskState,
    ReviewTaskStatus,
    RightsStatus,
    VerificationStatus,
    assert_publish_allowed,
    cancel_review_task,
    claim_review_task,
    evaluate_publish_gate,
    submit_review_decision,
)


def test_enums_match_postgresql_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (
        VerificationStatus,
        RightsStatus,
        PublicationStatus,
        ReviewEntityType,
        ReviewSeverity,
        ReviewTaskStatus,
        ReviewDecisionType,
            ):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_fail_closed_publish_gate_blocks_pending_restricted_and_takedown_even_when_verified() -> None:
    # A09 core rule: VERIFIED alone is never enough if rights are PENDING_REVIEW, RESTRICTED, or TAKEDOWN
    for blocked_rights in (RightsStatus.PENDING_REVIEW, RightsStatus.RESTRICTED, RightsStatus.TAKEDOWN):
        decision = evaluate_publish_gate(
            verification_status=VerificationStatus.VERIFIED,
            rights_status=blocked_rights,
            publication_status=PublicationStatus.READY,
            current_version_id="ver-123",
        )
        assert decision.allowed_publish is False
        assert decision.allowed_branded_pdf is False
        with pytest.raises(GovernancePolicyError) as exc:
            assert_publish_allowed(
                verification_status=VerificationStatus.VERIFIED,
                rights_status=blocked_rights,
                publication_status=PublicationStatus.READY,
                current_version_id="ver-123",
            )
        assert exc.value.code in {"PUBLISH_RIGHTS_BLOCKED", "PUBLISH_TAKEDOWN_ACTIVE"}


def test_publish_gate_checks_verification_and_version_presence() -> None:
    # Unverified or missing version is blocked even with OWNED rights
    for bad_verif in (
        VerificationStatus.UNCHECKED,
        VerificationStatus.AUTO_VERIFIED,
        VerificationStatus.DISPUTED,
        VerificationStatus.REJECTED,
    ):
        dec = evaluate_publish_gate(
            verification_status=bad_verif,
            rights_status=RightsStatus.OWNED,
            publication_status=PublicationStatus.READY,
            current_version_id="ver-1",
        )
        assert dec.allowed_publish is False
        assert "PUBLISH_NOT_VERIFIED" in dec.blocking_codes

    missing_ver = evaluate_publish_gate(
        verification_status=VerificationStatus.VERIFIED,
        rights_status=RightsStatus.OWNED,
        publication_status=PublicationStatus.READY,
        current_version_id=None,
    )
    assert missing_ver.allowed_publish is False
    assert "PUBLISH_MISSING_VERSION" in missing_ver.blocking_codes

    # LICENSED can publish but cannot rebrand PDF; OWNED & ALLOWED_DERIVATIVE allow both
    licensed = assert_publish_allowed(
        verification_status=VerificationStatus.VERIFIED,
        rights_status=RightsStatus.LICENSED,
        publication_status=PublicationStatus.READY,
        current_version_id="ver-1",
    )
    assert licensed.allowed_publish is True
    assert licensed.allowed_branded_pdf is False

    for brandable in (RightsStatus.OWNED, RightsStatus.ALLOWED_DERIVATIVE):
        assert brandable in PUBLISHABLE_RIGHTS_STATUSES
        ok = assert_publish_allowed(
            verification_status=VerificationStatus.VERIFIED,
            rights_status=brandable,
            publication_status=PublicationStatus.READY,
            current_version_id="ver-1",
        )
        assert ok.allowed_publish is True
        assert ok.allowed_branded_pdf is True


def test_review_task_workflow_and_decision_idempotency() -> None:
    task = ReviewTaskState(
        task_id="rt-1",
        tenant_id="tenant-1",
        entity_type=ReviewEntityType.QUESTION_VERSION,
        entity_id="qv-1",
        queue_name="sympy-dispute",
        severity=ReviewSeverity.HIGH,
    )
    claimed = claim_review_task(task, reviewer_id="rev-alice")
    assert claimed.status == ReviewTaskStatus.IN_REVIEW
    assert claim_review_task(claimed, reviewer_id="rev-alice") == claimed

    with pytest.raises(GovernancePolicyError) as empty_reason:
        submit_review_decision(
            claimed,
            decision_id="dec-1",
            reviewer_id="rev-alice",
            decision=ReviewDecisionType.APPROVE,
            reason="   ",
        )
    assert empty_reason.value.code == "MISSING_DECISION_REASON"

    resolved = submit_review_decision(
        claimed,
        decision_id="dec-1",
        reviewer_id="rev-alice",
        decision=ReviewDecisionType.APPROVE,
        reason="Verified against SymPy and official rubric",
    )
    assert resolved.status == ReviewTaskStatus.RESOLVED
    assert resolved.decisions[0].resulting_verification_status == VerificationStatus.VERIFIED
    # Idempotent replay with same decision_id
    assert (
        submit_review_decision(
            resolved,
            decision_id="dec-1",
            reviewer_id="rev-alice",
            decision=ReviewDecisionType.APPROVE,
            reason="duplicate delivery",
        )
        == resolved
    )

    with pytest.raises(GovernancePolicyError) as closed_err:
        cancel_review_task(resolved)
    assert closed_err.value.code == "REVIEW_TASK_CLOSED"
