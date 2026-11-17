"""A09 review service: the review queue, reviewer decisions and source rights records (tenant RLS).

The state machine lives in `review_workflow` (pure); this module loads a task under a row lock, runs the pure
transition, and stores its result: the task row, an append-only `review_decisions` row, and, for a question
candidate, the verification and candidate status the decision implies. A reviewer may decide an OPEN task
directly: the task is claimed for them first, in the same transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.public import record_audit_event
from app.modules.review.internal.models import ReviewDecision, ReviewTask, SourceRight
from app.modules.review.internal.publish_gate import GovernancePolicyError
from app.modules.review.internal.review_workflow import (
    ReviewDecisionType,
    ReviewEntityType,
    ReviewSeverity,
    ReviewTaskState,
    ReviewTaskStatus,
    claim_review_task,
    submit_review_decision,
)

# Candidate status a decision implies; REQUEST_REPROCESS and SPLIT_MERGE leave the candidate in the queue.
_CANDIDATE_STATUS = {ReviewDecisionType.APPROVE: "APPROVED", ReviewDecisionType.REJECT: "REJECTED"}
LICENSE_TYPES = frozenset(
    {"OWNED", "CC_BY", "CC_BY_SA", "CC_BY_NC", "PUBLIC_DOMAIN", "PUBLISHER_LICENSE", "SCHOOL_INTERNAL", "UNKNOWN"}
)


class ReviewNotFoundError(LookupError):
    """The task, entity or source does not exist or belongs to another tenant (same answer)."""


class ReviewConflictError(ValueError):
    """The task cannot take this action now; `code` is the governance code (e.g. REVIEW_TASK_CLOSED)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReviewTaskRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    queue_name: str
    severity: str
    status: str
    assigned_to: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewDecisionRecord:
    id: uuid.UUID
    review_task_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: str
    reason: str
    decided_at: datetime
    resulting_verification_status: str | None


@dataclass(frozen=True, slots=True)
class SourceRightRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    license_type: str
    allow_rebranding: bool
    allow_public_redistribution: bool
    evidence_asset_id: uuid.UUID | None
    verified_by: uuid.UUID | None
    verified_at: datetime | None


def _task(row: ReviewTask) -> ReviewTaskRecord:
    return ReviewTaskRecord(**{f: getattr(row, f) for f in ReviewTaskRecord.__dataclass_fields__})


def _right(row: SourceRight) -> SourceRightRecord:
    return SourceRightRecord(**{f: getattr(row, f) for f in SourceRightRecord.__dataclass_fields__})


async def create_review_task(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    queue_name: str,
    severity: str = "NORMAL",
) -> ReviewTaskRecord:
    """Queue an entity for human review. A question candidate must exist in the tenant; one OPEN task per entity."""
    kind = ReviewEntityType(entity_type)
    level = ReviewSeverity(severity)
    if not queue_name.strip():
        raise ValueError("queue_name must not be blank")
    if kind is ReviewEntityType.QUESTION_CANDIDATE:
        found = await session.scalar(text("SELECT 1 FROM question_candidates WHERE id = :i"), {"i": entity_id})
        if found is None:
            raise ReviewNotFoundError("question candidate not found")
    if kind is ReviewEntityType.QUESTION_VERSION:
        frozen = await session.scalar(
            text("SELECT is_immutable FROM question_versions WHERE id = :i AND tenant_id = :t"),
            {"i": entity_id, "t": tenant_id},
        )
        if frozen is None:
            raise ReviewNotFoundError("question version not found")
        if frozen:
            raise ReviewConflictError("VERSION_ALREADY_PUBLISHED", "a published version has nothing left to review")
    open_task = await session.scalar(
        select(ReviewTask.id).where(
            ReviewTask.entity_type == kind.value,
            ReviewTask.entity_id == entity_id,
            ReviewTask.status.in_(("OPEN", "IN_REVIEW")),
        )
    )
    if open_task is not None:
        raise ReviewConflictError("REVIEW_TASK_EXISTS", "this entity already has an open review task")
    row = (
        await session.execute(
            insert(ReviewTask)
            .values(
                tenant_id=tenant_id,
                entity_type=kind.value,
                entity_id=entity_id,
                queue_name=queue_name.strip(),
                severity=level.value,
            )
            .returning(ReviewTask)
        )
    ).scalar_one()
    return _task(row)


