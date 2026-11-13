"""Pure unit tests for F03 Gradebook Analytics (module gradebook)."""

from __future__ import annotations

import csv
import io
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.modules.gradebook.public import (
    GradebookError,
    ItemResponseObservation,
    StudentAttemptGrade,
    compute_class_analytics,
    render_gradebook_csv,
)


def test_historical_inactive_grades_stay_in_gradebook_export() -> None:
    t0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    grades = [
        StudentAttemptGrade("mem-alice", "Alice", Decimal("8.5"), t0 - timedelta(days=2), t0, True),
        StudentAttemptGrade("mem-bob", "Bob", Decimal("9"), t0 - timedelta(days=1), t0, False),
    ]
    csv_out = render_gradebook_csv(grades)
    assert "mem-alice,Alice,8.50" in csv_out
    assert "mem-bob,Bob,9.00" in csv_out


def test_class_analytics_identifies_top_5_hardest_and_top_distractor() -> None:
    t_due = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    grades = [
        StudentAttemptGrade("m1", "S1", Decimal("9"), t_due - timedelta(hours=1), t_due),
        StudentAttemptGrade("m2", "S2", Decimal("5.5"), t_due - timedelta(minutes=5), t_due),
        StudentAttemptGrade("m3", "S3", Decimal("3.5"), t_due + timedelta(hours=2), t_due),
    ]
    obs = []
    # q1: 4 wrong out of 5, distractor C chosen 3 times
    obs.extend([ItemResponseObservation("q1", False, "C")] * 3)
    obs.append(ItemResponseObservation("q1", False, "D"))
    obs.append(ItemResponseObservation("q1", True, "A"))
    # q2..q7: varying error rates
    for idx in range(2, 8):
        obs.extend([ItemResponseObservation(f"q{idx}", False, "B")] * (idx % 3))
        obs.extend([ItemResponseObservation(f"q{idx}", True, "A")] * 4)

    summary = compute_class_analytics(grades=grades, item_observations=obs)
    assert summary.total_submissions == 3
    assert summary.on_time_submissions == 2
    assert summary.score_histogram_2pt_bins["[8, 10]"] == 1
    assert len(summary.top_5_hardest_questions) == 5
    hardest = summary.top_5_hardest_questions[0]
    assert hardest.question_id == "q1"
    assert hardest.error_rate == Decimal("0.8")
    assert hardest.top_wrong_distractor == "C"
    assert hardest.top_wrong_distractor_count == 3


_T0 = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _g(
    score: object = "7",
    label: str = "S",
    submitted: datetime | None = _T0 - timedelta(hours=1),
    due: datetime | None = _T0,
    mid: str = "m1",
    inactive: bool = False,
) -> StudentAttemptGrade:
    value = Decimal(score) if isinstance(score, str) else score
    return StudentAttemptGrade(mid, label, value, submitted, due, inactive)  # type: ignore[arg-type]


def test_csv_formula_injection_is_neutralised_in_every_text_cell() -> None:
    risky = ['=HYPERLINK("http://evil")', "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1", "\n=1", "＝1+1"]
    for label in risky:
        out = render_gradebook_csv([_g(label=label, mid=label)])
        row = list(csv.reader(io.StringIO(out)))[1]
        assert row[0] == "'" + label and row[1] == "'" + label, label
    safe = render_gradebook_csv([_g(label="Nguyễn Văn A")])
    assert list(csv.reader(io.StringIO(safe)))[1][1] == "Nguyễn Văn A"
    # a dash or equals sign in the middle is harmless and untouched
    mid = render_gradebook_csv([_g(label="An-Binh = ok")])
    assert list(csv.reader(io.StringIO(mid)))[1][1] == "An-Binh = ok"


def test_csv_escapes_commas_quotes_and_newlines_and_keeps_vietnamese_forms() -> None:
    for form in ("NFC", "NFD"):
        label = unicodedata.normalize(form, 'Trần "Bảo", Lớp 11A\nTổ 1')
        out = render_gradebook_csv([_g(label=label)])
        assert list(csv.reader(io.StringIO(out, newline="")))[1][1] == label


def test_unsubmitted_grades_are_excluded_from_statistics_and_histogram() -> None:
    grades = [
        _g("9", mid="a"),
        _g("5", mid="b"),
        _g("0", submitted=None, mid="c"),
        _g("0", submitted=None, due=None, mid="d"),
    ]
    summary = compute_class_analytics(grades=grades, item_observations=[])
    assert summary.total_submissions == 2
    assert summary.mean_score == Decimal("7.0000")
    assert summary.min_score == Decimal("5")
    assert summary.max_score == Decimal("9")
    assert sum(summary.score_histogram_2pt_bins.values()) == 2
    assert summary.score_histogram_2pt_bins["[0, 2)"] == 0
    # the CSV keeps the row but exports no score for an unsubmitted attempt
    rows = list(csv.reader(io.StringIO(render_gradebook_csv(grades))))
    assert rows[3][2] == "" and rows[3][3] == ""


