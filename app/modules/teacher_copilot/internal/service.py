"""Database service layer for E06 Teacher AI Copilot."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.teacher_copilot.internal.models import TeacherAssistJob
from app.modules.teacher_copilot.internal.teacher_assist import (
    CrossClassScopeError,
    DraftAssignmentStatus,
    DraftRemedialAssignment,
    StudentMistakeObservation,
    confirm_remedial_draft,
    generate_class_copilot_summary,
)


class TeacherCopilotScopeError(PermissionError):
    """Raised when Teacher Copilot access or observations violate class/teacher/tenant scope."""


class TeacherCopilotNotFoundError(LookupError):
    """Raised when a class, assignment, or assist job is not found inside tenant RLS."""


class TeacherCopilotValidationError(ValueError):
    """Raised when Teacher Copilot request payload or draft state fails validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TeacherCopilotConflictError(ValueError):
    """Raised when a draft assignment has already been confirmed or discarded."""


@dataclass(frozen=True, slots=True)
class TeacherAssistJobView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: uuid.UUID
    assignment_id: uuid.UUID
    teacher_membership_id: uuid.UUID
    status: str
    clusters: tuple[dict[str, Any], ...]
    draft_assignments: tuple[dict[str, Any], ...]
    created_at: datetime


async def _table_exists(session: AsyncSession, qualified_name: str) -> bool:
    result = await session.execute(
        text("SELECT to_regclass(:name) IS NOT NULL"),
        {"name": qualified_name},
    )
    return bool(result.scalar())


async def _verify_class_teacher_ownership(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
) -> None:
    row = (
        await session.execute(
            text(
                """
                SELECT teacher_membership_id
                FROM classes
                WHERE id = :class_id
                  AND tenant_id = :tenant_id
                """
            ),
            {"class_id": class_id, "tenant_id": tenant_id},
        )
    ).first()
    if row is None:
        raise TeacherCopilotNotFoundError(f"class {class_id} was not found in tenant {tenant_id}")
    if uuid.UUID(str(row[0])) != actor_membership_id:
        raise TeacherCopilotScopeError(
            "only the owning teacher of the class may invoke or confirm Teacher Copilot"
        )


def _to_job_view(job: TeacherAssistJob) -> TeacherAssistJobView:
    report = dict(job.summary_report_json or {})
    clusters = tuple(dict(c) for c in report.get("clusters", ()))
    drafts = tuple(dict(d) for d in report.get("draft_assignments", ()))
    teacher_id_raw = report.get("teacher_membership_id")
    teacher_uuid = uuid.UUID(str(teacher_id_raw)) if teacher_id_raw else uuid.UUID(int=0)
    return TeacherAssistJobView(
        id=job.id,
        tenant_id=job.tenant_id,
        class_id=job.class_id,
        assignment_id=job.assignment_id,
        teacher_membership_id=teacher_uuid,
        status=job.status,
        clusters=clusters,
        draft_assignments=drafts,
        created_at=job.created_at,
    )


