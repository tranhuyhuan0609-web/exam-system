"""F03 Classroom Gradebook summary, on-time metrics, and distractor item analysis."""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StudentAttemptGrade:
    membership_id: str
    student_label: str
    score: float
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
    error_rate: float
    top_wrong_distractor: str | None
    top_wrong_distractor_count: int


@dataclass(frozen=True, slots=True)
class ClassAnalyticsSummary:
    total_submissions: int
    on_time_submissions: int
    on_time_rate: float
    mean_score: float
    min_score: float
    max_score: float
    score_histogram_2pt_bins: dict[str, int]
    top_5_hardest_questions: tuple[QuestionDistractorReport, ...]


def _score_bin_label(score: float) -> str:
    if score < 2.0:
        return "[0, 2)"
    if score < 4.0:
        return "[2, 4)"
    if score < 6.0:
        return "[4, 6)"
    if score < 8.0:
        return "[6, 8)"
    return "[8, 10]"


def compute_class_analytics(
    *,
    grades: Sequence[StudentAttemptGrade],
    item_observations: Sequence[ItemResponseObservation],
) -> ClassAnalyticsSummary:
    """
    Compute F03 class gradebook summary (preserving inactive leavers' historical grades)
    plus Top 5 hardest questions & their most confusing wrong distractors.
    """
    scores = [g.score for g in grades]
    total_subs = sum(1 for g in grades if g.submitted_at is not None)
    on_time = sum(
        1
        for g in grades
        if g.submitted_at is not None and (g.due_at is None or g.submitted_at <= g.due_at)
    )
    on_time_rate = round(on_time / total_subs, 4) if total_subs > 0 else 0.0

    bins: dict[str, int] = {"[0, 2)": 0, "[2, 4)": 0, "[4, 6)": 0, "[6, 8)": 0, "[8, 10]": 0}
    for s in scores:
        bins[_score_bin_label(s)] += 1

    # Item & distractor aggregation
    by_q: dict[str, list[ItemResponseObservation]] = {}
    for obs in item_observations:
        by_q.setdefault(obs.question_id, []).append(obs)

    item_reports: list[QuestionDistractorReport] = []
    for qid, obs_list in by_q.items():
        total_q = len(obs_list)
        wrong_list = [o for o in obs_list if not o.is_correct]
        wrong_cnt = len(wrong_list)
        distractor_counts = Counter(
            o.selected_option.strip()
            for o in wrong_list
            if o.selected_option is not None and o.selected_option.strip()
        )
        top_opt: str | None = None
        top_opt_cnt = 0
        if distractor_counts:
            top_opt, top_opt_cnt = distractor_counts.most_common(1)[0]

        item_reports.append(
            QuestionDistractorReport(
                question_id=qid,
                attempt_count=total_q,
                wrong_count=wrong_cnt,
                error_rate=round(wrong_cnt / total_q, 4) if total_q > 0 else 0.0,
                top_wrong_distractor=top_opt,
                top_wrong_distractor_count=top_opt_cnt,
            )
        )

    item_reports.sort(key=lambda r: (-r.error_rate, -r.wrong_count, r.question_id))
    return ClassAnalyticsSummary(
        total_submissions=total_subs,
        on_time_submissions=on_time,
        on_time_rate=on_time_rate,
        mean_score=round(sum(scores) / len(scores), 4) if scores else 0.0,
        min_score=min(scores) if scores else 0.0,
        max_score=max(scores) if scores else 0.0,
        score_histogram_2pt_bins=bins,
        top_5_hardest_questions=tuple(item_reports[:5]),
    )


def render_gradebook_csv(grades: Sequence[StudentAttemptGrade]) -> str:
    """Serialize gradebook rows cleanly with RFC-4180 CSV escaping."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["membership_id", "student_label", "score", "submitted_at", "historical_inactive"])
    for g in grades:
        writer.writerow(
            [
                g.membership_id,
                g.student_label,
                f"{g.score:.2f}",
                g.submitted_at.isoformat() if g.submitted_at else "",
                "true" if g.is_historical_inactive_member else "false",
            ]
        )
    return buf.getvalue()
