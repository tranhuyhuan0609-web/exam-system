"""Database service layer for D01 Learner Dashboard & Activity projections."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.dashboard.internal.dashboard import (
    ChapterMasterySummary,
    DashboardError,
    InProgressLessonRef,
    LearnerDashboardProjection,
    UpcomingAssignmentRef,
    build_learner_dashboard_projection,
)
from app.modules.dashboard.internal.models import (
    ActivityEvent,
    LearnerDashboardProjectionModel,
)


def _resolve_vn_tz() -> tzinfo:
    try:
        return ZoneInfo("Asia/Ho_Chi_Minh")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


VN_TZ: tzinfo = _resolve_vn_tz()


class DashboardValidationError(ValueError):
    """Raised when activity event or query inputs fail validation."""


class DashboardNotFoundError(LookupError):
    """Raised when a caller membership or projection does not exist."""


@dataclass(frozen=True, slots=True)
class ActivityEventView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    activity_type: str
    entity_id: uuid.UUID
    duration_seconds: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StoredDashboardProjectionView:
    membership_id: uuid.UUID
    tenant_id: uuid.UUID
    streak_days: int
    summary_json: dict[str, Any]
    updated_at: datetime


def _ensure_aware(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise DashboardValidationError("timestamp must be timezone-aware")
    return ts


def _activity_view(row: ActivityEvent) -> ActivityEventView:
    return ActivityEventView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        activity_type=row.activity_type,
        entity_id=row.entity_id,
        duration_seconds=row.duration_seconds,
        occurred_at=row.occurred_at,
    )


def _compute_streak_days(activity_timestamps: Sequence[datetime], *, now: datetime) -> int:
    """Count consecutive Asia/Ho_Chi_Minh study days ending today (or yesterday if not studied yet today)."""
    vn_dates = {ts.astimezone(VN_TZ).date() for ts in activity_timestamps}
    if not vn_dates:
        return 0
    today_vn = now.astimezone(VN_TZ).date()
    cursor: date = today_vn if today_vn in vn_dates else (today_vn - timedelta(days=1))
    streak = 0
    while cursor in vn_dates:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def _serialize_projection(projection: LearnerDashboardProjection) -> dict[str, Any]:
    return {
        "tenant_id": projection.tenant_id,
        "membership_id": projection.membership_id,
        "recent_lessons": [
            {
                "content_item_id": item.content_item_id,
                "title": item.title,
                "completion_pct": str(item.completion_pct),
                "last_accessed_at": item.last_accessed_at.isoformat(),
            }
            for item in projection.recent_lessons
        ],
        "upcoming_assignments": [
            {
                "assignment_id": a.assignment_id,
                "title": a.title,
                "due_at": a.due_at.isoformat(),
                "attempts_used": a.attempts_used,
                "max_attempts": a.max_attempts,
            }
            for a in projection.upcoming_assignments
        ],
        "open_error_notes_count": projection.open_error_notes_count,
        "fsrs_cards_due_today": projection.fsrs_cards_due_today,
        "chapter_mastery": [
            {
                "chapter_code": ch.chapter_code,
                "chapter_name": ch.chapter_name,
                "mastery_estimate": str(ch.mastery_estimate),
            }
            for ch in projection.chapter_mastery
        ],
        "overall_mastery_avg": str(projection.overall_mastery_avg),
    }


async def _readable_tables(session: AsyncSession, names: Sequence[str]) -> set[str]:
    """The tables of `names` the current role may SELECT (the grading worker reads fewer than the API, H-03)."""
    rows = (
        await session.execute(
            text(
                "SELECT t FROM unnest(CAST(:names AS text[])) AS t "
                "WHERE has_table_privilege(current_user, 'public.' || quote_ident(t), 'SELECT')"
            ),
            {"names": list(names)},
        )
    ).scalars().all()
    return set(rows)


async def record_activity_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    activity_type: str,
    entity_id: uuid.UUID,
    duration_seconds: int = 0,
    occurred_at: datetime | None = None,
) -> ActivityEventView:
    """Append an activity event for a learner within the caller's tenant."""
    clean_type = activity_type.strip().upper()
    if not clean_type or len(clean_type) > 50:
        raise DashboardValidationError("activity_type must be 1..50 non-blank characters")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int) or duration_seconds < 0:
        raise DashboardValidationError("duration_seconds must be a non-negative integer")

    ts = _ensure_aware(occurred_at)
    event = ActivityEvent(
        tenant_id=tenant_id,
        membership_id=membership_id,
        activity_type=clean_type,
        entity_id=entity_id,
        duration_seconds=duration_seconds,
        occurred_at=ts,
    )
    session.add(event)
    await session.flush()
    return _activity_view(event)


