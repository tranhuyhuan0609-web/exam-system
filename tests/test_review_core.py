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
            reason="Verified against SymPy and official rubric",
        )
        == resolved
    )

    with pytest.raises(GovernancePolicyError) as closed_err:
        cancel_review_task(resolved)
    assert closed_err.value.code == "REVIEW_TASK_CLOSED"


def _task(**kw: object) -> ReviewTaskState:
    base: dict[str, object] = {
        "task_id": "rt-1",
        "tenant_id": "tenant-1",
        "entity_type": ReviewEntityType.QUESTION_VERSION,
        "entity_id": "qv-1",
        "queue_name": "q",
    }
    base.update(kw)
    return ReviewTaskState(**base)  # type: ignore[arg-type]


def _decide(state: ReviewTaskState, **kw: object) -> ReviewTaskState:
    args: dict[str, object] = {
        "decision_id": "dec-1",
        "reviewer_id": "alice",
        "decision": ReviewDecisionType.APPROVE,
        "reason": "ok",
    }
    args.update(kw)
    return submit_review_decision(state, **args)  # type: ignore[arg-type]


def test_second_reviewer_cannot_take_over_a_claimed_task() -> None:
    claimed = claim_review_task(_task(), reviewer_id="alice")
    with pytest.raises(GovernancePolicyError) as exc:
        claim_review_task(claimed, reviewer_id="bob")
    assert exc.value.code == "REVIEW_TASK_ALREADY_CLAIMED"
    assert claimed.assigned_to == "alice"


def test_only_the_assigned_reviewer_may_decide_and_task_must_be_claimed() -> None:
    open_task = _task()
    with pytest.raises(GovernancePolicyError) as unclaimed:
        _decide(open_task)
    assert unclaimed.value.code == "REVIEW_TASK_NOT_CLAIMED"

    claimed = claim_review_task(open_task, reviewer_id="alice")
    with pytest.raises(GovernancePolicyError) as other:
        _decide(claimed, reviewer_id="bob")
    assert other.value.code == "REVIEWER_NOT_ASSIGNED"
    assert claimed.status == ReviewTaskStatus.IN_REVIEW and claimed.decisions == ()


def test_replayed_decision_id_with_different_content_conflicts() -> None:
    resolved = _decide(claim_review_task(_task(), reviewer_id="alice"))
    assert _decide(resolved) == resolved
    for changed in (
        {"decision": ReviewDecisionType.REJECT},
        {"reason": "different reason"},
        {"reviewer_id": "bob"},
    ):
        with pytest.raises(GovernancePolicyError) as exc:
            _decide(resolved, **changed)
        assert exc.value.code == "DECISION_ID_CONFLICT"
    # whitespace around an identical reason is still the same decision
    assert _decide(resolved, reason="  ok  ") == resolved


def test_new_decision_id_on_resolved_task_is_rejected_as_closed() -> None:
    resolved = _decide(claim_review_task(_task(), reviewer_id="alice"))
    with pytest.raises(GovernancePolicyError) as exc:
        _decide(resolved, decision_id="dec-2")
    assert exc.value.code == "REVIEW_TASK_CLOSED"


def test_decision_inputs_fail_closed() -> None:
    claimed = claim_review_task(_task(), reviewer_id="alice")
    cases = [
        ({"decision_id": ""}, "MISSING_DECISION_ID"),
        ({"decision_id": "  "}, "MISSING_DECISION_ID"),
        ({"reviewer_id": ""}, "MISSING_REVIEWER_ID"),
        ({"reason": ""}, "MISSING_DECISION_REASON"),
        ({"reason": None}, "MISSING_DECISION_REASON"),
        ({"decision": "approve"}, "INVALID_DECISION"),
        ({"decision": "APPROVE"}, "INVALID_DECISION"),
        ({"decision": None}, "INVALID_DECISION"),
    ]
    for override, code in cases:
        with pytest.raises(GovernancePolicyError) as exc:
            _decide(claimed, **override)
        assert exc.value.code == code, override


