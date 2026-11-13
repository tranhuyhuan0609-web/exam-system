"""F03 Classroom Gradebook summary, on-time metrics, and distractor item analysis."""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

_FOUR_PLACES = Decimal("0.0001")
_TWO_PLACES = Decimal("0.01")
_ZERO = Decimal("0")
_TEN = Decimal("10")
# Cells that a spreadsheet may interpret as a formula (including full-width variants and control characters).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")


class GradebookError(ValueError):
    """Raised when a gradebook input is not trustworthy (non-finite score, naive timestamp, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StudentAttemptGrade:
    membership_id: str
    student_label: str
    score: Decimal
    submitted_at: datetime | None
    due_at: datetime | None
    is_historical_inactive_member: bool = False


@dataclass(frozen=True, slots=True)
class ItemResponseObservation:
    question_id: str
    is_correct: bool
    selected_option: str | None = None


@dataclass(frozen=True, slots=True)
class QuestionDistractorReport:
    question_id: str
    attempt_count: int
    wrong_count: int
    error_rate: Decimal
    top_wrong_distractor: str | None
    top_wrong_distractor_count: int


@dataclass(frozen=True, slots=True)
class ClassAnalyticsSummary:
    total_submissions: int
    on_time_submissions: int
    on_time_rate: Decimal
    mean_score: Decimal
    min_score: Decimal
    max_score: Decimal
    score_histogram_2pt_bins: dict[str, int]
    top_5_hardest_questions: tuple[QuestionDistractorReport, ...]


_BIN_EDGES: tuple[tuple[Decimal, str], ...] = (
    (Decimal("2"), "[0, 2)"),
    (Decimal("4"), "[2, 4)"),
    (Decimal("6"), "[4, 6)"),
    (Decimal("8"), "[6, 8)"),
)


def _score_bin_label(score: Decimal) -> str:
    for upper, label in _BIN_EDGES:
        if score < upper:
            return label
    return "[8, 10]"


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return _ZERO
    return (Decimal(numerator) / Decimal(denominator)).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


def _checked_score(grade: StudentAttemptGrade) -> Decimal:
    score = grade.score
    if isinstance(score, bool) or not isinstance(score, (Decimal, int)):
        raise GradebookError("INVALID_SCORE", "score must be a Decimal (floats are not accepted)")
    value = Decimal(score)
    if not value.is_finite() or value < _ZERO or value > _TEN:
        raise GradebookError("INVALID_SCORE", "score must be a finite number between 0 and 10")
    return value


def _check_timestamps(grade: StudentAttemptGrade) -> None:
    for stamp in (grade.submitted_at, grade.due_at):
        if stamp is not None and stamp.tzinfo is None:
            raise GradebookError("NAIVE_TIMESTAMP", "submitted_at and due_at must be timezone-aware")


def compute_class_analytics(
    *,
    grades: Sequence[StudentAttemptGrade],
    item_observations: Sequence[ItemResponseObservation],
) -> ClassAnalyticsSummary:
    """
    Compute F03 class gradebook summary (preserving inactive leavers' historical grades)
    plus Top 5 hardest questions & their most confusing wrong distractors.

    Only submitted grades (`submitted_at` set) contribute to the score statistics and the histogram.
    """
    submitted: list[tuple[StudentAttemptGrade, Decimal]] = []
    for grade in grades:
        _check_timestamps(grade)
        score = _checked_score(grade)
        if grade.submitted_at is not None:
            submitted.append((grade, score))

    scores = [score for _, score in submitted]
    total_subs = len(submitted)
    on_time = sum(1 for g, _ in submitted if g.due_at is None or g.submitted_at <= g.due_at)  # type: ignore[operator]

    bins: dict[str, int] = {"[0, 2)": 0, "[2, 4)": 0, "[4, 6)": 0, "[6, 8)": 0, "[8, 10]": 0}
    for score in scores:
        bins[_score_bin_label(score)] += 1

    by_q: dict[str, list[ItemResponseObservation]] = {}
    for obs in item_observations:
        by_q.setdefault(obs.question_id, []).append(obs)

    item_reports: list[QuestionDistractorReport] = []
    for qid, obs_list in by_q.items():
        total_q = len(obs_list)
        wrong_list = [o for o in obs_list if not o.is_correct]
        wrong_cnt = len(wrong_list)
        distractor_counts = Counter(
            o.selected_option.strip() for o in wrong_list if o.selected_option is not None and o.selected_option.strip()
        )
        top_opt: str | None = None
        top_opt_cnt = 0
        if distractor_counts:
            # most_common is insertion ordered on ties; make ties deterministic by label
            top_opt, top_opt_cnt = min(distractor_counts.items(), key=lambda kv: (-kv[1], kv[0]))

        item_reports.append(
            QuestionDistractorReport(
                question_id=qid,
                attempt_count=total_q,
                wrong_count=wrong_cnt,
                error_rate=_ratio(wrong_cnt, total_q),
                top_wrong_distractor=top_opt,
                top_wrong_distractor_count=top_opt_cnt,
            )
        )

    item_reports.sort(key=lambda r: (-r.error_rate, -r.wrong_count, r.question_id))
    mean = (
        (sum(scores, _ZERO) / Decimal(len(scores))).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP) if scores else _ZERO
    )
    return ClassAnalyticsSummary(
        total_submissions=total_subs,
        on_time_submissions=on_time,
        on_time_rate=_ratio(on_time, total_subs),
        mean_score=mean,
        min_score=min(scores) if scores else _ZERO,
        max_score=max(scores) if scores else _ZERO,
        score_histogram_2pt_bins=bins,
        top_5_hardest_questions=tuple(item_reports[:5]),
    )


def _csv_safe(cell: str) -> str:
    """Neutralise spreadsheet formula injection by prefixing risky cells with an apostrophe."""
    text = str(cell)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def render_gradebook_csv(grades: Sequence[StudentAttemptGrade]) -> str:
    """Serialize gradebook rows with RFC-4180 escaping and formula-injection protection."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["membership_id", "student_label", "score", "submitted_at", "historical_inactive"])
    for g in grades:
        _check_timestamps(g)
        score = _checked_score(g)
        writer.writerow(
            [
                _csv_safe(g.membership_id),
                _csv_safe(g.student_label),
                f"{score.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)}" if g.submitted_at else "",
                g.submitted_at.isoformat() if g.submitted_at else "",
                "true" if g.is_historical_inactive_member else "false",
            ]
        )
    return buf.getvalue()
