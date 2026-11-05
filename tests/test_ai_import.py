import asyncio
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from app import worker
from app.ai.pipeline import clean_choice, run_pipeline
from app.ai.provider import AIError, FigureImage, PageImage
from app.ai.schemas import BoundingBox, ExtractedChoice, ExtractedQuestion, Solution
from app.config import get_settings
from app.models import Chapter, Exam, ExamQuestion, ImportJob, JobStatus, Question, Subject, UserRole


def make_pdf(pages: int = 2) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Cau {i + 1}: trang {i + 1}")
        page.draw_rect(pymupdf.Rect(300, 300, 500, 450), color=(0, 0, 0), width=2)  # a "figure"
    return doc.tobytes()


def mcq(number, page=1, figure=False, kind="MULTIPLE_CHOICE"):
    return ExtractedQuestion(
        number=number,
        page=page,
        type=kind,
        content=f"Câu hỏi {number}: tính $p_{number}$",
        choices=[ExtractedChoice(label=label, content=f"${number}{label}\\text{{ lít.}}$") for label in "ABCD"]
        if kind == "MULTIPLE_CHOICE"
        else [],
        figure_bbox=BoundingBox(ymin=350, xmin=480, ymax=550, xmax=820) if figure else None,
    )


class FakeProvider:
    """Mimics the model: page 1 has Câu 1 (with figure) and 2; page 2 has Câu 3 (short answer)."""

    def __init__(self, fail_solve=False):
        self.extract_calls: list[list[PageImage]] = []
        self.solve_calls: list[tuple[list[ExtractedQuestion], list[FigureImage]]] = []
        self.fail_solve = fail_solve

    @property
    def models_used(self):
        return {"fake-model"}

    async def extract(self, pages):
        self.extract_calls.append(pages)
        # Câu 2 is returned twice (once per chunk would happen with overlap) to exercise de-duplication.
        return [mcq(1, figure=True), mcq(2), mcq(2), mcq(3, page=2, kind="SHORT_ANSWER")]

    async def solve(self, questions, figures):
        self.solve_calls.append((questions, figures))
        if self.fail_solve:
            raise AIError("boom")
        out = []
        for q in questions:
            if q.type == "SHORT_ANSWER":
                out.append(
                    Solution(
                        number=q.number,
                        step_by_step_solution="$h = 30$ m",
                        final_value="30 m",
                        matches_an_option=False,
                        correct_label=None,
                        short_answer="30 m",
                        difficulty="APPLICATION",
                    )
                )
            else:
                out.append(
                    Solution(
                        number=q.number,
                        step_by_step_solution=f"Lời giải câu {q.number}",
                        final_value="8 lít",
                        matches_an_option=q.number != 2,
                        correct_label="C",
                        short_answer=None,
                        difficulty="UNDERSTANDING",
                    )
                )
        return out


async def _noop_progress(stage, done, total):
    return None


def test_clean_choice_strips_labels_and_trailing_dots():
    assert clean_choice("$11\\text{ lít.}$") == "$11\\text{ lít}$"
    assert clean_choice("A. 8,9 lít.") == "8,9 lít"
    assert clean_choice("$1{,}3V.$") == "$1{,}3V$"
    assert clean_choice("đi qua vị trí cân bằng") == "đi qua vị trí cân bằng"


def test_pipeline_builds_drafts_with_figures_flags_and_answers(tmp_path):
    source = tmp_path / "de.pdf"
    source.write_bytes(make_pdf())
    provider = FakeProvider()
    pages, drafts = asyncio.run(
        run_pipeline(
            job_id=7,
            source=source,
            data_dir=tmp_path,
            provider=provider,
            soffice="soffice",
            default_difficulty="RECOGNITION",
            progress=_noop_progress,
        )
    )
    assert pages == 2
    assert [d["number"] for d in drafts] == [1, 2, 3]  # duplicate Câu 2 removed
    first, second, third = drafts
    assert first["choices"] == ["$1A\\text{ lít}$", "$1B\\text{ lít}$", "$1C\\text{ lít}$", "$1D\\text{ lít}$"]
    assert first["correct"] == 2 and first["flags"] == [] and first["difficulty"] == "UNDERSTANDING"
    assert first["figure"].startswith("figures/7-1-")
    assert (tmp_path / "media" / first["figure"]).is_file()
    assert second["flags"] == ["no_match"]
    assert third["type"] == "SHORT_ANSWER" and third["flags"] == []
    assert third["answers"] == ["30"]  # unit stripped from the model's "30 m"
    # The solver sees the cropped figure of Câu 1.
    ((_, figures),) = provider.solve_calls
    assert [f.question_number for f in figures] == [1]
    # Page images are rendered for the review screen.
    assert (tmp_path / "jobs" / "7" / "pages" / "page-002.png").is_file()


def test_pipeline_keeps_questions_when_solving_fails(tmp_path):
    source = tmp_path / "de.pdf"
    source.write_bytes(make_pdf(1))
    _, drafts = asyncio.run(
        run_pipeline(
            job_id=1,
            source=source,
            data_dir=tmp_path,
            provider=FakeProvider(fail_solve=True),
            soffice="soffice",
            default_difficulty="RECOGNITION",
            progress=_noop_progress,
        )
    )
    assert drafts and all("no_solution" in d["flags"] for d in drafts)
    assert all(d["correct"] is None for d in drafts)


# ---------------------------------------------------------------- end to end through the web + worker


