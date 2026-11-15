"""Database service layer for F02 Assessment Assignments, Windows & Answer Reveal."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.event_catalog import GradeReleasedPayload, build_catalog_envelope
from app.modules.assignments.internal.models import AssessmentAssignment
from app.modules.assignments.internal.schedule import (
    AssignmentSchedule,
    RevealPolicy,
    assert_can_start_assignment,
    can_reveal_assignment_answers,
)
from app.modules.classes.public import (
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    ClassroomPolicyError,
    filter_active_assignment_recipients,
    get_active_class_recipients,
    get_class_member,
    is_class_participant,
    list_classes,
    to_class_member_state,
)
from app.modules.outbox.public import enqueue


class AssignmentValidationError(ValueError):
    """Raised when assignment parameters violate schedule or scope invariants."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AssignmentNotFoundError(LookupError):
    """Raised when an assignment or its referenced target cannot be found."""


@dataclass(frozen=True, slots=True)
class AssignmentView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    assessment_version_id: uuid.UUID
    class_id: uuid.UUID | None
    group_id: uuid.UUID | None
    assigned_by: uuid.UUID
    available_from: datetime
    due_at: datetime | None
    max_attempts: int
    reveal_policy: str
    manually_released: bool
    created_at: datetime
    active_recipient_membership_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class AssignmentSummaryView:
    """An assignment with the paper it hands out, for list screens."""

    id: uuid.UUID
    class_id: uuid.UUID | None
    group_id: uuid.UUID | None
    assessment_version_id: uuid.UUID
    assessment_id: uuid.UUID | None
    assessment_title: str | None
    available_from: datetime
    due_at: datetime | None
    max_attempts: int
    reveal_policy: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StartCheckResult:
    assignment_id: uuid.UUID
    allowed: bool
    attempts_used: int
    max_attempts: int
    available_from: datetime
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class RevealStatusResult:
    assignment_id: uuid.UUID
    reveal_policy: str
    learner_submitted: bool
    manually_released: bool
    can_reveal_answers: bool
    due_at: datetime | None


async def _has_priv(session: AsyncSession, table_name: str, privilege: str) -> bool:
    result = await session.execute(
        text(
            "SELECT to_regclass('public.' || quote_ident(:tbl)) IS NOT NULL "
            "AND has_table_privilege(current_user, 'public.' || quote_ident(:tbl), :priv)"
        ),
        {"tbl": table_name, "priv": privilege},
    )
    return bool(result.scalar())


def _normalize_reveal_policy(value: RevealPolicy | str) -> RevealPolicy:
    if isinstance(value, RevealPolicy):
        return value
    try:
        return RevealPolicy(value.strip().upper())
    except ValueError as exc:
        raise AssignmentValidationError("INVALID_REVEAL_POLICY", f"unsupported reveal_policy: {value!r}") from exc


def _to_schedule(row: AssessmentAssignment, *, manually_released: bool) -> AssignmentSchedule:
    return AssignmentSchedule(
        assignment_id=str(row.id),
        available_from=row.available_from,
        due_at=row.due_at,
        max_attempts=row.max_attempts,
        reveal_policy=RevealPolicy(row.reveal_policy),
        manually_released=manually_released,
    )


async def _is_manually_released(session: AsyncSession, *, tenant_id: uuid.UUID, assignment_id: uuid.UUID) -> bool:
    if not await _has_priv(session, "tenants", "SELECT"):
        return False
    val = (
        await session.execute(
            text(
                "SELECT (settings_json->'manual_released_assignments'->>:aid)::boolean "
                "FROM tenants WHERE id = :tid"
            ),
            {"tid": tenant_id, "aid": str(assignment_id)},
        )
    ).scalar_one_or_none()
    return bool(val)


async def _set_manually_released(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    released: bool,
) -> None:
    if not await _has_priv(session, "tenants", "UPDATE"):
        raise AssignmentValidationError("PRIVILEGE_REQUIRED", "current role cannot update manual release flags")
    flag_json = json.dumps(bool(released))
    await session.execute(
        text(
            "UPDATE tenants SET settings_json = jsonb_set("
            "jsonb_set(COALESCE(settings_json, '{}'::jsonb), '{manual_released_assignments}', "
            "COALESCE(settings_json->'manual_released_assignments', '{}'::jsonb), true), "
            "ARRAY['manual_released_assignments', :aid], CAST(:val AS jsonb), true), "
            "updated_at = NOW() WHERE id = :tid"
        ),
        {"tid": tenant_id, "aid": str(assignment_id), "val": flag_json},
    )


