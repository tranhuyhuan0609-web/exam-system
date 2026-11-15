"""Database service layer for F03 Class Gradebook Projections, Item Analysis & CSV Export."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.gradebook.internal.analytics import (
    ClassAnalyticsSummary,
    GradebookError,
    ItemResponseObservation,
    QuestionDistractorReport,
    StudentAttemptGrade,
    compute_class_analytics,
    render_gradebook_csv,
)
from app.modules.gradebook.internal.models import GradebookProjection


class GradebookNotFoundError(LookupError):
    """Raised when the requested class or assignment cannot be found in tenant scope."""


class GradebookValidationError(ValueError):
    """Raised when gradebook computation inputs or parameters are invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GradebookProjectionView:
    class_id: uuid.UUID
    assignment_id: uuid.UUID
    tenant_id: uuid.UUID
    stats_json: dict[str, Any]
    top_wrong_questions_json: list[dict[str, Any]]
    rows: tuple[StudentAttemptGrade, ...]
    updated_at: datetime


async def _has_priv(session: AsyncSession, table_name: str, privilege: str) -> bool:
    res = await session.execute(
        text(
            "SELECT to_regclass('public.' || quote_ident(:tbl)) IS NOT NULL "
            "AND has_table_privilege(current_user, 'public.' || quote_ident(:tbl), :priv)"
        ),
        {"tbl": table_name, "priv": privilege},
    )
    return bool(res.scalar())


