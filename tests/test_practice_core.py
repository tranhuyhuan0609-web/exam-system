"""Pure unit tests for D03 Practice Engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.practice.public import (
    PracticeEngineError,
    PracticeItemSpec,
    PracticeMode,
    PracticeSessionState,
    PracticeSessionStatus,
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