async def _resolve_group_active_recipients(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
) -> tuple[ClassMemberState, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT cm.class_id, cm.tenant_id, cm.membership_id, cm.role_in_class, cm.status, cm.left_at "
                "FROM class_members cm "
                "JOIN classes c ON c.id = cm.class_id AND c.tenant_id = cm.tenant_id "
                "WHERE cm.tenant_id = :tid AND c.group_id = :gid"
            ),
            {"tid": tenant_id, "gid": group_id},
        )
    ).mappings().all()
    recipients: list[ClassMemberState] = []
    by_class: dict[str, list[ClassMemberState]] = {}
    for r in rows:
        cid = str(r["class_id"])
        by_class.setdefault(cid, []).append(
            ClassMemberState(
                class_id=cid,
                tenant_id=str(r["tenant_id"]),
                membership_id=str(r["membership_id"]),
                role_in_class=ClassRole(r["role_in_class"]),
                status=ClassMemberStatus(r["status"]),
                left_at=r["left_at"],
            )
        )
    for cid, class_roster in by_class.items():
        recipients.extend(filter_active_assignment_recipients(class_roster, tenant_id=str(tenant_id), class_id=cid))
    return tuple(recipients)


async def _load_recipients(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
) -> tuple[uuid.UUID, ...]:
    if not (await _has_priv(session, "class_members", "SELECT") and await _has_priv(session, "classes", "SELECT")):
        return ()
    mids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    if class_id is not None:
        states = await get_active_class_recipients(session, tenant_id=tenant_id, class_id=class_id)
        for st in states:
            uid = uuid.UUID(st.membership_id)
            if uid not in seen:
                seen.add(uid)
                mids.append(uid)
    elif group_id is not None:
        states = await _resolve_group_active_recipients(session, tenant_id=tenant_id, group_id=group_id)
        for st in states:
            uid = uuid.UUID(st.membership_id)
            if uid not in seen:
                seen.add(uid)
                mids.append(uid)
    return tuple(mids)


def _to_view(
    row: AssessmentAssignment,
    *,
    manually_released: bool,
    recipients: tuple[uuid.UUID, ...],
) -> AssignmentView:
    return AssignmentView(
        id=row.id,
        tenant_id=row.tenant_id,
        assessment_version_id=row.assessment_version_id,
        class_id=row.class_id,
        group_id=row.group_id,
        assigned_by=row.assigned_by,
        available_from=row.available_from,
        due_at=row.due_at,
        max_attempts=row.max_attempts,
        reveal_policy=row.reveal_policy,
        manually_released=manually_released,
        created_at=row.created_at,
        active_recipient_membership_ids=recipients,
    )


async def create_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assessment_version_id: uuid.UUID,
    assigned_by: uuid.UUID,
    class_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    available_from: datetime | None = None,
    due_at: datetime | None = None,
    max_attempts: int = 1,
    reveal_policy: RevealPolicy | str = RevealPolicy.AFTER_DUE,
) -> AssignmentView:
    """Create an assignment scoped to a class or group and snapshot active recipients."""
    if class_id is None and group_id is None:
        raise AssignmentValidationError("TARGET_REQUIRED", "class_id or group_id must be specified")

    policy = _normalize_reveal_policy(reveal_policy)
    start_at = datetime.now(UTC) if available_from is None else available_from
    try:
        AssignmentSchedule(
            assignment_id="pending",
            available_from=start_at,
            due_at=due_at,
            max_attempts=max_attempts,
            reveal_policy=policy,
            manually_released=False,
        )
    except ValueError as exc:
        raise AssignmentValidationError("INVALID_SCHEDULE", str(exc)) from exc

    if await _has_priv(session, "assessment_versions", "SELECT"):
        av = (
            await session.execute(
                text("SELECT 1 FROM assessment_versions WHERE tenant_id = :tid AND id = :avid"),
                {"tid": tenant_id, "avid": assessment_version_id},
            )
        ).first()
        if av is None:
            raise AssignmentNotFoundError(f"assessment_version {assessment_version_id} not found in tenant")

    if class_id is not None and await _has_priv(session, "classes", "SELECT"):
        cls_row = (
            await session.execute(
                text("SELECT 1 FROM classes WHERE tenant_id = :tid AND id = :cid"),
                {"tid": tenant_id, "cid": class_id},
            )
        ).first()
        if cls_row is None:
            raise AssignmentNotFoundError(f"class {class_id} not found in tenant")

    if group_id is not None and await _has_priv(session, "groups", "SELECT"):
        grp_row = (
            await session.execute(
                text("SELECT 1 FROM groups WHERE tenant_id = :tid AND id = :gid"),
                {"tid": tenant_id, "gid": group_id},
            )
        ).first()
        if grp_row is None:
            raise AssignmentNotFoundError(f"group {group_id} not found in tenant")

    row = AssessmentAssignment(
        tenant_id=tenant_id,
        assessment_version_id=assessment_version_id,
        class_id=class_id,
        group_id=group_id,
        assigned_by=assigned_by,
        available_from=start_at,
        due_at=due_at,
        max_attempts=max_attempts,
        reveal_policy=policy.value,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)

    recipients = await _load_recipients(session, tenant_id=tenant_id, class_id=class_id, group_id=group_id)
    return _to_view(row, manually_released=False, recipients=recipients)