async def decide_review_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    decision: str,
    reason: str,
) -> tuple[ReviewTaskRecord, ReviewDecisionRecord]:
    """Claim (when OPEN) and resolve a task with the reviewer's decision, applying it to a question candidate."""
    kind = ReviewDecisionType(decision)
    row = await session.scalar(
        select(ReviewTask).where(ReviewTask.id == task_id).with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise ReviewNotFoundError("review task not found")
    state = ReviewTaskState(
        task_id=str(row.id),
        tenant_id=str(row.tenant_id),
        entity_type=ReviewEntityType(row.entity_type),
        entity_id=str(row.entity_id),
        queue_name=row.queue_name,
        severity=ReviewSeverity(row.severity),
        status=ReviewTaskStatus(row.status),
        assigned_to=None if row.assigned_to is None else str(row.assigned_to),
    )
    decision_id = str(uuid.uuid4())
    try:
        if state.status is ReviewTaskStatus.OPEN:
            state = claim_review_task(state, reviewer_id=str(reviewer_id))
        state = submit_review_decision(
            state, decision_id=decision_id, reviewer_id=str(reviewer_id), decision=kind, reason=reason
        )
    except GovernancePolicyError as exc:
        raise ReviewConflictError(exc.code, str(exc)) from exc
    recorded = state.decisions[-1]
    resulting = recorded.resulting_verification_status
    saved_task = (
        await session.execute(
            update(ReviewTask)
            .where(ReviewTask.id == row.id)
            .values(status=state.status.value, assigned_to=reviewer_id)
            .returning(ReviewTask)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    saved = (
        await session.execute(
            insert(ReviewDecision)
            .values(
                tenant_id=tenant_id,
                review_task_id=row.id,
                reviewer_id=reviewer_id,
                decision=kind.value,
                reason=recorded.reason,
            )
            .returning(ReviewDecision)
        )
    ).scalar_one()
    if state.entity_type is ReviewEntityType.QUESTION_CANDIDATE and resulting is not None:
        await session.execute(
            text(_UPDATE_CANDIDATE),
            {"i": row.entity_id, "verification_status": resulting.value, "cs": _CANDIDATE_STATUS.get(kind)},
        )
    if state.entity_type is ReviewEntityType.QUESTION_VERSION and resulting is not None:
        moved = await session.scalar(
            text(_UPDATE_QUESTION_OF_VERSION),
            {"vid": row.entity_id, "tid": tenant_id, "verification_status": resulting.value},
        )
        if moved is None:
            # Not applied: the whole decision rolls back with it, so a task never resolves without effect.
            raise ReviewConflictError(
                "QUESTION_NOT_REVIEWABLE",
                "the question is taken down, or this version is frozen or no longer the newest",
            )
    return _task(saved_task), ReviewDecisionRecord(
        id=saved.id,
        review_task_id=saved.review_task_id,
        reviewer_id=saved.reviewer_id,
        decision=saved.decision,
        reason=saved.reason,
        decided_at=saved.decided_at,
        resulting_verification_status=None if resulting is None else resulting.value,
    )


# A human verdict on a question version (A09) is the only thing that makes a C01 question VERIFIED. It applies to
# the newest, still unfrozen version of a question that is not taken down. A published question keeps VERIFIED
# whatever the verdict on its next draft (the database requires it); the verdict is still recorded and the publish
# route reads it back for that draft.
_UPDATE_QUESTION_OF_VERSION = (
    "UPDATE questions q SET verification_status = CASE WHEN q.publication_status = 'PUBLISHED' "
    "THEN q.verification_status ELSE :verification_status END FROM question_versions v "
    "WHERE v.id = :vid AND q.id = v.question_id AND q.tenant_id = :tid AND v.tenant_id = :tid "
    "AND q.publication_status <> 'TAKEN_DOWN' AND v.is_immutable IS FALSE "
    "AND v.version_no = (SELECT max(version_no) FROM question_versions WHERE question_id = q.id) "
    "RETURNING q.id"
)

_UPDATE_CANDIDATE = (
    "UPDATE question_candidates SET verification_status = :verification_status, "
    "candidate_status = COALESCE(:cs, candidate_status) WHERE id = :i"
)


async def create_rights_record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    source_id: uuid.UUID,
    license_type: str,
    allow_rebranding: bool,
    allow_public_redistribution: bool,
    evidence_asset_id: uuid.UUID | None,
    correlation_id: str,
) -> SourceRightRecord:
    """Record the licence evidence of a source (verified by the caller now); audited."""
    kind = license_type.strip().upper()
    if kind not in LICENSE_TYPES:
        raise ValueError(f"license_type must be one of {sorted(LICENSE_TYPES)}")
    if await session.scalar(text("SELECT 1 FROM sources WHERE id = :s"), {"s": source_id}) is None:
        raise ReviewNotFoundError("source not found")
    row = (
        await session.execute(
            insert(SourceRight)
            .values(
                tenant_id=tenant_id,
                source_id=source_id,
                license_type=kind,
                allow_rebranding=allow_rebranding,
                allow_public_redistribution=allow_public_redistribution,
                evidence_asset_id=evidence_asset_id,
                verified_by=actor_id,
                verified_at=datetime.now(UTC),
            )
            .returning(SourceRight)
        )
    ).scalar_one()
    await record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="SOURCE_RIGHTS_RECORDED",
        target_type="source_rights",
        target_id=row.id,
        reason=f"licence {kind} recorded for source {source_id}",
        before=None,
        after={
            "license_type": kind,
            "allow_rebranding": allow_rebranding,
            "allow_public_redistribution": allow_public_redistribution,
        },
        correlation_id=correlation_id,
    )
    return _right(row)
