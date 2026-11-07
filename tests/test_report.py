from datetime import timedelta

import pytest
from sqlalchemy import func, update

from app.models import Difficulty, Exam, ExamAttempt, ExamQuestion, Question
from tests.factories import make_tree


@pytest.fixture
def topic_exam(db):
    """4 multiple-choice questions: 2 in lesson "Định luật Boyle" (recognition), 2 in chapter "Chương 2" only
    (application). Correct option id ends with "a"."""

    async def _make(s):
        t = await make_tree(s, {"Vật lí 12": {"Chương 2": {"Định luật Boyle": {}}}})
        subject, chapter, lesson = t["Vật lí 12"], t["Chương 2"], t["Định luật Boyle"]

        def mc(n, difficulty, lesson_id=None):
            return Question(
                root_id=subject.id,
                category_id=lesson_id or chapter.id,
                difficulty=difficulty,
                content=f"Câu {n}",
                choices=[
                    {"id": f"{n}a", "content": "đúng", "is_correct": True},
                    {"id": f"{n}b", "content": "sai", "is_correct": False},
                ],
            )

        questions = [
            mc(1, Difficulty.RECOGNITION, lesson.id),
            mc(2, Difficulty.RECOGNITION, lesson.id),
            mc(3, Difficulty.APPLICATION),
            mc(4, Difficulty.APPLICATION),
        ]
        s.add_all(questions)
        exam = Exam(
            root_id=subject.id, category_id=subject.id, title="Đề báo cáo", duration_seconds=600, is_published=True
        )
        s.add(exam)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=exam.id, question_id=q.id, position=i) for i, q in enumerate(questions, 1))
        return exam.id, [q.id for q in questions]

    return db(_make)


@pytest.fixture
def started(client, make_user, topic_exam):
    make_user("hs@example.com")
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    exam_id, ids = topic_exam
    loc = client.post(f"/exams/{exam_id}/start", follow_redirects=False).headers["location"]
    return client, int(loc.rsplit("/", 1)[1]), ids


def test_flags_are_autosaved_restored_and_kept_after_submit(started, db):
    client, attempt_id, (q1, q2, q3, q4) = started
    r = client.patch(f"/api/attempts/{attempt_id}/answers", json={"changes": {}, "flags": [str(q3), str(q1), "99999"]})
    assert r.status_code == 200
    assert client.get(f"/api/attempts/{attempt_id}/paper").json()["flags"] == [str(q1), str(q3)]  # paper order
    client.patch(f"/api/attempts/{attempt_id}/answers", json={"changes": {str(q2): "2a"}})  # no flags: unchanged
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).flags == [str(q1), str(q3)]
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}, "flags": [str(q4)]})
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).flags == [str(q4)]


def test_dashboard_then_solutions_page(started, db):
    client, attempt_id, (q1, q2, q3, q4) = started
    # Lesson questions right, chapter-only questions: one wrong, one blank. Flag question 3.
    answers = {str(q1): "1a", str(q2): "2a", str(q3): "3b"}
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": answers, "flags": [str(q3)]})
    db(lambda s: s.execute(update(ExamAttempt).values(submitted_at=ExamAttempt.started_at + timedelta(seconds=200))))

    page = client.get(f"/attempts/{attempt_id}/result").text
    # Overview: rank for 5.0 / 10, time vs allowed, pace.
    assert "rank-average" in page and ">Trung bình<" in page
    assert "3 phút 20 giây" in page and "Quy định: 10 phút" in page and "50 giây" in page
    # Difficulty breakdown by questions fully correct.
    assert 'data-tip="Nhận biết: đúng 2/2 câu (100%)"' in page
    assert 'data-tip="Vận dụng: đúng 0/2 câu (0%)"' in page
    # Topic mastery: the chapter-only questions are the weak topic.
    weak = page.split("weak-topics-alert", 1)[1].split("weak-tags-list", 1)[1].split("</div>", 1)[0]
    assert "cần củng cố" in page and "Chương 2" in weak and "Định luật Boyle" not in weak
    # The dashboard shows no question details, only the answer sheet linking to the solutions page.
    assert "Đáp án đúng" not in page and "question-card" not in page
    assert f'href="/attempts/{attempt_id}/solutions#r-3"' in page and "📖 Xem lời giải chi tiết" in page

    solutions = client.get(f"/attempts/{attempt_id}/solutions").text
    assert f'href="/attempts/{attempt_id}/result"' in solutions and "← Quay lại báo cáo" in solutions
    assert 'data-filter="wrong">Câu sai (1)' in solutions and 'data-filter="unanswered">Bỏ trống (1)' in solutions
    assert "🚩 Phân vân (1)" in solutions and "🚩 Em phân vân" in solutions
    assert solutions.count("question-card review-") == 4
    assert "reveal-only" not in solutions and "Đáp án đúng" in solutions  # everything visible here


def test_question_times_are_saved_and_power_the_slowest_filter(started, db):
    client, attempt_id, (q1, q2, q3, q4) = started
    client.patch(
        f"/api/attempts/{attempt_id}/answers",
        json={"changes": {}, "times": {str(q1): 10, str(q2): 95, "9999": 5, str(q3): -4}},
    )
    assert db(lambda s: s.get(ExamAttempt, attempt_id)).question_times == {str(q1): 10, str(q2): 95}
    assert client.get(f"/api/attempts/{attempt_id}/paper").json()["times"] == {str(q1): 10, str(q2): 95}
    times = {str(q1): 12, str(q2): 130, str(q3): 40, str(q4): 100000}  # q4 is capped at the attempt length
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {}, "times": times})
    attempt = db(lambda s: s.get(ExamAttempt, attempt_id))
    assert attempt.question_times[str(q4)] == 630  # 10 minutes + 30 s grace
    page = client.get(f"/attempts/{attempt_id}/solutions").text
    assert "🐢 Làm chậm nhất (1)" in page  # slowest quarter of 4 questions
    assert "⏱ 2 phút 10 giây" in page and "⏱ 12 giây" in page


def test_report_without_weak_topics(started, db):
    client, attempt_id, ids = started
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {str(q): f"{i}a" for i, q in enumerate(ids, 1)}})
    db(lambda s: s.execute(update(ExamAttempt).values(submitted_at=func.now())))
    page = client.get(f"/attempts/{attempt_id}/result").text
    assert "không bị hổng kiến thức ở chuyên đề nào" in page
    assert "Phân vân (" not in page  # no flag filter when nothing was flagged
