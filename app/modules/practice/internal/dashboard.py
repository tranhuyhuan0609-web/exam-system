"""D01 Learner Dashboard Projection builder from multi-module read signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class InProgressLessonRef:
    content_item_id: str
    title: str
    completion_pct: float
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
    mastery_estimate: float  # 0.0 .. 1.0


@dataclass(frozen=True, slots=True)
class LearnerDashboardProjection:
    tenant_id: str
    membership_id: str
    recent_lessons: tuple[InProgressLessonRef, ...]
    upcoming_assignments: tuple[UpcomingAssignmentRef, ...]
    open_error_notes_count: int
    fsrs_cards_due_today: int
    chapter_mastery: tuple[ChapterMasterySummary, ...]
    overall_mastery_avg: float


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
    sorted_lessons = tuple(
        sorted(in_progress_lessons, key=lambda lesson: lesson.last_accessed_at, reverse=True)[
            :max_recent_lessons
        ]
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
        round(sum(c.mastery_estimate for c in sorted_mastery) / len(sorted_mastery), 4)
        if sorted_mastery
        else 0.0
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
