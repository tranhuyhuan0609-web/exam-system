"""Similar papers: AI rewrites a teacher's paper into new questions, checks them by solving again; the teacher
reviews and gives them as tests (admins may publish them on the site)."""

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.ai.schemas import Solution
from app.application.categories import create_category
from app.application.similar import make_variants
from app.main import app
from app.models import Exam, ExamQuestion, ImportJob, JobStatus, Question, UserRole
from app.worker import claim_next

PAPER = [
    {
        "number": 1,
        "type": "MULTIPLE_CHOICE",
        "content": "Tính $2+3$.",
        "choices": ["4", "5", "6", "7"],
        "correct": 1,
        "truths": [],
        "answers": [],
        "explanation": "2+3=5",
        "difficulty": "RECOGNITION",
        "figure": None,
        "flags": [],
    },
    {
        "number": 2,
        "type": "SHORT_ANSWER",
        "content": "Tính $3 \\cdot 4$.",
        "choices": [],
        "correct": None,
        "truths": [],
        "answers": ["12"],
        "explanation": "12",
        "difficulty": "UNDERSTANDING",
        "figure": None,
        "flags": [],
    },
    {
        "number": 3,
        "type": "MULTIPLE_CHOICE",
        "content": "Dựa vào hình, chọn đáp án.",
        "choices": ["a", "b", "c", "d"],
        "correct": 0,
        "truths": [],
        "answers": [],
        "explanation": "",
        "difficulty": "RECOGNITION",
        "figure": "figures/x.png",
        "flags": [],
    },
]


class Writer:
    name, model = "groq", "gpt-oss"

    def __init__(self):
        self.asked = []

    async def available(self):
        return True

    async def ask_json(self, system, user, items=1):
        sources = json.loads(user)
        self.asked.append([s["ref"] for s in sources])
        items = []
        for s in sources:
            for v in (1, 2):
                if s["type"] == "MULTIPLE_CHOICE":
                    items.append(
                        {
                            "ref": s["ref"],
                            "version": v,
                            "content": f"Tính ${v}+4$.",
                            "choices": ["3", "4", f"{v + 4}", "9"],
                            "correct": 2,
                            "explanation": "Cộng",
                        }
                    )
                else:
                    items.append(
                        {
                            "ref": s["ref"],
                            "version": v,
                            "content": f"Tính ${v} \\cdot 5$.",
                            "answer": str(v * 5),
                            "explanation": "Nhân",
                        }
                    )
        return json.dumps({"items": items})

    async def solve(self, questions, figures=()):
        out = []
        for q in questions:  # right for multiple choice; wrong for the short answers of version 2
            short = "5" if "1 \\cdot" in q.content else "11"
            out.append(
                Solution(
                    number=q.number,
                    step_by_step_solution="",
                    final_value=short,
                    matches_an_option=True,
                    correct_label="C" if q.type == "MULTIPLE_CHOICE" else None,
                    short_answer=short,
                    difficulty="RECOGNITION",
                )
            )
        return out


def test_ai_writes_similar_questions_and_checks_them_by_solving_again():
    writer = Writer()
    variants = asyncio.run(make_variants(PAPER, 2, [writer], None))
    assert writer.asked == [[0, 1]]  # the figure question is not rewritten
    by = {(d["version"], d["of"]): d for d in variants}
    assert len(variants) == 6 and [d["version"] for d in variants] == [1, 1, 1, 2, 2, 2]
    assert by[(1, 1)]["content"] == "Tính $1+4$." and by[(1, 1)]["checked"] is True
    assert by[(1, 2)]["answers"] == ["5"] and by[(1, 2)]["checked"] is True
    assert (
        by[(2, 2)]["checked"] is False
        and "check_mismatch" in by[(2, 2)]["flags"]
        and by[(2, 2)]["check_answer"] == "11"
    )
    kept = by[(2, 3)]
    assert "kept_original" in kept["flags"] and kept["figure"] == "figures/x.png"
    assert kept["choices"][kept["correct"]] == "a"  # options moved, still the right one