@pytest.fixture
def chapter_scope(db):
    async def _make(s):
        subject = Subject(name="Vật lí 10", slug="vat-li-10")
        subject.chapters = [Chapter(name="Chương 5: Khí lí tưởng", position=1)]
        s.add(subject)
        await s.flush()
        return f"c:{subject.chapters[0].id}"

    return db(_make)


def upload(client, scope, data=None, filename="de.pdf"):
    return client.post(
        "/admin/ai-imports",
        data={"scope": scope, "default_difficulty": "UNDERSTANDING"},
        files={"file": (filename, data or make_pdf(), "application/pdf")},
        follow_redirects=False,
    )


def test_upload_process_review_and_publish(admin_client, db, chapter_scope):
    r = upload(admin_client, chapter_scope)
    assert r.status_code == 303
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    job = db(lambda s: s.get(ImportJob, job_id))
    assert job.status == JobStatus.QUEUED
    assert (Path(get_settings().data_dir) / job.stored_path).is_file()

    assert asyncio.run(worker.claim_next()) == job_id
    asyncio.run(worker.process(job_id, provider=FakeProvider()))
    job = db(lambda s: s.get(ImportJob, job_id))
    assert job.status == JobStatus.DONE and len(job.drafts) == 3 and job.page_count == 2

    page = admin_client.get(f"/admin/ai-imports/{job_id}")
    assert page.status_code == 200
    assert "Câu 1" in page.text and "Kết quả AI tính ra không khớp" in page.text
    assert admin_client.get(f"/admin/ai-imports/{job_id}/pages/1.png").headers["content-type"] == "image/png"
    assert admin_client.get(f"/admin/ai-imports/{job_id}/status").json()["status"] == "DONE"

    # Fix Câu 2 by hand: pick B and rewrite the explanation; the warning goes away.
    admin_client.post(
        f"/admin/ai-imports/{job_id}/drafts/1",
        data={
            "content": "Câu hỏi 2 đã sửa",
            "choice_content": ["a", "b", "c", "d"],
            "correct": "1",
            "explanation": "Giải lại",
            "difficulty": "APPLICATION",
            "type": "MULTIPLE_CHOICE",
        },
    )
    draft = db(lambda s: s.get(ImportJob, job_id)).drafts[1]
    assert (draft["content"], draft["correct"], draft["flags"], draft["edited"]) == ("Câu hỏi 2 đã sửa", 1, [], True)

    # Publish all three, including the short-answer question.
    r = admin_client.post(
        f"/admin/ai-imports/{job_id}/publish",
        data={"selected": ["0", "1", "2"], "create_exam": "on", "exam_title": "Buổi 12", "duration_minutes": "20"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    questions = db(lambda s: s.scalars(select(Question).order_by(Question.id)))
    assert [q.content for q in questions] == ["Câu hỏi 1: tính $p_1$", "Câu hỏi 2 đã sửa", "Câu hỏi 3: tính $p_3$"]
    assert questions[0].image_path.startswith("figures/") and questions[1].image_path is None
    assert [c["is_correct"] for c in questions[1].choices] == [False, True, False, False]
    assert (questions[2].question_type.value, questions[2].accepted_answers, questions[2].choices) == (
        "SHORT_ANSWER",
        ["30"],
        [],
    )
    exam = db(lambda s: s.scalar(select(Exam)))
    assert (exam.title, exam.duration_seconds, exam.is_published) == ("Buổi 12", 1200, False)
    assert len(db(lambda s: s.scalars(select(ExamQuestion)))) == 3

    # Figures are public under /media; publishing twice does not duplicate questions.
    assert admin_client.get(f"/media/{questions[0].image_path}").status_code == 200
    admin_client.post(f"/admin/ai-imports/{job_id}/publish", data={"selected": ["0", "1", "2"]})
    assert len(db(lambda s: s.scalars(select(Question.id)))) == 3


def test_word_file_without_libreoffice_fails_with_a_clear_message(admin_client, db, chapter_scope, monkeypatch):
    monkeypatch.setattr(get_settings(), "soffice_path", "definitely-not-installed-soffice")
    r = upload(admin_client, chapter_scope, data=b"PK\x03\x04 fake docx", filename="de.docx")
    job_id = int(r.headers["location"].rsplit("/", 1)[1])
    asyncio.run(worker.process(job_id, provider=FakeProvider()))
    job = db(lambda s: s.get(ImportJob, job_id))
    assert job.status == JobStatus.FAILED
    assert "LibreOffice" in job.error
    # A failed job can be re-queued.
    admin_client.post(f"/admin/ai-imports/{job_id}/retry")
    assert db(lambda s: s.get(ImportJob, job_id)).status == JobStatus.QUEUED


def test_upload_validation_and_access(admin_client, client, make_user, chapter_scope):
    assert (
        "Hãy chọn file PDF"
        in admin_client.post(
            "/admin/ai-imports", data={"scope": chapter_scope}, files={"file": ("de.exe", b"MZ", "application/x")}
        ).text
    )
    assert (
        "Vui lòng chọn môn"
        in admin_client.post(
            "/admin/ai-imports", data={"scope": ""}, files={"file": ("de.pdf", make_pdf(), "application/pdf")}
        ).text
    )
    admin_client.post("/logout")
    make_user("hs@example.com", role=UserRole.STUDENT)
    client.post("/login", data={"email": "hs@example.com", "password": "password123"})
    assert client.get("/admin/ai-imports").status_code == 403
    assert client.get("/admin/ai-imports/1/pages/1.png").status_code == 403