def _extract_choice_label(payload: Any, *, default: str) -> str:
    if isinstance(payload, dict):
        for key in ("chosen_option", "selected_option", "selected", "option", "answer"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for key in ("correct_option", "correct", "key"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return default


async def _load_class_roster_student_ids(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
) -> set[uuid.UUID]:
    rows = (
        await session.execute(
            text(
                """
                SELECT membership_id
                FROM class_members
                WHERE tenant_id = :tenant_id
                  AND class_id = :class_id
                  AND status = 'ACTIVE'
                """
            ),
            {"tenant_id": tenant_id, "class_id": class_id},
        )
    ).all()
    return {uuid.UUID(str(r[0])) for r in rows}


async def _load_wrong_observations_from_db(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> tuple[StudentMistakeObservation, ...]:
    required_tables = (
        "public.attempts",
        "public.grades",
        "public.grading_details",
        "public.responses",
    )
    for tbl in required_tables:
        if not await _table_exists(session, tbl):
            return ()

    has_qva = await _table_exists(session, "public.question_version_answers")
    if has_qva:
        sql = """
            SELECT
                att.membership_id AS student_id,
                COALESCE(q.primary_taxonomy_node_id, qv.id) AS taxonomy_node_id,
                qv.id AS question_id,
                r.response_payload AS response_payload,
                qva.answer_schema AS answer_schema
            FROM attempts att
            JOIN class_members cm
                ON cm.tenant_id = att.tenant_id
               AND cm.class_id = :class_id
               AND cm.membership_id = att.membership_id
               AND cm.status = 'ACTIVE'
            JOIN grades g
                ON g.tenant_id = att.tenant_id
               AND g.attempt_id = att.id
               AND g.is_current = TRUE
            JOIN grading_details gd
                ON gd.tenant_id = g.tenant_id
               AND gd.grade_id = g.id
               AND gd.is_correct = FALSE
            JOIN responses r
                ON r.tenant_id = att.tenant_id
               AND r.attempt_id = att.id
               AND r.question_version_id = gd.question_version_id
            JOIN question_versions qv
                ON qv.id = gd.question_version_id
            JOIN questions q
                ON q.id = qv.question_id
            LEFT JOIN question_version_answers qva
                ON qva.question_version_id = qv.id
               AND qva.tenant_id = qv.tenant_id
            WHERE att.tenant_id = :tenant_id
              AND att.assignment_id = :assignment_id
            ORDER BY att.membership_id ASC, qv.id ASC
        """
    else:
        sql = """
            SELECT
                att.membership_id AS student_id,
                COALESCE(q.primary_taxonomy_node_id, qv.id) AS taxonomy_node_id,
                qv.id AS question_id,
                r.response_payload AS response_payload,
                NULL::jsonb AS answer_schema
            FROM attempts att
            JOIN class_members cm
                ON cm.tenant_id = att.tenant_id
               AND cm.class_id = :class_id
               AND cm.membership_id = att.membership_id
               AND cm.status = 'ACTIVE'
            JOIN grades g
                ON g.tenant_id = att.tenant_id
               AND g.attempt_id = att.id
               AND g.is_current = TRUE
            JOIN grading_details gd
                ON gd.tenant_id = g.tenant_id
               AND gd.grade_id = g.id
               AND gd.is_correct = FALSE
            JOIN responses r
                ON r.tenant_id = att.tenant_id
               AND r.attempt_id = att.id
               AND r.question_version_id = gd.question_version_id
            JOIN question_versions qv
                ON qv.id = gd.question_version_id
            JOIN questions q
                ON q.id = qv.question_id
            WHERE att.tenant_id = :tenant_id
              AND att.assignment_id = :assignment_id
            ORDER BY att.membership_id ASC, qv.id ASC
        """

    rows = (
        await session.execute(
            text(sql),
            {
                "tenant_id": tenant_id,
                "class_id": class_id,
                "assignment_id": assignment_id,
            },
        )
    ).all()

    observations: list[StudentMistakeObservation] = []
    for student_id, tax_id, q_id, resp_payload, answer_schema in rows:
        chosen = _extract_choice_label(resp_payload, default="SELECTED_WRONG")
        correct = _extract_choice_label(answer_schema, default="EXPECTED_CORRECT")
        if chosen == correct:
            correct = f"EXPECTED_{correct}"
        observations.append(
            StudentMistakeObservation(
                tenant_id=str(tenant_id),
                class_id=str(class_id),
                student_id=str(student_id),
                taxonomy_node_id=str(tax_id),
                question_id=str(q_id),
                chosen_option=chosen,
                correct_option=correct,
            )
        )
    return tuple(observations)


async def run_teacher_ai_assist(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
    observations: Sequence[StudentMistakeObservation] | None = None,
    max_question_target: int = 10,
) -> TeacherAssistJobView:
    """Cluster class misconceptions strictly within owning teacher's class & create unconfirmed drafts."""
    await _verify_class_teacher_ownership(
        session,
        tenant_id=tenant_id,
        class_id=class_id,
        actor_membership_id=actor_membership_id,
    )

    assignment_row = (
        await session.execute(
            text(
                """
                SELECT class_id
                FROM assessment_assignments
                WHERE id = :assignment_id
                  AND tenant_id = :tenant_id
                """
            ),
            {"assignment_id": assignment_id, "tenant_id": tenant_id},
        )
    ).first()
    if assignment_row is None:
        raise TeacherCopilotNotFoundError(
            f"assignment {assignment_id} was not found in tenant {tenant_id}"
        )
    if assignment_row[0] is None or uuid.UUID(str(assignment_row[0])) != class_id:
        raise TeacherCopilotScopeError(
            f"assignment {assignment_id} does not belong to class {class_id}"
        )

    roster_ids = await _load_class_roster_student_ids(
        session,
        tenant_id=tenant_id,
        class_id=class_id,
    )

    if observations is not None:
        checked_obs: list[StudentMistakeObservation] = []
        for obs in observations:
            if obs.tenant_id != str(tenant_id) or obs.class_id != str(class_id):
                raise TeacherCopilotScopeError(
                    f"observation class {obs.class_id!r} (tenant {obs.tenant_id!r}) violates target class scope"
                )
            try:
                student_uuid = uuid.UUID(obs.student_id)
            except ValueError as exc:
                raise TeacherCopilotScopeError(
                    f"observation student_id {obs.student_id!r} is not a valid class member UUID"
                ) from exc
            if student_uuid not in roster_ids:
                raise TeacherCopilotScopeError(
                    f"student {obs.student_id!r} is not an active member of class {class_id}"
                )
            checked_obs.append(obs)
        final_observations = tuple(checked_obs)
    else:
        final_observations = await _load_wrong_observations_from_db(
            session,
            tenant_id=tenant_id,
            class_id=class_id,
            assignment_id=assignment_id,
        )

    run_id = uuid.uuid4()
    try:
        summary = generate_class_copilot_summary(
            tenant_id=str(tenant_id),
            class_id=str(class_id),
            run_id=str(run_id),
            observations=final_observations,
            max_question_target=max_question_target,
        )
    except CrossClassScopeError as exc:
        raise TeacherCopilotScopeError(str(exc)) from exc
    except ValueError as exc:
        raise TeacherCopilotValidationError("INVALID_COPILOT_INPUT", str(exc)) from exc

    summary_report_json: dict[str, Any] = {
        "teacher_membership_id": str(actor_membership_id),
        "max_question_target": max_question_target,
        "clusters": [
            {
                "taxonomy_node_id": c.taxonomy_node_id,
                "wrong_response_count": c.wrong_response_count,
                "affected_student_ids": list(c.affected_student_ids),
                "question_ids": list(c.question_ids),
                "top_distractor": c.top_distractor,
                "summary_text": c.summary_text,
            }
            for c in summary.clusters
        ],
        "draft_assignments": [
            {
                "draft_id": d.draft_id,
                "tenant_id": d.tenant_id,
                "class_id": d.class_id,
                "taxonomy_node_id": d.taxonomy_node_id,
                "target_student_ids": list(d.target_student_ids),
                "suggested_question_count": d.suggested_question_count,
                "rationale": d.rationale,
                "status": d.status.value,
                "confirmed_by_teacher_id": d.confirmed_by_teacher_id,
            }
            for d in summary.draft_assignments
        ],
    }

    job = TeacherAssistJob(
        id=run_id,
        tenant_id=tenant_id,
        class_id=class_id,
        assignment_id=assignment_id,
        summary_report_json=summary_report_json,
        status="READY_FOR_TEACHER",
    )
    session.add(job)
    await session.flush()
    return _to_job_view(job)


async def get_teacher_assist_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    job_id: uuid.UUID,
) -> TeacherAssistJobView:
    """Fetch a single Teacher Copilot job after enforcing owning-teacher & tenant scope."""
    job = (
        await session.execute(
            select(TeacherAssistJob).where(
                TeacherAssistJob.id == job_id,
                TeacherAssistJob.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise TeacherCopilotNotFoundError(f"teacher assist job {job_id} was not found")

    await _verify_class_teacher_ownership(
        session,
        tenant_id=tenant_id,
        class_id=job.class_id,
        actor_membership_id=actor_membership_id,
    )
    return _to_job_view(job)


async def confirm_teacher_assist_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_membership_id: uuid.UUID,
    job_id: uuid.UUID,
    draft_id: str | None = None,
) -> TeacherAssistJobView:
    """Require explicit owning-teacher confirmation before marking remedial drafts CONFIRMED."""
    job = (
        await session.execute(
            select(TeacherAssistJob).where(
                TeacherAssistJob.id == job_id,
                TeacherAssistJob.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise TeacherCopilotNotFoundError(f"teacher assist job {job_id} was not found")

    await _verify_class_teacher_ownership(
        session,
        tenant_id=tenant_id,
        class_id=job.class_id,
        actor_membership_id=actor_membership_id,
    )

    report = dict(job.summary_report_json or {})
    draft_dicts = [dict(d) for d in report.get("draft_assignments", ())]
    if not draft_dicts:
        raise TeacherCopilotValidationError("NO_DRAFTS", "teacher assist job has no draft assignments to confirm")

    matched = False
    updated_drafts: list[dict[str, Any]] = []
    for raw_d in draft_dicts:
        if draft_id is not None and raw_d.get("draft_id") != draft_id:
            updated_drafts.append(raw_d)
            continue

        matched = True
        pure_draft = DraftRemedialAssignment(
            draft_id=str(raw_d["draft_id"]),
            tenant_id=str(raw_d["tenant_id"]),
            class_id=str(raw_d["class_id"]),
            taxonomy_node_id=str(raw_d["taxonomy_node_id"]),
            target_student_ids=tuple(str(x) for x in raw_d.get("target_student_ids", ())),
            suggested_question_count=int(raw_d["suggested_question_count"]),
            rationale=str(raw_d["rationale"]),
            status=DraftAssignmentStatus(str(raw_d["status"])),
            confirmed_by_teacher_id=(
                str(raw_d["confirmed_by_teacher_id"])
                if raw_d.get("confirmed_by_teacher_id") is not None
                else None
            ),
        )
        try:
            confirmed = confirm_remedial_draft(
                pure_draft,
                teacher_id=str(actor_membership_id),
                actor_tenant_id=str(tenant_id),
            )
        except CrossClassScopeError as exc:
            raise TeacherCopilotScopeError(str(exc)) from exc
        except ValueError as exc:
            raise TeacherCopilotConflictError(str(exc)) from exc

        raw_d["status"] = confirmed.status.value
        raw_d["confirmed_by_teacher_id"] = confirmed.confirmed_by_teacher_id
        updated_drafts.append(raw_d)

    if not matched:
        raise TeacherCopilotValidationError("DRAFT_NOT_FOUND", f"draft_id {draft_id!r} was not found in job {job_id}")

    all_confirmed = all(d.get("status") == DraftAssignmentStatus.CONFIRMED.value for d in updated_drafts)
    job.summary_report_json = {
        **report,
        "draft_assignments": updated_drafts,
    }
    job.status = "CONFIRMED" if all_confirmed else "PARTIALLY_CONFIRMED"
    await session.flush()
    return _to_job_view(job)
