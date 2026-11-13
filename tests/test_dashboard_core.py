"""Pure unit tests for D01 Learner Dashboard Projection (module dashboard)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.dashboard.public import (
    ChapterMasterySummary,
    InProgressLessonRef,
    UpcomingAssignmentRef,
    build_learner_dashboard_projection,
)


def test_dashboard_projection_orders_lessons_and_filters_expired_or_exhausted_assignments() -> None:
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    proj = build_learner_dashboard_projection(
        tenant_id="tenant-1",
        membership_id="mem-1",
        in_progress_lessons=[
            InProgressLessonRef("c1", "Bài 1", 0.4, datetime(2026, 11, 12, 8, 0, tzinfo=UTC)),
            InProgressLessonRef("c2", "Bài 2", 0.8, datetime(2026, 11, 13, 9, 0, tzinfo=UTC)),
        ],
        upcoming_assignments=[
            UpcomingAssignmentRef("a-past", "Đề quá hạn", datetime(2026, 11, 13, 10, 0, tzinfo=UTC), 0, 1),
            UpcomingAssignmentRef("a-done", "Đã hết lượt", datetime(2026, 11, 14, 10, 0, tzinfo=UTC), 1, 1),
            UpcomingAssignmentRef("a-later", "Đề tuần tới", datetime(2026, 11, 20, 10, 0, tzinfo=UTC), 0, 2),
            UpcomingAssignmentRef("a-soon", "Đề mai", datetime(2026, 11, 14, 8, 0, tzinfo=UTC), 0, 1),
        ],
        open_error_notes_count=4,
        fsrs_cards_due_today=15,
        chapter_mastery=[
            ChapterMasterySummary("CH2", "Tổ hợp Xác suất", 0.60),
            ChapterMasterySummary("CH1", "Hàm số", 0.80),
        ],
        now=now,
    )
    assert [lesson.content_item_id for lesson in proj.recent_lessons] == ["c2", "c1"]
    assert [a.assignment_id for a in proj.upcoming_assignments] == ["a-soon", "a-later"]
    assert proj.open_error_notes_count == 4
    assert proj.fsrs_cards_due_today == 15
    assert proj.overall_mastery_avg == 0.7