def _extract_selected_option(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("selected_option", "choice", "option", "answer"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    opts = payload.get("selected_options")
    if isinstance(opts, list) and opts:
        return ",".join(str(x) for x in opts if x is not None) or None
    return None


def _summary_to_json(summary: ClassAnalyticsSummary) -> dict[str, Any]:
    return {
        "total_submissions": summary.total_submissions,
        "on_time_submissions": summary.on_time_submissions,
        "on_time_rate": str(summary.on_time_rate),
        "mean_score": str(summary.mean_score),
        "min_score": str(summary.min_score),
        "max_score": str(summary.max_score),
        "score_histogram_2pt_bins": dict(summary.score_histogram_2pt_bins),
    }


def _reports_to_json(reports: tuple[QuestionDistractorReport, ...]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": rep.question_id,
            "attempt_count": rep.attempt_count,
            "wrong_count": rep.wrong_count,
            "error_rate": str(rep.error_rate),
            "top_wrong_distractor": rep.top_wrong_distractor,
            "top_wrong_distractor_count": rep.top_wrong_distractor_count,
        }
        for rep in reports
    ]


async def _load_assignment_due_at(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    require_exists: bool,
) -> datetime | None:
    if not await _has_priv(session, "assessment_assignments", "SELECT"):
        return None
    row = (
        await session.execute(
            text("SELECT id, due_at FROM assessment_assignments WHERE tenant_id = :tid AND id = :aid"),
            {"tid": tenant_id, "aid": assignment_id},
        )
    ).mappings().first()
    if row is None:
        if require_exists:
            raise GradebookNotFoundError(f"assignment {assignment_id} not found in tenant")
        return None
    return row["due_at"]


async def _load_student_labels(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    labels: dict[uuid.UUID, str] = {}
    if not membership_ids:
        return labels
    if not await _has_priv(session, "memberships", "SELECT"):
        return labels
    can_read_users = await _has_priv(session, "users", "SELECT")
    if can_read_users:
        rows = (
            await session.execute(
                text(
                    "SELECT m.id AS membership_id, u.pseudonym_handle "
                    "FROM memberships m "
                    "LEFT JOIN users u ON u.id = m.user_id "
                    "WHERE m.tenant_id = :tid AND m.id = ANY(:mids)"
                ),
                {"tid": tenant_id, "mids": membership_ids},
            )
        ).mappings().all()
        for r in rows:
            handle = r["pseudonym_handle"]
            labels[r["membership_id"]] = str(handle) if handle else str(r["membership_id"])
    return labels


async def _load_item_observations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    attempt_ids: list[uuid.UUID],
) -> list[ItemResponseObservation]:
    if not attempt_ids:
        return []
    if not (
        await _has_priv(session, "grading_details", "SELECT")
        and await _has_priv(session, "grades", "SELECT")
        and await _has_priv(session, "attempts", "SELECT")
    ):
        return []

    can_read_qv = await _has_priv(session, "question_versions", "SELECT")
    can_read_resp = await _has_priv(session, "responses", "SELECT")

    select_qid = (
        "COALESCE(qv.question_id::text, gd.question_version_id::text)"
        if can_read_qv
        else "gd.question_version_id::text"
    )
    select_payload = "r.response_payload" if can_read_resp else "NULL::jsonb"
    join_qv = "LEFT JOIN question_versions qv ON qv.id = gd.question_version_id " if can_read_qv else ""
    join_resp = (
        "LEFT JOIN responses r ON r.attempt_id = a.id AND r.question_version_id = gd.question_version_id "
        "AND r.tenant_id = gd.tenant_id "
        if can_read_resp
        else ""
    )

    sql = (
        f"SELECT {select_qid} AS question_id, gd.is_correct, {select_payload} AS response_payload "
        "FROM grading_details gd "
        "JOIN grades g ON g.id = gd.grade_id AND g.tenant_id = gd.tenant_id "
        "JOIN attempts a ON a.id = g.attempt_id AND a.tenant_id = g.tenant_id "
        f"{join_qv}{join_resp}"
        "WHERE gd.tenant_id = :tid AND a.assignment_id = :aid AND g.is_current = TRUE "
        "AND a.id = ANY(:attempt_ids) "
        "ORDER BY gd.id ASC"
    )
    rows = (
        await session.execute(
            text(sql),
            {"tid": tenant_id, "aid": assignment_id, "attempt_ids": attempt_ids},
        )
    ).mappings().all()
    return [
        ItemResponseObservation(
            question_id=str(r["question_id"]),
            is_correct=bool(r["is_correct"]),
            selected_option=_extract_selected_option(r["response_payload"]),
        )
        for r in rows
    ]


async def load_class_gradebook_inputs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> tuple[tuple[StudentAttemptGrade, ...], tuple[ItemResponseObservation, ...]]:
    """
    Collect current student attempt grades & item observations from PostgreSQL.

    Preserves `INACTIVE` soft-deleted class members who have historical attempts/grades.
    """
    if await _has_priv(session, "classes", "SELECT"):
        cls_exists = (
            await session.execute(
                text("SELECT 1 FROM classes WHERE tenant_id = :tid AND id = :cid"),
                {"tid": tenant_id, "cid": class_id},
            )
        ).first()
        if cls_exists is None:
            raise GradebookNotFoundError(f"class {class_id} not found in tenant")

    due_at = await _load_assignment_due_at(
        session, tenant_id=tenant_id, assignment_id=assignment_id, require_exists=True
    )

    can_read_roster = await _has_priv(session, "class_members", "SELECT")
    can_read_attempts = await _has_priv(session, "attempts", "SELECT")
    can_read_grades = await _has_priv(session, "grades", "SELECT")

    roster_rows: list[Any] = []
    if can_read_roster:
        roster_rows = list(
            (
                await session.execute(
                    text(
                        "SELECT membership_id, role_in_class, status "
                        "FROM class_members WHERE tenant_id = :tid AND class_id = :cid "
                        "ORDER BY joined_at ASC, id ASC"
                    ),
                    {"tid": tenant_id, "cid": class_id},
                )
            )
            .mappings()
            .all()
        )

    member_status_by_id = {r["membership_id"]: str(r["status"]) for r in roster_rows}
    member_role_by_id = {r["membership_id"]: str(r["role_in_class"]) for r in roster_rows}

    best_attempt_by_mid: dict[uuid.UUID, dict[str, Any]] = {}
    if can_read_attempts:
        grade_join = (
            "LEFT JOIN grades g ON g.attempt_id = a.id AND g.tenant_id = a.tenant_id AND g.is_current = TRUE "
            if can_read_grades
            else ""
        )
        score_col = "g.total_score" if can_read_grades else "NULL::numeric"
        attempt_rows = (
            await session.execute(
                text(
                    f"SELECT a.id AS attempt_id, a.membership_id, a.status, a.submitted_at, a.started_at, "
                    f"{score_col} AS total_score "
                    "FROM attempts a "
                    f"{grade_join}"
                    "WHERE a.tenant_id = :tid AND a.assignment_id = :aid "
                    "AND a.status IN ('SUBMITTED', 'AUTO_SUBMITTED', 'GRADED') "
                    "ORDER BY a.submitted_at DESC NULLS LAST, a.started_at DESC"
                ),
                {"tid": tenant_id, "aid": assignment_id},
            )
        ).mappings().all()
        for r in attempt_rows:
            mid = r["membership_id"]
            if roster_rows and mid not in member_status_by_id:
                continue
            if mid not in best_attempt_by_mid:
                best_attempt_by_mid[mid] = dict(r)

    target_mids: list[uuid.UUID] = []
    seen_mids: set[uuid.UUID] = set()
    for r in roster_rows:
        mid = r["membership_id"]
        is_active_student = r["status"] == "ACTIVE" and r["role_in_class"] == "STUDENT"
        has_historical_attempt = mid in best_attempt_by_mid
        if (is_active_student or has_historical_attempt) and mid not in seen_mids:
            seen_mids.add(mid)
            target_mids.append(mid)
    for mid in best_attempt_by_mid:
        if mid not in seen_mids:
            seen_mids.add(mid)
            target_mids.append(mid)

    labels = await _load_student_labels(session, tenant_id=tenant_id, membership_ids=target_mids)

    grades_out: list[StudentAttemptGrade] = []
    attempt_ids: list[uuid.UUID] = []
    for mid in target_mids:
        att = best_attempt_by_mid.get(mid)
        if att is not None:
            attempt_ids.append(att["attempt_id"])
            score = Decimal(str(att["total_score"])) if att["total_score"] is not None else Decimal("0")
            submitted_at = att["submitted_at"]
        else:
            if member_role_by_id.get(mid, "STUDENT") != "STUDENT":
                continue
            score = Decimal("0")
            submitted_at = None

        grades_out.append(
            StudentAttemptGrade(
                membership_id=str(mid),
                student_label=labels.get(mid, str(mid)),
                score=score,
                submitted_at=submitted_at,
                due_at=due_at,
                is_historical_inactive_member=(member_status_by_id.get(mid) == "INACTIVE"),
            )
        )

    observations = await _load_item_observations(
        session, tenant_id=tenant_id, assignment_id=assignment_id, attempt_ids=attempt_ids
    )
    return tuple(grades_out), tuple(observations)


async def recompute_gradebook_projection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
    grades: Sequence[StudentAttemptGrade] | None = None,
    item_observations: Sequence[ItemResponseObservation] | None = None,
) -> GradebookProjectionView:
    """
    Recompute and upsert `gradebook_projections` idempotently.

    Callable both from API (`APP_RW`) and grading worker (`WORKER_GRADING`) passing preloaded rows.
    """
    if grades is None or item_observations is None:
        loaded_grades, loaded_obs = await load_class_gradebook_inputs(
            session, tenant_id=tenant_id, class_id=class_id, assignment_id=assignment_id
        )
        effective_grades = tuple(grades) if grades is not None else loaded_grades
        effective_obs = tuple(item_observations) if item_observations is not None else loaded_obs
    else:
        effective_grades = tuple(grades)
        effective_obs = tuple(item_observations)

    try:
        summary = compute_class_analytics(grades=effective_grades, item_observations=effective_obs)
    except GradebookError as exc:
        raise GradebookValidationError(exc.code, str(exc)) from exc

    stats_json = _summary_to_json(summary)
    top_wrong_json = _reports_to_json(summary.top_5_hardest_questions)

    stmt = (
        pg_insert(GradebookProjection)
        .values(
            class_id=class_id,
            assignment_id=assignment_id,
            tenant_id=tenant_id,
            stats_json=stats_json,
            top_wrong_questions_json=top_wrong_json,
        )
        .on_conflict_do_update(
            index_elements=[GradebookProjection.class_id, GradebookProjection.assignment_id],
            set_={
                "stats_json": stats_json,
                "top_wrong_questions_json": top_wrong_json,
                "updated_at": text("NOW()"),
            },
        )
        .returning(GradebookProjection)
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(stmt)).scalar_one()
    return GradebookProjectionView(
        class_id=row.class_id,
        assignment_id=row.assignment_id,
        tenant_id=row.tenant_id,
        stats_json=dict(row.stats_json),
        top_wrong_questions_json=list(row.top_wrong_questions_json),
        rows=effective_grades,
        updated_at=row.updated_at,
    )