async def list_activity_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    activity_type: str | None = None,
    limit: int = 50,
) -> tuple[ActivityEventView, ...]:
    """List learner activity events ordered newest first."""
    if limit < 1 or limit > 200:
        raise DashboardValidationError("limit must be between 1 and 200")
    stmt = (
        select(ActivityEvent)
        .where(
            ActivityEvent.tenant_id == tenant_id,
            ActivityEvent.membership_id == membership_id,
        )
        .order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())
        .limit(limit)
    )
    if activity_type is not None:
        stmt = stmt.where(ActivityEvent.activity_type == activity_type.strip().upper())
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(_activity_view(r) for r in rows)


async def _gather_live_signals(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    now: datetime,
) -> tuple[
    list[InProgressLessonRef],
    list[UpcomingAssignmentRef],
    int,
    int,
    list[ChapterMasterySummary],
]:
    """Collect read signals from already-migrated Chapter 4 tables without hard coupling."""
    present = await _readable_tables(
        session,
        (
            "activity_events",
            "content_items",
            "content_versions",
            "assessment_assignments",
            "class_members",
            "attempts",
            "error_notes",
            "revision_schedules",
            "mastery_estimates",
            "taxonomy_nodes",
        ),
    )

    lessons: list[InProgressLessonRef] = []
    if {"activity_events", "content_items", "content_versions"}.issubset(present):
        lesson_rows = (
            await session.execute(
                text(
                    """
                    SELECT DISTINCT ON (ci.id)
                           ci.id AS content_item_id,
                           COALESCE(cv.title, ci.slug) AS title,
                           ae.duration_seconds,
                           ae.occurred_at
                    FROM activity_events ae
                    JOIN content_items ci ON ci.id = ae.entity_id
                    LEFT JOIN content_versions cv ON cv.id = ci.current_version_id AND cv.tenant_id = ci.tenant_id
                    WHERE ae.tenant_id = :tenant_id
                      AND ae.membership_id = :membership_id
                    ORDER BY ci.id, ae.occurred_at DESC
                    LIMIT 10
                    """
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).all()
        for row in lesson_rows:
            pct = min(Decimal("1"), Decimal(max(0, int(row.duration_seconds))) / Decimal("600"))
            lessons.append(
                InProgressLessonRef(
                    content_item_id=str(row.content_item_id),
                    title=str(row.title),
                    completion_pct=pct.quantize(Decimal("0.0001")),
                    last_accessed_at=row.occurred_at,
                )
            )

    assignments: list[UpcomingAssignmentRef] = []
    if {"assessment_assignments", "class_members"}.issubset(present):
        has_attempts = "attempts" in present
        attempts_subquery = (
            "(SELECT COUNT(*) FROM attempts a "
            " WHERE a.tenant_id = aa.tenant_id AND a.assignment_id = aa.id "
            "   AND a.membership_id = :membership_id AND a.status <> 'VOIDED')"
            if has_attempts
            else "0"
        )
        asgn_rows = (
            await session.execute(
                text(
                    f"""
                    SELECT aa.id AS assignment_id,
                           aa.due_at,
                           aa.max_attempts,
                           {attempts_subquery} AS attempts_used
                    FROM assessment_assignments aa
                    JOIN class_members cm
                      ON cm.class_id = aa.class_id AND cm.tenant_id = aa.tenant_id
                    WHERE aa.tenant_id = :tenant_id
                      AND cm.membership_id = :membership_id
                      AND cm.status = 'ACTIVE'
                      AND aa.due_at IS NOT NULL
                      AND aa.due_at >= :now
                    ORDER BY aa.due_at ASC
                    LIMIT 20
                    """
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id, "now": now},
            )
        ).all()
        for row in asgn_rows:
            assignments.append(
                UpcomingAssignmentRef(
                    assignment_id=str(row.assignment_id),
                    title=f"Assignment {str(row.assignment_id)[:8]}",
                    due_at=row.due_at,
                    attempts_used=int(row.attempts_used),
                    max_attempts=int(row.max_attempts),
                )
            )

    open_error_notes_count = 0
    if "error_notes" in present:
        open_error_notes_count = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM error_notes "
                        "WHERE tenant_id = :tenant_id AND membership_id = :membership_id "
                        "AND status IN ('OPEN', 'IN_REMEDIATION')"
                    ),
                    {"tenant_id": tenant_id, "membership_id": membership_id},
                )
            ).scalar()
            or 0
        )

    fsrs_due = 0
    if "revision_schedules" in present:
        fsrs_due = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM revision_schedules "
                        "WHERE tenant_id = :tenant_id AND membership_id = :membership_id AND due_at <= :now"
                    ),
                    {"tenant_id": tenant_id, "membership_id": membership_id, "now": now},
                )
            ).scalar()
            or 0
        )

    mastery_items: list[ChapterMasterySummary] = []
    if {"mastery_estimates", "taxonomy_nodes"}.issubset(present):
        mastery_rows = (
            await session.execute(
                text(
                    """
                    SELECT tn.code AS chapter_code, tn.code AS chapter_name, me.mastery_probability AS mastery_prob
                    FROM mastery_estimates me
                    JOIN taxonomy_nodes tn ON tn.id = me.taxonomy_node_id
                    WHERE me.tenant_id = :tenant_id AND me.membership_id = :membership_id
                    ORDER BY tn.code ASC
                    """
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).all()
        for row in mastery_rows:
            mastery_items.append(
                ChapterMasterySummary(
                    chapter_code=str(row.chapter_code),
                    chapter_name=str(row.chapter_name),
                    mastery_estimate=Decimal(str(row.mastery_prob)).quantize(Decimal("0.0001")),
                )
            )

    return lessons, assignments, open_error_notes_count, fsrs_due, mastery_items


async def recompute_learner_dashboard_projection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    now: datetime | None = None,
    in_progress_lessons: Sequence[InProgressLessonRef] | None = None,
    upcoming_assignments: Sequence[UpcomingAssignmentRef] | None = None,
    open_error_notes_count: int | None = None,
    fsrs_cards_due_today: int | None = None,
    chapter_mastery: Sequence[ChapterMasterySummary] | None = None,
) -> StoredDashboardProjectionView:
    """Compile and upsert `learner_dashboard_projections` using D01 pure-core builder."""
    resolved_now = _ensure_aware(now)

    readable_activity = await _readable_tables(session, ("activity_events",))
    if "activity_events" in readable_activity:
        activity_timestamps = (
            await session.execute(
                select(ActivityEvent.occurred_at).where(
                    ActivityEvent.tenant_id == tenant_id,
                    ActivityEvent.membership_id == membership_id,
                )
            )
        ).scalars().all()
        streak_days = _compute_streak_days(activity_timestamps, now=resolved_now)
        activity_event_count = len(activity_timestamps)
    else:
        existing_proj = (
            await session.execute(
                select(LearnerDashboardProjectionModel).where(
                    LearnerDashboardProjectionModel.tenant_id == tenant_id,
                    LearnerDashboardProjectionModel.membership_id == membership_id,
                )
            )
        ).scalar_one_or_none()
        streak_days = existing_proj.streak_days if existing_proj is not None else 0
        activity_event_count = (
            int(existing_proj.summary_json.get("activity_event_count", 0))
            if existing_proj is not None
            else 0
        )

    (
        live_lessons,
        live_assignments,
        live_errors,
        live_fsrs_due,
        live_mastery,
    ) = await _gather_live_signals(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        now=resolved_now,
    )

    try:
        built = build_learner_dashboard_projection(
            tenant_id=str(tenant_id),
            membership_id=str(membership_id),
            in_progress_lessons=live_lessons if in_progress_lessons is None else in_progress_lessons,
            upcoming_assignments=live_assignments if upcoming_assignments is None else upcoming_assignments,
            open_error_notes_count=live_errors if open_error_notes_count is None else open_error_notes_count,
            fsrs_cards_due_today=live_fsrs_due if fsrs_cards_due_today is None else fsrs_cards_due_today,
            chapter_mastery=live_mastery if chapter_mastery is None else chapter_mastery,
            now=resolved_now,
        )
    except DashboardError as exc:
        raise DashboardValidationError(str(exc)) from exc

    summary_payload = _serialize_projection(built)
    summary_payload["activity_event_count"] = activity_event_count

    upsert_stmt = (
        pg_insert(LearnerDashboardProjectionModel)
        .values(
            membership_id=membership_id,
            tenant_id=tenant_id,
            streak_days=streak_days,
            summary_json=summary_payload,
            updated_at=resolved_now,
        )
        .on_conflict_do_update(
            index_elements=[LearnerDashboardProjectionModel.membership_id],
            set_={
                "streak_days": streak_days,
                "summary_json": summary_payload,
                "updated_at": resolved_now,
            },
        )
        .returning(LearnerDashboardProjectionModel)
        .execution_options(populate_existing=True)
    )
    row = (await session.execute(upsert_stmt)).scalar_one()
    await session.flush()
    return StoredDashboardProjectionView(
        membership_id=row.membership_id,
        tenant_id=row.tenant_id,
        streak_days=row.streak_days,
        summary_json=dict(row.summary_json),
        updated_at=row.updated_at,
    )


async def get_learner_dashboard(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    refresh: bool = False,
    now: datetime | None = None,
) -> StoredDashboardProjectionView:
    """Return cached projection or recompute it on demand."""
    if not refresh:
        existing = (
            await session.execute(
                select(LearnerDashboardProjectionModel).where(
                    LearnerDashboardProjectionModel.tenant_id == tenant_id,
                    LearnerDashboardProjectionModel.membership_id == membership_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return StoredDashboardProjectionView(
                membership_id=existing.membership_id,
                tenant_id=existing.tenant_id,
                streak_days=existing.streak_days,
                summary_json=dict(existing.summary_json),
                updated_at=existing.updated_at,
            )

    return await recompute_learner_dashboard_projection(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        now=now,
    )
