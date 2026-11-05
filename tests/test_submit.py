from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, update

from app.attempts import clean_answers, grade
from app.models import AttemptStatus, ExamAttempt
from tests.test_attempts import attempt_id_from, exam_id, start, student  # noqa: F401  (fixtures)

# Fixture paper: questions 1..3, correct choice ids "q{i}a", wrong ones "q{i}b".


def submit(client, attempt_id, answers):
    return client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": answers})


def question_ids(client, attempt_id) -> list[int]:
    return [q["id"] for q in client.get(f"/api/attempts/{attempt_id}/paper").json()["questions"]]


def test_grade_scores_on_a_ten_point_scale():
    snapshot = {
        "questions": [
            {"id": i, "choices": [{"id": "ok", "is_correct": True}, {"id": "no", "is_correct": False}]}
            for i in range(1, 4)
        ]
    }
    assert grade(snapshot, {"1": "ok", "2": "ok", "3": "ok"}).score == Decimal("10.00")
    result = grade(snapshot, {"1": "ok", "2": "no"})
    assert (result.correct_count, result.total, result.score) == (1, 3, Decimal("3.33"))
    assert grade(snapshot, {}).score == Decimal("0.00")
    # Unknown questions, unknown choices and non-dict payloads are dropped.
    assert clean_answers(snapshot, {"1": "ok", "2": "hack", "99": "ok", 3: "no"}) == {"1": "ok", "3": "no"}
    assert clean_answers(snapshot, ["ok"]) == {}


def test_submit_grades_and_result_page_shows_answers_and_explanations(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, attempt_id)

    r = submit(student, attempt_id, {str(q1): "q1a", str(q2): "q2b"})  # 1 right, 1 wrong, 1 blank
    assert r.status_code == 200
    data = r.json()
    assert (data["status"], data["correct_count"], data["total_questions"], data["score"]) == (
        "SUBMITTED",
        1,
        3,
        "3.33",
    )

    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.status == AttemptStatus.SUBMITTED
    assert attempt.answers == {str(q1): "q1a", str(q2): "q2b"}
    assert attempt.submitted_at <= attempt.expires_at

    page = student.get(data["result_url"])
    assert page.status_code == 200
    assert "3.33" in page.text
    assert "LOI_GIAI_BI_MAT_2" in page.text  # explanations are revealed only after submitting
    assert "Đáp án đúng" in page.text

    # The taking page now redirects to the result.
    r = student.get(f"/attempts/{attempt_id}", follow_redirects=False)
    assert r.headers["location"] == f"/attempts/{attempt_id}/result"


def test_submit_is_idempotent(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, attempt_id)
    submit(student, attempt_id, {str(q1): "q1a"})
    second = submit(student, attempt_id, {str(q1): "q1a", str(q2): "q2a", str(q3): "q3a"})
    assert second.json()["correct_count"] == 1
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).correct_count == 1


def test_result_is_hidden_while_the_attempt_is_running(student, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    r = student.get(f"/attempts/{attempt_id}/result", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/attempts/{attempt_id}"


def test_late_submission_ignores_answers_sent_after_the_grace_period(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, attempt_id)
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(minutes=5))))
    data = submit(student, attempt_id, {str(q1): "q1a", str(q2): "q2a", str(q3): "q3a"}).json()
    assert (data["status"], data["correct_count"], data["score"]) == ("SUBMITTED", 0, "0.00")


def test_submission_within_grace_period_counts(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, *_ = question_ids(student, attempt_id)
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(seconds=5))))
    data = submit(student, attempt_id, {str(q1): "q1a"}).json()
    assert data["correct_count"] == 1
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.submitted_at == attempt.expires_at  # recorded as on time, capped at the deadline


def test_abandoned_attempt_is_finalized_when_student_returns(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(minutes=5))))
    page = student.get(f"/exams/{exam_id}")
    assert "Đã nộp" in page.text
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert (attempt.status, attempt.score, attempt.submitted_at) == (
        AttemptStatus.SUBMITTED,
        Decimal("0.00"),
        attempt.expires_at,
    )


def test_cannot_submit_someone_elses_attempt(student, make_user, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    student.post("/logout")
    make_user("khac@example.com")
    student.post("/login", data={"email": "khac@example.com", "password": "password123"})
    assert submit(student, attempt_id, {}).status_code == 404
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).status == AttemptStatus.IN_PROGRESS


def test_history_shows_scores_and_retry(student, exam_id):  # noqa: F811
    first = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, first)
    submit(student, first, {str(q1): "q1a", str(q2): "q2a", str(q3): "q3a"})
    second = attempt_id_from(start(student, exam_id))
    assert second != first
    page = student.get(f"/exams/{exam_id}")
    assert "cao nhất 10 điểm" in page.text
    assert f"/attempts/{first}/result" in page.text
