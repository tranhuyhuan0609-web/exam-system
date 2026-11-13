"""Pure unit tests for D01 Learner Dashboard Projection (module dashboard)."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.dashboard.public import (
    ChapterMasterySummary,
    DashboardError,
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
            InProgressLessonRef("c1", "Bài 1", Decimal("0.4"), datetime(2026, 11, 12, 8, 0, tzinfo=UTC)),
            InProgressLessonRef("c2", "Bài 2", Decimal("0.8"), datetime(2026, 11, 13, 9, 0, tzinfo=UTC)),
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
            ChapterMasterySummary("CH2", "Tổ hợp Xác suất", Decimal("0.60")),
            ChapterMasterySummary("CH1", "Hàm số", Decimal("0.80")),
        ],
        now=now,
    )
    assert [lesson.content_item_id for lesson in proj.recent_lessons] == ["c2", "c1"]
    assert [a.assignment_id for a in proj.upcoming_assignments] == ["a-soon", "a-later"]
    assert proj.open_error_notes_count == 4
    assert proj.fsrs_cards_due_today == 15
    assert proj.overall_mastery_avg == Decimal("0.7")


_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _build(**kw: object):
    args: dict[str, object] = {
        "tenant_id": "t",
        "membership_id": "m",
        "in_progress_lessons": [],
        "upcoming_assignments": [],
        "open_error_notes_count": 0,
        "fsrs_cards_due_today": 0,
        "chapter_mastery": [],
        "now": _NOW,
    }
    args.update(kw)
    return build_learner_dashboard_projection(**args)  # type: ignore[arg-type]


def test_empty_dashboard_is_zeroed() -> None:
    proj = _build()
    assert proj.recent_lessons == () and proj.upcoming_assignments == () and proj.chapter_mastery == ()
    assert proj.overall_mastery_avg == Decimal("0")


def test_recent_lessons_are_limited_and_sorted_newest_first() -> None:
    lessons = [InProgressLessonRef(f"c{i}", "t", Decimal("0.1"), _NOW - timedelta(hours=i)) for i in range(8)]
    assert [x.content_item_id for x in _build(in_progress_lessons=lessons).recent_lessons] == [
        "c0",
        "c1",
        "c2",
        "c3",
        "c4",
    ]
    assert _build(in_progress_lessons=lessons, max_recent_lessons=0).recent_lessons == ()
    assert len(_build(in_progress_lessons=lessons, max_recent_lessons=100).recent_lessons) == 8
    for bad in (-1, True, 2.5):
        with pytest.raises(DashboardError) as exc:
            _build(max_recent_lessons=bad)
        assert exc.value.code == "INVALID_LIMIT"


def test_assignment_due_exactly_now_is_still_listed_and_exhausted_ones_are_not() -> None:
    assignments = [
        UpcomingAssignmentRef("due-now", "x", _NOW, 0, 1),
        UpcomingAssignmentRef("past", "x", _NOW - timedelta(seconds=1), 0, 1),
        UpcomingAssignmentRef("used-up", "x", _NOW + timedelta(days=1), 2, 2),
        UpcomingAssignmentRef("over-used", "x", _NOW + timedelta(days=1), 5, 2),
    ]
    assert [a.assignment_id for a in _build(upcoming_assignments=assignments).upcoming_assignments] == ["due-now"]


def test_negative_counts_are_clamped_to_zero() -> None:
    proj = _build(open_error_notes_count=-3, fsrs_cards_due_today=-1)
    assert proj.open_error_notes_count == 0 and proj.fsrs_cards_due_today == 0


def test_mastery_is_decimal_sorted_by_chapter_and_averaged_without_float_drift() -> None:
    chapters = [
        ChapterMasterySummary("CH3", "c", Decimal("0.1")),
        ChapterMasterySummary("CH1", "a", Decimal("0.2")),
        ChapterMasterySummary("CH2", "b", Decimal("0.3")),
    ]
    proj = _build(chapter_mastery=chapters)
    assert [c.chapter_code for c in proj.chapter_mastery] == ["CH1", "CH2", "CH3"]
    assert proj.overall_mastery_avg == Decimal("0.2000")
    third = _build(
        chapter_mastery=[ChapterMasterySummary(f"C{i}", "n", Decimal(v)) for i, v in enumerate(["1", "0", "0"])]
    )
    assert third.overall_mastery_avg == Decimal("0.3333")
    assert isinstance(proj.overall_mastery_avg, Decimal)


def test_non_finite_out_of_range_or_float_ratios_are_rejected() -> None:
    bad_values = [
        float("nan"),
        float("inf"),
        0.5,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-0.1"),
        Decimal("1.1"),
        True,
        None,
    ]
    for bad in bad_values:
        with pytest.raises(DashboardError) as exc:
            _build(chapter_mastery=[ChapterMasterySummary("C", "n", bad)])  # type: ignore[arg-type]
        assert exc.value.code == "INVALID_RATIO", bad
        with pytest.raises(DashboardError):
            _build(in_progress_lessons=[InProgressLessonRef("c", "t", bad, _NOW)])  # type: ignore[arg-type]
    _build(chapter_mastery=[ChapterMasterySummary("C", "n", Decimal("0")), ChapterMasterySummary("D", "n", 1)])


def test_naive_datetimes_are_rejected() -> None:
    naive = datetime(2026, 11, 14, 12, 0)
    with pytest.raises(DashboardError):
        _build(now=naive)
    with pytest.raises(DashboardError):
        _build(upcoming_assignments=[UpcomingAssignmentRef("a", "x", naive, 0, 1)])
    with pytest.raises(DashboardError):
        _build(in_progress_lessons=[InProgressLessonRef("c", "t", Decimal("0.5"), naive)])


def test_inputs_are_not_mutated_and_vietnamese_titles_are_preserved() -> None:
    title = unicodedata.normalize("NFD", "Đạo hàm")
    lessons = [InProgressLessonRef("c", title, Decimal("0.5"), _NOW)]
    before = list(lessons)
    proj = _build(in_progress_lessons=lessons)
    assert lessons == before and proj.recent_lessons[0].title == title
