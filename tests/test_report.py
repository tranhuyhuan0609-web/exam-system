from datetime import timedelta

import pytest
from sqlalchemy import func, update

from app.models import Chapter, Difficulty, Exam, ExamAttempt, ExamQuestion, Lesson, Question, Subject


@pytest.fixture
def topic_exam(db):
    """4 multiple-choice questions: 2 in lesson "Định luật Boyle" (recognition), 2 in chapter "Chương 2" only
    (application). Correct option id ends with "a"."""

    async def _make(s):
        subject = Subject(name="Vật lí 12", slug="vat-li-12")
        chapter = Chapter(name="Chương 2", position=1)
        chapter.lessons = [Lesson(name="Định luật Boyle", position=1)]
        subject.chapters = [chapter]
        s.add(subject)
        await s.flush()
        lesson = chapter.lessons[0]

        def mc(n, difficulty, lesson_id=None):
            return Question(
                subject_id=subject.id,
                chapter_id=chapter.id,
                lesson_id=lesson_id,
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
        exam = Exam(subject_id=subject.id, title="Đề báo cáo", duration_seconds=600, is_published=True)
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


def test_report_shows_timing_accuracy_by_difficulty_and_weak_topics(started, db):
    client, attempt_id, (q1, q2, q3, q4) = started
    # Lesson questions right, chapter-only questions: one wrong, one blank. Flag question 3.
    answers = {str(q1): "1a", str(q2): "2a", str(q3): "3b"}
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": answers, "flags": [str(q3)]})
    db(lambda s: s.execute(update(ExamAttempt).values(submitted_at=ExamAttempt.started_at + timedelta(seconds=200))))
    page = client.get(f"/attempts/{attempt_id}/result").text
    assert "Trung bình mỗi câu" in page and "50 giây" in page  # 200 s / 4 questions
    assert "Tỉ lệ đúng theo mức độ" in page
    assert 'data-tip="Nhận biết: 100%' in page and 'data-tip="Vận dụng: 0%' in page
    # The chapter-only questions form the weak topic; the lesson is fine.
    weak = page.split("Chuyên đề cần ôn", 1)[1].split("</section>", 1)[0]
    assert "Chương 2" in weak and "Hổng kiến thức" in weak and "0%" in weak
    assert "Định luật Boyle" in page.split("Các chuyên đề đạt từ 50%", 1)[1]
    # Review filter and flag markers.
    assert 'data-filter="wrong">Sai (1)' in page and 'data-filter="unanswered">Bỏ trống (1)' in page
    assert "🚩 Phân vân (1)" in page and "🚩 Bạn phân vân" in page


def test_report_without_weak_topics(started, db):
    client, attempt_id, ids = started
    client.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {str(q): f"{i}a" for i, q in enumerate(ids, 1)}})
    db(lambda s: s.execute(update(ExamAttempt).values(submitted_at=func.now())))
    page = client.get(f"/attempts/{attempt_id}/result").text
    assert "Không có chuyên đề nào dưới 50%" in page
    assert "Phân vân (" not in page  # no flag filter when nothing was flagged