async def get_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> AssignmentView:
    """Fetch an assignment cleanly under both `APP_RW` and `EXAM_SESSION_RW` roles."""
    row = (
        await session.execute(
            select(AssessmentAssignment).where(
                AssessmentAssignment.tenant_id == tenant_id,
                AssessmentAssignment.id == assignment_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AssignmentNotFoundError(f"assignment {assignment_id} not found in tenant")

    released = await _is_manually_released(session, tenant_id=tenant_id, assignment_id=assignment_id)
    recipients = await _load_recipients(
        session, tenant_id=tenant_id, class_id=row.class_id, group_id=row.group_id
    )
    return _to_view(row, manually_released=released, recipients=recipients)


async def list_assignments(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
) -> tuple[AssignmentSummaryView, ...]:
    """Assignments of the tenant, newest first, optionally of one class.

    With `membership_id` only assignments of the classes that membership teaches or learns in are returned (a
    class it does not belong to yields nothing, not an error); without it, everything in the tenant.
    """
    stmt = select(AssessmentAssignment).where(AssessmentAssignment.tenant_id == tenant_id)
    if membership_id is not None:
        visible = {
            c.id for c in await list_classes(session, tenant_id=tenant_id, membership_id=membership_id)
        }
        if class_id is not None and class_id not in visible:
            return ()
        stmt = stmt.where(AssessmentAssignment.class_id.in_(visible))
    if class_id is not None:
        stmt = stmt.where(AssessmentAssignment.class_id == class_id)
    rows = (
        (await session.execute(stmt.order_by(AssessmentAssignment.created_at.desc(), AssessmentAssignment.id.desc())))
        .scalars()
        .all()
    )
    if not rows:
        return ()

    version_ids = list({r.assessment_version_id for r in rows})
    papers = {
        row.version_id: (row.assessment_id, row.title)
        for row in (
            await session.execute(
                text(
                    "SELECT v.id AS version_id, a.id AS assessment_id, a.title AS title "
                    "FROM assessment_versions v "
                    "JOIN assessments a ON a.id = v.assessment_id AND a.tenant_id = v.tenant_id "
                    "WHERE v.tenant_id = :tid AND v.id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"tid": tenant_id, "ids": version_ids},
            )
        )
    }
    return tuple(
        AssignmentSummaryView(
            id=r.id,
            class_id=r.class_id,
            group_id=r.group_id,
            assessment_version_id=r.assessment_version_id,
            assessment_id=papers.get(r.assessment_version_id, (None, None))[0],
            assessment_title=papers.get(r.assessment_version_id, (None, None))[1],
            available_from=r.available_from,
            due_at=r.due_at,
            max_attempts=r.max_attempts,
            reveal_policy=r.reveal_policy,
            created_at=r.created_at,
        )
        for r in rows
    )


async def can_view_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment: AssignmentView,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Who sees an assignment: its author, whoever teaches or sits in its class, or (group) in a class of its group."""
    if assignment.assigned_by == user_id:
        return True
    if assignment.class_id is not None:
        return await is_class_participant(
            session, tenant_id=tenant_id, class_id=assignment.class_id, membership_id=membership_id
        )
    if assignment.group_id is not None:
        found = await session.execute(
            text(
                "SELECT 1 FROM classes c "
                "WHERE c.tenant_id = :tid AND c.group_id = :gid AND (c.teacher_membership_id = :mid OR EXISTS ("
                "  SELECT 1 FROM class_members cm WHERE cm.tenant_id = c.tenant_id AND cm.class_id = c.id "
                "  AND cm.membership_id = :mid AND cm.status = 'ACTIVE')) LIMIT 1"
            ),
            {"tid": tenant_id, "gid": assignment.group_id, "mid": membership_id},
        )
        return found.first() is not None
    return False


async def update_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    available_from: datetime | None = None,
    due_at: datetime | None = None,
    clear_due_at: bool = False,
    max_attempts: int | None = None,
    reveal_policy: RevealPolicy | str | None = None,
    manually_released: bool | None = None,
) -> AssignmentView:
    """Update assignment schedule fields or trigger MANUAL_RELEASE answer reveal."""
    row = (
        await session.execute(
            select(AssessmentAssignment).where(
                AssessmentAssignment.tenant_id == tenant_id,
                AssessmentAssignment.id == assignment_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AssignmentNotFoundError(f"assignment {assignment_id} not found in tenant")

    next_start = row.available_from if available_from is None else available_from
    next_due = None if clear_due_at else (row.due_at if due_at is None else due_at)
    next_max = row.max_attempts if max_attempts is None else max_attempts
    next_policy = RevealPolicy(row.reveal_policy) if reveal_policy is None else _normalize_reveal_policy(reveal_policy)
    current_released = await _is_manually_released(session, tenant_id=tenant_id, assignment_id=assignment_id)
    next_released = current_released if manually_released is None else manually_released

    try:
        AssignmentSchedule(
            assignment_id=str(row.id),
            available_from=next_start,
            due_at=next_due,
            max_attempts=next_max,
            reveal_policy=next_policy,
            manually_released=next_released,
        )
    except ValueError as exc:
        raise AssignmentValidationError("INVALID_SCHEDULE", str(exc)) from exc

    row.available_from = next_start
    row.due_at = next_due
    row.max_attempts = next_max
    row.reveal_policy = next_policy.value
    await session.flush()
    await session.refresh(row)

    if manually_released is not None:
        await _set_manually_released(
            session, tenant_id=tenant_id, assignment_id=assignment_id, released=manually_released
        )
        if manually_released:
            await _release_pending_grades_and_emit(session, tenant_id=tenant_id, assignment_id=assignment_id)

    recipients = await _load_recipients(
        session, tenant_id=tenant_id, class_id=row.class_id, group_id=row.group_id
    )
    return _to_view(row, manually_released=next_released, recipients=recipients)


async def _release_pending_grades_and_emit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> None:
    if not (await _has_priv(session, "grades", "UPDATE") and await _has_priv(session, "attempts", "SELECT")):
        return
    released_at = datetime.now(UTC)
    released_rows = (
        await session.execute(
            text(
                "UPDATE grades g SET released_at = :released_at "
                "FROM attempts a "
                "WHERE g.attempt_id = a.id AND g.tenant_id = a.tenant_id "
                "AND a.tenant_id = :tid AND a.assignment_id = :aid "
                "AND g.is_current = TRUE AND g.released_at IS NULL "
                "RETURNING g.id AS grade_id, a.id AS attempt_id, a.membership_id AS membership_id"
            ),
            {"tid": tenant_id, "aid": assignment_id, "released_at": released_at},
        )
    ).mappings().all()

    if released_rows and await _has_priv(session, "outbox_events", "INSERT"):
        for gr in released_rows:
            envelope = build_catalog_envelope(
                event_type="grade.released",
                aggregate_type="grade",
                aggregate_id=gr["grade_id"],
                payload=GradeReleasedPayload(
                    tenant_id=tenant_id,
                    grade_id=gr["grade_id"],
                    attempt_id=gr["attempt_id"],
                    membership_id=gr["membership_id"],
                    released_at=released_at,
                ),
            )
            await enqueue(session, envelope)


async def _resolve_member_for_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    row: AssessmentAssignment,
    membership_id: uuid.UUID,
) -> ClassMemberState:
    if row.class_id is not None:
        member = await get_class_member(
            session, tenant_id=tenant_id, class_id=row.class_id, membership_id=membership_id
        )
        if member is None:
            raise ClassroomPolicyError("MEMBER_NOT_IN_CLASS", "Membership is not enrolled in the assigned class")
        return to_class_member_state(member)

    if row.group_id is not None:
        member_row = (
            await session.execute(
                text(
                    "SELECT cm.class_id, cm.tenant_id, cm.membership_id, cm.role_in_class, cm.status, cm.left_at "
                    "FROM class_members cm "
                    "JOIN classes c ON c.id = cm.class_id AND c.tenant_id = cm.tenant_id "
                    "WHERE cm.tenant_id = :tid AND c.group_id = :gid AND cm.membership_id = :mid "
                    "ORDER BY CASE WHEN cm.status = 'ACTIVE' THEN 0 ELSE 1 END, cm.joined_at DESC "
                    "LIMIT 1"
                ),
                {"tid": tenant_id, "gid": row.group_id, "mid": membership_id},
            )
        ).mappings().first()
        if member_row is None:
            raise ClassroomPolicyError("MEMBER_NOT_IN_GROUP", "Membership is not enrolled in any class of this group")
        return ClassMemberState(
            class_id=str(member_row["class_id"]),
            tenant_id=str(member_row["tenant_id"]),
            membership_id=str(member_row["membership_id"]),
            role_in_class=ClassRole(member_row["role_in_class"]),
            status=ClassMemberStatus(member_row["status"]),
            left_at=member_row["left_at"],
        )

    raise ClassroomPolicyError("SCOPE_REQUIRED", "Assignment has no class_id or group_id")


async def check_can_start_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    membership_id: uuid.UUID,
    now: datetime | None = None,
    attempts_used: int | None = None,
    allow_late_submission: bool = False,
) -> StartCheckResult:
    """Enforce F01 membership status and F02 availability/due/max_attempts gates."""
    row = (
        await session.execute(
            select(AssessmentAssignment).where(
                AssessmentAssignment.tenant_id == tenant_id,
                AssessmentAssignment.id == assignment_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AssignmentNotFoundError(f"assignment {assignment_id} not found in tenant")

    member_state = await _resolve_member_for_assignment(
        session, tenant_id=tenant_id, row=row, membership_id=membership_id
    )
    resolved_now = datetime.now(UTC) if now is None else now
    if attempts_used is not None:
        used = attempts_used
    elif await _has_priv(session, "attempts", "SELECT"):
        used = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM attempts "
                        "WHERE tenant_id = :tid AND assignment_id = :aid AND membership_id = :mid "
                        "AND status <> 'VOIDED'"
                    ),
                    {"tid": tenant_id, "aid": assignment_id, "mid": membership_id},
                )
            ).scalar()
            or 0
        )
    else:
        used = 0

    released = await _is_manually_released(session, tenant_id=tenant_id, assignment_id=assignment_id)
    schedule = _to_schedule(row, manually_released=released)
    assert_can_start_assignment(
        member=member_state,
        schedule=schedule,
        attempts_used=used,
        now=resolved_now,
        allow_late_submission=allow_late_submission,
    )
    return StartCheckResult(
        assignment_id=row.id,
        allowed=True,
        attempts_used=used,
        max_attempts=row.max_attempts,
        available_from=row.available_from,
        due_at=row.due_at,
    )


async def get_assignment_reveal_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    membership_id: uuid.UUID,
    now: datetime | None = None,
    learner_submitted: bool | None = None,
) -> RevealStatusResult:
    """Evaluate answer-reveal eligibility under the assignment's RevealPolicy."""
    row = (
        await session.execute(
            select(AssessmentAssignment).where(
                AssessmentAssignment.tenant_id == tenant_id,
                AssessmentAssignment.id == assignment_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AssignmentNotFoundError(f"assignment {assignment_id} not found in tenant")

    resolved_now = datetime.now(UTC) if now is None else now
    if learner_submitted is not None:
        submitted = learner_submitted
    elif await _has_priv(session, "attempts", "SELECT"):
        submitted = (
            await session.execute(
                text(
                    "SELECT 1 FROM attempts "
                    "WHERE tenant_id = :tid AND assignment_id = :aid AND membership_id = :mid "
                    "AND status IN ('SUBMITTED', 'AUTO_SUBMITTED', 'GRADED') LIMIT 1"
                ),
                {"tid": tenant_id, "aid": assignment_id, "mid": membership_id},
            )
        ).first() is not None
    else:
        submitted = False

    released = await _is_manually_released(session, tenant_id=tenant_id, assignment_id=assignment_id)
    schedule = _to_schedule(row, manually_released=released)
    can_reveal = can_reveal_assignment_answers(schedule, learner_submitted=submitted, now=resolved_now)
    return RevealStatusResult(
        assignment_id=row.id,
        reveal_policy=row.reveal_policy,
        learner_submitted=submitted,
        manually_released=released,
        can_reveal_answers=can_reveal,
        due_at=row.due_at,
    )