def test_scores_are_decimal_and_summary_has_no_float_drift() -> None:
    grades = [_g(s, mid=str(i)) for i, s in enumerate(["0.1", "0.2", "0.3"])]
    summary = compute_class_analytics(grades=grades, item_observations=[])
    assert summary.mean_score == Decimal("0.2000")
    assert isinstance(summary.mean_score, Decimal) and isinstance(summary.on_time_rate, Decimal)
    third = compute_class_analytics(grades=[_g("1", mid="1"), _g("0", mid="2"), _g("0", mid="3")], item_observations=[])
    assert third.mean_score == Decimal("0.3333")  # rounded half up to four places


def test_invalid_scores_and_naive_timestamps_fail_closed() -> None:
    bad_scores = [
        float("nan"),
        float("inf"),
        7.5,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-0.01"),
        Decimal("10.01"),
        -1,
        11,
        True,
        None,
    ]
    for bad in bad_scores:
        with pytest.raises(GradebookError) as exc:
            compute_class_analytics(grades=[_g(bad)], item_observations=[])
        assert exc.value.code == "INVALID_SCORE", bad
        with pytest.raises(GradebookError):
            render_gradebook_csv([_g(bad)])
    naive = datetime(2026, 11, 14, 11, 0)
    for grade in (_g(submitted=naive), _g(due=naive)):
        with pytest.raises(GradebookError) as exc:
            compute_class_analytics(grades=[grade], item_observations=[])
        assert exc.value.code == "NAIVE_TIMESTAMP"


def test_histogram_bin_boundaries_and_score_extremes() -> None:
    cases = {
        "0": "[0, 2)",
        "1.9999": "[0, 2)",
        "2": "[2, 4)",
        "3.99": "[2, 4)",
        "4": "[4, 6)",
        "6": "[6, 8)",
        "7.999": "[6, 8)",
        "8": "[8, 10]",
        "10": "[8, 10]",
    }
    for score, label in cases.items():
        summary = compute_class_analytics(grades=[_g(score)], item_observations=[])
        assert summary.score_histogram_2pt_bins[label] == 1, score
        assert sum(summary.score_histogram_2pt_bins.values()) == 1


def test_on_time_boundary_and_no_due_date() -> None:
    exactly = _g(submitted=_T0, due=_T0, mid="a")
    late = _g(submitted=_T0 + timedelta(seconds=1), due=_T0, mid="b")
    no_due = _g(submitted=_T0 + timedelta(days=9), due=None, mid="c")
    summary = compute_class_analytics(grades=[exactly, late, no_due], item_observations=[])
    assert (summary.total_submissions, summary.on_time_submissions) == (3, 2)
    assert summary.on_time_rate == Decimal("0.6667")


def test_empty_inputs_produce_a_zeroed_summary() -> None:
    summary = compute_class_analytics(grades=[], item_observations=[])
    assert summary.total_submissions == 0 and summary.on_time_rate == Decimal("0")
    assert summary.mean_score == summary.min_score == summary.max_score == Decimal("0")
    assert summary.top_5_hardest_questions == ()
    assert render_gradebook_csv([]).strip() == "membership_id,student_label,score,submitted_at,historical_inactive"


def test_distractor_analysis_ties_blank_options_and_ordering() -> None:
    obs = [
        ItemResponseObservation("q1", False, "B"),
        ItemResponseObservation("q1", False, "A"),
        ItemResponseObservation("q1", False, " "),
        ItemResponseObservation("q1", False, None),
        ItemResponseObservation("q2", True, "A"),
        ItemResponseObservation("q3", False, " C "),
    ]
    summary = compute_class_analytics(grades=[], item_observations=obs)
    by_id = {r.question_id: r for r in summary.top_5_hardest_questions}
    assert by_id["q1"].top_wrong_distractor == "A"  # tie broken alphabetically, not by arrival order
    assert by_id["q1"].top_wrong_distractor_count == 1
    assert by_id["q1"].error_rate == Decimal("1")
    assert by_id["q2"].error_rate == Decimal("0") and by_id["q2"].top_wrong_distractor is None
    assert by_id["q3"].top_wrong_distractor == "C"
    assert [r.question_id for r in summary.top_5_hardest_questions] == ["q1", "q3", "q2"]


def test_inputs_are_not_mutated() -> None:
    grades = [_g("5")]
    obs = [ItemResponseObservation("q", False, "A")]
    compute_class_analytics(grades=grades, item_observations=obs)
    render_gradebook_csv(grades)
    assert grades == [_g("5")] and obs == [ItemResponseObservation("q", False, "A")]
