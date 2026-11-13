"""Pure unit tests for D03 Practice Engine."""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.practice.public import (
    MasterySignal,
    PracticeEngineError,
    PracticeItemSpec,
    PracticeMode,
    PracticeSessionState,
    PracticeSessionStatus,
    aggregate_signal_summary,
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
        session, response_id="r-1", question_version_id="qv-1", submitted_answer="b"
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


def _session(
    *, feedback: bool = True, mode: PracticeMode = PracticeMode.TOPIC_DRILL, expected: str = "B"
) -> PracticeSessionState:
    return PracticeSessionState(
        session_id="ps",
        tenant_id="t",
        membership_id="m",
        mode=mode,
        immediate_feedback=feedback,
        items=(
            PracticeItemSpec("qv-1", "NODE.1", expected, "Giải thích", ("h1", "h2")),
            PracticeItemSpec("qv-2", None, "0,5", "Giải thích 2"),
        ),
    )


def _answer(state: PracticeSessionState, rid: str, qid: str, text: str):
    return submit_practice_response(state, response_id=rid, question_version_id=qid, submitted_answer=text)


def _err(action) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(PracticeEngineError) as exc:
        action()
    return exc.value.code


def test_without_immediate_feedback_the_record_hides_correctness_and_explanation() -> None:
    state, record = _answer(_session(feedback=False), "r1", "qv-1", "B")
    assert record.is_correct is None
    assert record.explanation_md is None
    # ... but completion still grades it from server-side state
    report = complete_practice_session(state)
    assert report.correct_count == 1 and report.accuracy == Decimal("1")
    assert report.mastery_signals[0].is_correct is True
    # the learner-visible response list never exposes the outcome either
    assert all(r.is_correct is None for r in state.responses)


def test_with_immediate_feedback_the_record_exposes_correctness_and_explanation() -> None:
    _, right = _answer(_session(), "r1", "qv-1", "B")
    _, wrong = _answer(_session(), "r1", "qv-1", "C")
    assert right.is_correct is True and right.explanation_md == "Giải thích"
    assert wrong.is_correct is False and wrong.explanation_md == "Giải thích"


def test_numeric_answers_match_by_value_across_decimal_separators() -> None:
    for text in ("0,5", "0.5", " 0,50 ", "+0.5", "0.500"):
        assert _answer(_session(), "r", "qv-2", text)[1].is_correct is True, text
    for text in ("0,51", "5", "-0,5", "0,5đ", "", "abc", "1/2"):
        assert _answer(_session(), "r", "qv-2", text)[1].is_correct is False, text
    # a numeric expected value never matches a non-numeric answer, even with equal text collapse
    assert _answer(_session(expected="12"), "r", "qv-1", "12,0")[1].is_correct is True
    assert _answer(_session(expected="-1,5"), "r", "qv-1", "-1.5")[1].is_correct is True
    assert _answer(_session(expected="1.000"), "r", "qv-1", "1000")[1].is_correct is False


def test_text_answers_ignore_case_whitespace_and_unicode_form() -> None:
    expected = "Đạo hàm"
    for form in ("NFC", "NFD"):
        for variant in (expected, expected.upper(), f"  {expected}  ", "Đạo   hàm"):
            answer = unicodedata.normalize(form, variant)
            assert _answer(_session(expected=expected), "r", "qv-1", answer)[1].is_correct is True, (form, variant)
    assert _answer(_session(expected=expected), "r", "qv-1", "Dao ham")[1].is_correct is False
    assert _answer(_session(expected=""), "r", "qv-1", "")[1].is_correct is False  # empty never "matches"


def test_response_replay_is_idempotent_only_for_identical_content() -> None:
    state, first = _answer(_session(), "r1", "qv-1", "B")
    same_state, again = _answer(state, "r1", "qv-1", "B")
    assert again == first and same_state is state
    assert _err(lambda: _answer(state, "r1", "qv-1", "C")) == "RESPONSE_ID_CONFLICT"
    assert _err(lambda: _answer(state, "r1", "qv-2", "B")) == "RESPONSE_ID_CONFLICT"


def test_response_input_validation_and_session_state_guards() -> None:
    session = _session()
    for bad_id in ("", "   ", None):
        assert _err(lambda b=bad_id: _answer(session, b, "qv-1", "B")) == "MISSING_RESPONSE_ID"  # type: ignore[arg-type]
    assert _err(lambda: _answer(session, "r", "qv-1", None)) == "INVALID_ANSWER"  # type: ignore[arg-type]
    assert _err(lambda: _answer(session, "r", "qv-unknown", "B")) == "ITEM_NOT_IN_SESSION"
    completed = complete_practice_session(session).session_state
    assert _err(lambda: _answer(completed, "r", "qv-1", "B")) == "SESSION_ALREADY_COMPLETED"
    # replaying an already-applied response on a completed session stays idempotent
    state, _ = _answer(session, "r9", "qv-1", "B")
    done = complete_practice_session(state).session_state
    assert _answer(done, "r9", "qv-1", "B")[1].response_id == "r9"


def test_hints_unlock_in_order_and_stop_at_the_last_hint() -> None:
    state = _session()
    state, h1, n1 = reveal_next_hint(state, question_version_id="qv-1")
    state, h2, n2 = reveal_next_hint(state, question_version_id="qv-1")
    same, h3, n3 = reveal_next_hint(state, question_version_id="qv-1")
    assert (h1, n1, h2, n2, h3, n3) == ("h1", 1, "h2", 2, None, 2) and same is state
    # an item without hints never unlocks anything
    _, none_hint, count = reveal_next_hint(state, question_version_id="qv-2")
    assert none_hint is None and count == 0
    assert _err(lambda: reveal_next_hint(state, question_version_id="nope")) == "ITEM_NOT_IN_SESSION"
    # hints used are frozen into the record at answer time
    _, record = _answer(state, "r", "qv-1", "B")
    assert record.hints_used == 2


def test_completion_uses_latest_response_and_flags_errors_and_remediation() -> None:
    state = _session(mode=PracticeMode.REMEDIATION_DRILL)
    state, _ = _answer(state, "r1", "qv-1", "wrong")
    state, _ = _answer(state, "r2", "qv-1", "B")  # retry, correct, no hints
    state, _ = _answer(state, "r3", "qv-2", "9")
    report = complete_practice_session(state)
    assert report.total_answered == 2 and report.correct_count == 1
    assert report.accuracy == Decimal("0.5")
    assert report.error_note_question_ids == ("qv-2",)
    assert report.resolved_remediation_question_ids == ("qv-1",)
    # using a hint before answering correctly does not count as remediated
    hinted = _session(mode=PracticeMode.REMEDIATION_DRILL)
    hinted, _, _ = reveal_next_hint(hinted, question_version_id="qv-1")
    hinted, _ = _answer(hinted, "r", "qv-1", "B")
    assert complete_practice_session(hinted).resolved_remediation_question_ids == ()
    # completion is idempotent and does not mutate the input
    assert complete_practice_session(report.session_state) == report
    assert state.status == PracticeSessionStatus.IN_PROGRESS


def test_completion_of_empty_session_and_accuracy_rounding() -> None:
    empty = complete_practice_session(_session())
    assert empty.total_answered == 0 and empty.accuracy == Decimal("0")
    state = _session()
    state = replace(state, items=state.items + (PracticeItemSpec("qv-3", None, "x", ""),))
    state, _ = _answer(state, "a", "qv-1", "B")
    state, _ = _answer(state, "b", "qv-2", "9")
    state, _ = _answer(state, "c", "qv-3", "9")
    assert complete_practice_session(state).accuracy == Decimal("0.3333")
    assert isinstance(complete_practice_session(state).accuracy, Decimal)


def test_signal_summary_groups_by_taxonomy_node() -> None:
    signals = (
        MasterySignal("N1", "q1", True, 0),
        MasterySignal("N1", "q2", False, 2),
        MasterySignal(None, "q3", True, 1),
    )
    summary = aggregate_signal_summary(signals)
    assert summary == {
        "N1": {"total": 2, "correct": 1, "hinted": 1},
        "UNASSIGNED": {"total": 1, "correct": 1, "hinted": 1},
    }
    assert aggregate_signal_summary(()) == {}
    assert all(s.source_kind == "PRACTICE" for s in signals)