async def get_gradebook_projection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
    refresh: bool = True,
) -> GradebookProjectionView:
    """Return the class gradebook projection (recomputing from live attempts/grades by default)."""
    if refresh:
        return await recompute_gradebook_projection(
            session, tenant_id=tenant_id, class_id=class_id, assignment_id=assignment_id
        )

    existing = (
        await session.execute(
            select(GradebookProjection).where(
                GradebookProjection.tenant_id == tenant_id,
                GradebookProjection.class_id == class_id,
                GradebookProjection.assignment_id == assignment_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return await recompute_gradebook_projection(
            session, tenant_id=tenant_id, class_id=class_id, assignment_id=assignment_id
        )
    rows, _ = await load_class_gradebook_inputs(
        session, tenant_id=tenant_id, class_id=class_id, assignment_id=assignment_id
    )
    return GradebookProjectionView(
        class_id=existing.class_id,
        assignment_id=existing.assignment_id,
        tenant_id=existing.tenant_id,
        stats_json=dict(existing.stats_json),
        top_wrong_questions_json=list(existing.top_wrong_questions_json),
        rows=rows,
        updated_at=existing.updated_at,
    )


async def export_gradebook_csv(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> str:
    """Render RFC-4180 formula-safe CSV preserving inactive members' historical scores."""
    rows, _ = await load_class_gradebook_inputs(
        session, tenant_id=tenant_id, class_id=class_id, assignment_id=assignment_id
    )
    try:
        return render_gradebook_csv(rows)
    except GradebookError as exc:
        raise GradebookValidationError(exc.code, str(exc)) from exc