def _setup(db, make_user):
    async def make(s):
        root = await create_category(s, "Toán 6", slug="toan-6")
        await s.flush()
        return root.id

    root = db(make)
    make_user("gv@example.com", role=UserRole.TEACHER)
    teacher = TestClient(app)
    teacher.post("/login", data={"email": "gv@example.com", "password": "password123"})
    return root, teacher


def test_a_teacher_reviews_the_similar_papers_and_gives_them_as_tests(db, make_user):
    root, teacher = _setup(db, make_user)
    upload = teacher.post(
        "/teacher/make/similar",
        data={"root_id": root, "versions": 2},
        files={"file": ("de-toan.pdf", b"%PDF-1.4 x", "application/pdf")},
        follow_redirects=False,
    )
    job_id = int(upload.headers["location"].rsplit("/", 1)[1])
    job = db(lambda s: s.get(ImportJob, job_id))
    assert (job.purpose, job.versions, job.status) == ("SIMILAR", 2, JobStatus.QUEUED)
    assert 'http-equiv="refresh"' in teacher.get(f"/teacher/make/similar/{job_id}").text  # still working

    variants = asyncio.run(make_variants(PAPER[:2], 2, [Writer()], None))
    db(
        lambda s: s.execute(
            update(ImportJob).where(ImportJob.id == job_id).values(status=JobStatus.DONE, variants=variants)
        )
    )
    page = teacher.get(f"/teacher/make/similar/{job_id}").text
    assert "Đề 1" in page and "Đề 2" in page and "Cần xem: AI giải lại ra 11" in page

    form = {
        "version": ["1", "2"],
        "title": "Kiểm tra 15 phút",
        "starts_at": "2030-01-01T08:00",
        "ends_at": "2030-01-01T09:00",
        "minutes": 15,
        "shuffle": "1",
        "q3_drop": "1",
    }
    for k, d in enumerate(variants):
        form |= {f"q{k}_content": d["content"], f"q{k}_explanation": d["explanation"]}
        if d["type"] == "MULTIPLE_CHOICE":
            form |= {f"q{k}_correct": str(d["correct"])} | {f"q{k}_choice{j}": c for j, c in enumerate(d["choices"])}
        else:
            form[f"q{k}_answer"] = d["answers"][0]
    form["q0_content"] = "Tính $1+4$ (đã sửa)."
    created = teacher.post(f"/teacher/make/similar/{job_id}/create", data=form, follow_redirects=False)
    assert created.status_code == 303 and created.headers["location"] == "/teacher"
    exams = db(lambda s: s.scalars(select(Exam).order_by(Exam.id)))
    assert [e.title for e in exams] == ["Kiểm tra 15 phút – Đề 1", "Kiểm tra 15 phút – Đề 2"]
    assert all(e.visibility.value == "PRIVATE" and e.access_code for e in exams)
    sizes = [db(lambda s, e=e: s.scalars(select(ExamQuestion).where(ExamQuestion.exam_id == e.id))) for e in exams]
    assert [len(x) for x in sizes] == [2, 1]  # one question of paper 2 dropped
    questions = db(lambda s: s.scalars(select(Question).order_by(Question.id)))
    assert questions[0].content == "Tính $1+4$ (đã sửa)." and not any(q.is_verified for q in questions)

    make_user("gv2@example.com", role=UserRole.TEACHER)
    other = TestClient(app)
    other.post("/login", data={"email": "gv2@example.com", "password": "password123"})
    assert other.get(f"/teacher/make/similar/{job_id}").status_code == 404


def test_a_teachers_paper_goes_before_the_library_queue(db, make_user):
    root, _ = _setup(db, make_user)

    async def jobs(s):
        for purpose in ("BANK", "BANK", "SIMILAR"):
            s.add(
                ImportJob(
                    original_filename="x.pdf",
                    stored_path="raw/x.pdf",
                    status=JobStatus.QUEUED,
                    root_id=root,
                    category_id=root,
                    default_difficulty="UNDERSTANDING",
                    purpose=purpose,
                )
            )
        await s.flush()

    db(jobs)
    first = asyncio.run(claim_next())
    assert db(lambda s: s.get(ImportJob, first)).purpose == "SIMILAR"
