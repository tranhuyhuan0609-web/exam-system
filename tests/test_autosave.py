import asyncio
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, update

from app import worker
from app.models import AttemptStatus, ExamAttempt
from tests.test_attempts import attempt_id_from, exam_id, start, student  # noqa: F401  (fixtures)

# Fixture paper: questions 1..3, correct choice ids "q{i}a", wrong ones "q{i}b".


def autosave(client, attempt_id, changes):
    return client.patch(f"/api/attempts/{attempt_id}/answers", json={"changes": changes})


def question_ids(client, attempt_id) -> list[int]:
    return [q["id"] for q in client.get(f"/api/attempts/{attempt_id}/paper").json()["questions"]]


def test_autosave_merges_changes_and_paper_restores_them(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, attempt_id)

    r = autosave(student, attempt_id, {str(q1): "q1a"})
    assert r.status_code == 200
    assert r.json()["saved"] == 1 and 590 < r.json()["remaining_seconds"] <= 600
    autosave(student, attempt_id, {str(q2): "q2b", str(q3): "q3a"})
    autosave(student, attempt_id, {str(q2): "q2a", str(q3): None})  # change one, clear one

    # Session recovery: reopening the paper returns exactly what was saved.
    assert student.get(f"/api/attempts/{attempt_id}/paper").json()["answers"] == {str(q1): "q1a", str(q2): "q2a"}


def test_autosave_ignores_invalid_entries(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, *_ = question_ids(student, attempt_id)
    r = autosave(student, attempt_id, {str(q1): "not-a-choice", "999999": "x", "abc": "q1a"})
    assert r.status_code == 200 and r.json()["saved"] == 0
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).answers == {}


def test_autosave_is_rejected_after_submit_or_deadline(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    q1, *_ = question_ids(student, attempt_id)
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(minutes=5))))
    r = autosave(student, attempt_id, {str(q1): "q1a"})
    assert r.status_code == 409
    assert r.json() == {"status": "EXPIRED", "result_url": f"/attempts/{attempt_id}/result"}

    second = attempt_id_from(start(student, exam_id))
    student.post(f"/api/attempts/{second}/submit", json={"answers": {}})
    assert autosave(student, second, {str(q1): "q1a"}).json()["status"] == "SUBMITTED"


def test_autosave_requires_owner(student, make_user, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    student.post("/logout")
    assert autosave(student, attempt_id, {}).status_code == 401
    make_user("khac@example.com")
    student.post("/login", data={"email": "khac@example.com", "password": "password123"})
    assert autosave(student, attempt_id, {}).status_code == 404


def test_late_submit_and_worker_sweep_grade_the_autosaved_answers(student, db, exam_id):  # noqa: F811
    # A student who answered, then lost connection: the worker grades what was auto-saved.
    first = attempt_id_from(start(student, exam_id))
    q1, q2, q3 = question_ids(student, first)
    autosave(student, first, {str(q1): "q1a", str(q2): "q2a"})
    db(lambda s: s.execute(update(ExamAttempt).values(expires_at=func.now() - timedelta(minutes=5))))

    assert asyncio.run(worker.auto_submit_once()) == 1
    attempt = db(lambda s: s.get(ExamAttempt, first))
    assert (attempt.status, attempt.correct_count, attempt.score) == (AttemptStatus.SUBMITTED, 2, Decimal("6.67"))
    assert attempt.submitted_at == attempt.expires_at
    assert asyncio.run(worker.auto_submit_once()) == 0  # nothing left to do

    # A late manual submit keeps the auto-saved answers instead of the ones sent too late.
    second = attempt_id_from(start(student, exam_id))
    autosave(student, second, {str(q1): "q1a"})
    db(
        lambda s: s.execute(
            update(ExamAttempt).where(ExamAttempt.id == second).values(expires_at=func.now() - timedelta(minutes=5))
        )
    )
    late = {str(q1): "q1a", str(q2): "q2a", str(q3): "q3a"}
    data = student.post(f"/api/attempts/{second}/submit", json={"answers": late}).json()
    assert data["correct_count"] == 1


def test_worker_sweep_leaves_running_attempts_alone(student, db, exam_id):  # noqa: F811
    attempt_id = attempt_id_from(start(student, exam_id))
    assert asyncio.run(worker.auto_submit_once()) == 0
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).status == AttemptStatus.IN_PROGRESS
