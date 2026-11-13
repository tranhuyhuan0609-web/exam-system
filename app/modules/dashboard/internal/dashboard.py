"""D01 Learner Dashboard Projection builder from multi-module read signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal


class DashboardError(ValueError):
    """Raised when dashboard inputs are not trustworthy (non-finite ratios, naive clocks, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_ZERO = Decimal("0")
_ONE = Decimal("1")


def _unit_ratio(value: object, field_name: str) -> Decimal:
    """Accept only a finite Decimal/int in 0..1 (floats are rejected: scores are never floats)."""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise DashboardError("INVALID_RATIO", f"{field_name} must be a Decimal between 0 and 1")
    number = Decimal(value)
    if not number.is_finite() or number < _ZERO or number > _ONE:
        raise DashboardError("INVALID_RATIO", f"{field_name} must be a finite value between 0 and 1")
    return number


@dataclass(frozen=True, slots=True)
class InProgressLessonRef:
    content_item_id: str
    title: str
    completion_pct: Decimal  # 0..1
    last_accessed_at: datetime


@dataclass(frozen=True, slots=True)
class UpcomingAssignmentRef:
    assignment_id: str
    title: str
    due_at: datetime
    attempts_used: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ChapterMasterySummary:
    chapter_code: str
    chapter_name: str
    mastery_estimate: Decimal  # 0 .. 1


@dataclass(frozen=True, slots=True)
class LearnerDashboardProjection:
    tenant_id: str
    membership_id: str
    recent_lessons: tuple[InProgressLessonRef, ...]
    upcoming_assignments: tuple[UpcomingAssignmentRef, ...]
    open_error_notes_count: int
    fsrs_cards_due_today: int
    chapter_mastery: tuple[ChapterMasterySummary, ...]
    overall_mastery_avg: Decimal


def build_learner_dashboard_projection(
    *,
    tenant_id: str,
    membership_id: str,
    in_progress_lessons: Sequence[InProgressLessonRef],
    upcoming_assignments: Sequence[UpcomingAssignmentRef],
    open_error_notes_count: int,
    fsrs_cards_due_today: int,
    chapter_mastery: Sequence[ChapterMasterySummary],
    now: datetime,
    max_recent_lessons: int = 5,
) -> LearnerDashboardProjection:
    """Compile sorted D01 learner dashboard projection from learner activity & mastery snapshots."""
    if now.tzinfo is None:
        raise DashboardError("NAIVE_CLOCK", "now must be timezone-aware")
    if isinstance(max_recent_lessons, bool) or not isinstance(max_recent_lessons, int) or max_recent_lessons < 0:
        raise DashboardError("INVALID_LIMIT", "max_recent_lessons must be a non-negative integer")
    for lesson in in_progress_lessons:
        _unit_ratio(lesson.completion_pct, "completion_pct")
        if lesson.last_accessed_at.tzinfo is None:
            raise DashboardError("NAIVE_TIMESTAMP", "last_accessed_at must be timezone-aware")
    for assignment in upcoming_assignments:
        if assignment.due_at.tzinfo is None:
            raise DashboardError("NAIVE_TIMESTAMP", "due_at must be timezone-aware")
    for chapter in chapter_mastery:
        _unit_ratio(chapter.mastery_estimate, "mastery_estimate")
    sorted_lessons = tuple(
        sorted(in_progress_lessons, key=lambda lesson: lesson.last_accessed_at, reverse=True)[:max_recent_lessons]
    )
    # Only include assignments not yet exhausted on max_attempts and not past due
    active_assignments = tuple(
        sorted(
            (a for a in upcoming_assignments if a.due_at >= now and a.attempts_used < a.max_attempts),
            key=lambda a: a.due_at,
        )
    )
    sorted_mastery = tuple(sorted(chapter_mastery, key=lambda c: c.chapter_code))
    overall_avg = (
        (sum((Decimal(c.mastery_estimate) for c in sorted_mastery), _ZERO) / Decimal(len(sorted_mastery))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        if sorted_mastery
        else _ZERO
    )

    return LearnerDashboardProjection(
        tenant_id=tenant_id,
        membership_id=membership_id,
        recent_lessons=sorted_lessons,
        upcoming_assignments=active_assignments,
        open_error_notes_count=max(0, open_error_notes_count),
        fsrs_cards_due_today=max(0, fsrs_cards_due_today),
        chapter_mastery=sorted_mastery,
        overall_mastery_avg=overall_avg,
    )
