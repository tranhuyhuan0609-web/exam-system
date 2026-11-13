"""Pure unit tests for D01 Learner Dashboard Projection & D03 Practice Engine (P32)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.practice.public import (
    ChapterMasterySummary,
    InProgressLessonRef,
    PracticeEngineError,
    PracticeItemSpec,
    PracticeMode,
    PracticeSessionState,
    PracticeSessionStatus,
    UpcomingAssignmentRef,
    build_learner_dashboard_projection,
    complete_practice_session,
    reveal_next_hint,
    submit_practice_response,
)


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (PracticeMode, PracticeSessionStatus):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_immediate_feedback_progressive_hints_and_signals() -> None:
    session = PracticeSessionState(
        session_id="ps-1",
        tenant_id="tenant-1",
        membership_id="mem-1",
        mode=PracticeMode.LESSON_EMBED,
        immediate_feedback=True,
        items=(
            PracticeItemSpec(
                question_version_id="qv-1",
                taxonomy_node_id="MATH.PROB.BAYES",
                expected_answer="B",
                explanation_md="Áp dụng P(A|B) = P(B|A)P(A)/P(B).",
                hints=("Gợi ý 1: Viết xác suất toàn phần ở mẫu số.", "Gợi ý 2: P(B) = 0.45."),
            ),
            PracticeItemSpec(
                question_version_id="qv-2",
                taxonomy_node_id="MATH.CALC.DERIV",
                expected_answer="12",
                explanation_md="Đạo hàm f'(x)=3x^2 => f'(2)=12.",
            ),
        ),
    )

    session, h1, count1 = reveal_next_hint(session, question_version_id="qv-1")
    assert h1 == "Gợi ý 1: Viết xác suất toàn phần ở mẫu số."
    assert count1 == 1

    # Immediate feedback unlocks explanation right away
    session, rec1 = submit_practice_response(
        session, response_id="r-1", question_version_id="qv-1", submitted_answer="b"
    )
    assert rec1.is_correct is True
    assert rec1.hints_used == 1
    assert rec1.explanation_md is not None

    # Idempotent replay returns identical record without duplicating
    session_replay, rec1_replay = submit_practice_response(
        session, response_id="r-1", question_version_id="qv-1", submitted_answer="A"
    )
    assert session_replay == session
    assert rec1_replay == rec1

    session, rec2 = submit_practice_response(
        session, response_id="r-2", question_version_id="qv-2", submitted_answer="10"
    )
    assert rec2.is_correct is False

    report = complete_practice_session(session)
    assert report.session_state.status == PracticeSessionStatus.COMPLETED
    assert report.correct_count == 1
    assert report.accuracy == 0.5
    assert report.error_note_question_ids == ("qv-2",)
    assert all(sig.source_kind == "PRACTICE" for sig in report.mastery_signals)

    with pytest.raises(PracticeEngineError) as err:
        reveal_next_hint(report.session_state, question_version_id="qv-1")
    assert err.value.code == "SESSION_ALREADY_COMPLETED"


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
