"""Pure unit tests for F03 Gradebook Analytics (module gradebook)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.gradebook.public import (
    ItemResponseObservation,
    StudentAttemptGrade,
    compute_class_analytics,
    render_gradebook_csv,
)


def test_historical_inactive_grades_stay_in_gradebook_export() -> None:
    t0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
    grades = [
        StudentAttemptGrade("mem-alice", "Alice", 8.5, t0 - timedelta(days=2), t0, True),
        StudentAttemptGrade("mem-bob", "Bob", 9.0, t0 - timedelta(days=1), t0, False),
    ]
    csv_out = render_gradebook_csv(grades)
    assert "mem-alice,Alice,8.50" in csv_out
    assert "mem-bob,Bob,9.00" in csv_out


def test_class_analytics_identifies_top_5_hardest_and_top_distractor() -> None:
    t_due = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    grades = [
        StudentAttemptGrade("m1", "S1", 9.0, t_due - timedelta(hours=1), t_due),
        StudentAttemptGrade("m2", "S2", 5.5, t_due - timedelta(minutes=5), t_due),
        StudentAttemptGrade("m3", "S3", 3.5, t_due + timedelta(hours=2), t_due),
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
    assert hardest.error_rate == 0.8
    assert hardest.top_wrong_distractor == "C"
    assert hardest.top_wrong_distractor_count == 3