def test_each_decision_maps_to_expected_verification_status() -> None:
    expected = {
        ReviewDecisionType.APPROVE: VerificationStatus.VERIFIED,
        ReviewDecisionType.REJECT: VerificationStatus.REJECTED,
        ReviewDecisionType.REQUEST_REPROCESS: VerificationStatus.DISPUTED,
        ReviewDecisionType.SPLIT_MERGE: None,
    }
    for decision, status in expected.items():
        resolved = _decide(claim_review_task(_task(), reviewer_id="alice"), decision=decision)
        assert resolved.decisions[0].resulting_verification_status == status


def test_claim_and_cancel_state_machine_edges() -> None:
    with pytest.raises(GovernancePolicyError) as blank:
        claim_review_task(_task(), reviewer_id=" ")
    assert blank.value.code == "MISSING_REVIEWER_ID"
    cancelled = cancel_review_task(_task())
    assert cancelled.status == ReviewTaskStatus.CANCELLED
    assert cancel_review_task(cancelled) == cancelled
    with pytest.raises(GovernancePolicyError) as claim_closed:
        claim_review_task(cancelled, reviewer_id="alice")
    assert claim_closed.value.code == "REVIEW_TASK_CLOSED"
    with pytest.raises(GovernancePolicyError) as decide_closed:
        _decide(cancelled)
    assert decide_closed.value.code == "REVIEW_TASK_CLOSED"
    in_review = claim_review_task(_task(), reviewer_id="alice")
    assert cancel_review_task(in_review).status == ReviewTaskStatus.CANCELLED
    weird = _task(status="PAUSED")
    for action in (lambda: claim_review_task(weird, reviewer_id="a"), lambda: cancel_review_task(weird)):
        with pytest.raises(GovernancePolicyError) as exc:
            action()
        assert exc.value.code == "REVIEW_TASK_STATUS_UNKNOWN"


def test_state_inputs_are_not_mutated() -> None:
    task = _task()
    claim_review_task(task, reviewer_id="alice")
    assert task.status == ReviewTaskStatus.OPEN and task.assigned_to is None


def test_publish_gate_fails_closed_for_unknown_values_and_blank_version() -> None:
    base = {
        "verification_status": VerificationStatus.VERIFIED,
        "rights_status": RightsStatus.OWNED,
        "publication_status": PublicationStatus.READY,
        "current_version_id": "v1",
    }
    assert evaluate_publish_gate(**base).allowed_publish is True
    for override in (
        {"verification_status": "verified"},
        {"verification_status": "UNKNOWN"},
        {"rights_status": "owned"},
        {"rights_status": "FREE_FOR_ALL"},
        {"current_version_id": ""},
        {"current_version_id": "   "},
        {"current_version_id": None},
        {"publication_status": PublicationStatus.TAKEN_DOWN},
    ):
        decision = evaluate_publish_gate(**{**base, **override})  # type: ignore[arg-type]
        assert decision.allowed_publish is False, override
        assert decision.allowed_branded_pdf is False
        assert decision.blocking_codes
    with pytest.raises(GovernancePolicyError) as exc:
        assert_publish_allowed(**{**base, "rights_status": RightsStatus.RESTRICTED})  # type: ignore[arg-type]
    assert exc.value.code == "PUBLISH_RIGHTS_BLOCKED"


def test_publish_gate_reports_every_blocking_code() -> None:
    decision = evaluate_publish_gate(
        verification_status=VerificationStatus.UNCHECKED,
        rights_status=RightsStatus.TAKEDOWN,
        publication_status=PublicationStatus.TAKEN_DOWN,
        current_version_id=None,
    )
    assert set(decision.blocking_codes) == {
        "PUBLISH_TAKEDOWN_ACTIVE",
        "PUBLISH_NOT_VERIFIED",
        "PUBLISH_RIGHTS_BLOCKED",
        "PUBLISH_MISSING_VERSION",
    }
