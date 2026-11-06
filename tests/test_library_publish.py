"""A library file imported by the AI: published automatically when its drafts pass the checks, typeset into
clean PDFs (paper + solutions) that replace the original in the library."""

import asyncio
import shutil
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import func, select

from app.application.exam_pdf import PrintedPart, PrintedQuestion, chromium_pdf, exam_html
from app.application.library_publish import auto_publish
from app.infrastructure.storage import get_bronze_storage
from app.models import (
    AnswerKey,
    Difficulty,
    Exam,
    ImportJob,
    JobStatus,
    LibraryDocument,
    LibraryKind,
    LibraryStatus,
    Question,
)
from app.pipeline.use_cases.toc import ensure_path


def _draft(n: int, **extra) -> dict:
    return {
        "number": n,
        "page": 1,
        "type": "MULTIPLE_CHOICE",
        "content": f"Câu hỏi số {n}: tính $x^{n}$ khi $x = 1$",
        "choices": ["$0$", "$1$", "$2$", "$3$"],
        "correct": 1,
        "truths": [],
        "answers": [],
        "explanation": f"Lời giải câu {n}: $1^{n} = 1$.",
        "difficulty": "RECOGNITION",
        "figure": None,
        "flags": [],
        "question_id": None,
        **extra,
    }


class FakePrinter:
    def __init__(self):
        self.pages: list[str] = []

    async def __call__(self, html: str, footer: str) -> bytes:
        self.pages.append(html)
        return b"%PDF-1.4 fake"


def _library_import(db, drafts, file_id="x-101", official: list[str] | None = None):
    """A library exam ("Mã đề 101") and its import; `official` = the paper's official answers (default: the
    drafts' own answers, i.e. they agree), [] = no answer key."""

    async def make(s):
        category = await ensure_path(s, ["Ôn thi THPT – Toán", "Đề thi THPT 2024"])
        answers = ["ABCD"[d["correct"]] for d in drafts] if official is None else official
        known = await s.scalar(select(AnswerKey).where(AnswerKey.category_id == category.id, AnswerKey.code == "101"))
        if answers and known is None:
            s.add(AnswerKey(category_id=category.id, code="101", answers={"multiple_choice": answers}))
        job = ImportJob(
            original_filename="Mã đề 101.pdf",
            stored_path="raw/x.pdf",
            status=JobStatus.DONE,
            root_id=category.root_id,
            category_id=category.id,
            default_difficulty=Difficulty.UNDERSTANDING,
            drafts=drafts,
        )
        s.add(job)
        await s.flush()
        common = {
            "kind": LibraryKind.EXAM,
            "status": LibraryStatus.PUBLISHED,
            "source": "drive:demo",
            "mime": "application/pdf",
        }
        bundle = LibraryDocument(
            title="Toán (mã đề 10x)",
            folder_path=["Đề thi 2024"],
            source_file_id=f"b-{file_id}",
            storage_key="b.pdf",
            license_note="Bộ GD&ĐT",
            import_job_id=job.id,
            **common,
        )
        s.add(bundle)
        await s.flush()
        part = LibraryDocument(
            title="Mã đề 101",
            folder_path=["Đề thi 2024", "Toán (mã đề 10x)"],
            source_file_id=f"{file_id}#p1-6",
            storage_key="p.pdf",
            license_note="Bộ GD&ĐT",
            import_job_id=job.id,
            parent_id=bundle.id,
            downloadable=True,
            **common,
        )
        s.add(part)
        await s.flush()
        return job.id, bundle.id, part.id

    return db(make)


def _publish(db, job_id, printer):
    async def run(s):
        exam = await auto_publish(s, await s.get(ImportJob, job_id), render=printer)
        return exam.id if exam else None

    return db(run)


def test_checked_import_is_published_and_typeset_in_place_of_the_original(db):
    job_id, bundle_id, part_id = _library_import(db, [_draft(n) for n in range(1, 7)])
    printer = FakePrinter()
    exam_id = _publish(db, job_id, printer)

    exam = db(lambda s: s.get(Exam, exam_id))
    assert (
        exam.title == "Đề thi THPT 2024 – Toán – Mã đề 101" and exam.is_published and exam.duration_seconds == 90 * 60
    )
    assert db(lambda s: s.scalar(select(func.count()).select_from(Question).where(Question.is_verified))) == 6

    paper, solutions = printer.pages
    assert "Câu 1." in paper and "Nguồn đề: Bộ GD&amp;ĐT" in paper and "Lời giải câu 1" not in paper
    assert "Lời giải câu 1" in solutions and "Đáp án" in solutions

    clean = db(lambda s: s.scalars(select(LibraryDocument).where(LibraryDocument.exam_id == exam_id)))
    assert sorted(d.title for d in clean) == [exam.title, f"{exam.title} – Lời giải"]
    assert all(
        d.status == LibraryStatus.PUBLISHED
        and d.downloadable
        and d.origin_id == part_id
        and d.folder_path == ["Đề thi 2024", "Toán (mã đề 10x)"]
        for d in clean
    )
    stored = asyncio.run(get_bronze_storage().get(f"generated/exam-{exam_id}-de.pdf"))
    assert stored.startswith(b"%PDF")
    # The original and the bundle (its only exam now has a clean version) are for admins only.
    assert db(lambda s: s.get(LibraryDocument, part_id)).status == LibraryStatus.HIDDEN
    assert db(lambda s: s.get(LibraryDocument, bundle_id)).status == LibraryStatus.HIDDEN


def test_imports_that_need_a_human_wait_for_the_admin(db):
    drafts = [_draft(n) for n in range(1, 7)]
    drafts[2]["flags"] = ["no_match"]
    job_id, _, part_id = _library_import(db, drafts)
    assert _publish(db, job_id, FakePrinter()) is None
    assert "Chờ admin duyệt: câu 3" in db(lambda s: s.get(ImportJob, job_id)).stage
    assert db(lambda s: s.scalar(select(func.count()).select_from(Question))) == 0
    assert db(lambda s: s.get(LibraryDocument, part_id)).status == LibraryStatus.PUBLISHED

    short_job, _, _ = _library_import(db, [_draft(1), _draft(2)], file_id="x-102")
    assert _publish(db, short_job, FakePrinter()) is None  # two questions: not a whole exam


def test_without_official_answers_or_when_they_differ_the_admin_decides(db):
    no_key, _, _ = _library_import(db, [_draft(n) for n in range(1, 7)], official=[])
    assert _publish(db, no_key, FakePrinter()) is None
    assert "chưa có đáp án chính thức" in db(lambda s: s.get(ImportJob, no_key)).stage


def test_an_answer_that_differs_from_the_official_key_is_marked_for_review(db):
    # The AI read question 1 wrongly ("phần thực" for "phần ảo"), solved what it read: B; the key says C.
    job_id, _, part_id = _library_import(db, [_draft(n) for n in range(1, 7)], official=["C"] + ["B"] * 5)
    assert _publish(db, job_id, FakePrinter()) is None
    job = db(lambda s: s.get(ImportJob, job_id))
    assert "lệch đáp án chính thức – Phần trắc nghiệm câu 1: AI B, đáp án chính thức C" in job.stage
    assert "key_mismatch" in job.drafts[0]["flags"] and job.drafts[0]["official"] == "C"
    assert "key_mismatch" not in job.drafts[1]["flags"]
    assert db(lambda s: s.get(LibraryDocument, part_id)).status == LibraryStatus.PUBLISHED  # nothing replaced


def test_the_same_questions_in_another_code_reuse_the_bank(db):
    first, _, _ = _library_import(db, [_draft(n) for n in range(1, 7)])
    second, _, _ = _library_import(db, [_draft(n) for n in range(6, 0, -1)], file_id="x-102")  # shuffled order
    _publish(db, first, FakePrinter())
    exam_id = _publish(db, second, FakePrinter())
    assert db(lambda s: s.scalar(select(func.count()).select_from(Question))) == 6
    assert len(db(lambda s: s.get(Exam, exam_id)).title) > 0


@pytest.mark.skipif(
    not any(Path.home().glob("AppData/Local/ms-playwright/chromium*")) and not shutil.which("chromium"),
    reason="no Chromium for Playwright",
)
def test_chromium_prints_the_paper():
    question = PrintedQuestion(
        1, "MULTIPLE_CHOICE", "Cho hàm số $y = x^3$.", ["$1$", "$2$", "$3$", "$4$"], 4, None, "A"
    )
    html = exam_html(
        title="Đề thử",
        subtitle="Thử",
        minutes=50,
        source="Nguồn đề: thử",
        parts=[PrintedPart("Câu trắc nghiệm", [question])],
        solutions=False,
    )
    pdf = asyncio.run(chromium_pdf(html, "Đề thử"))
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        text = document[0].get_text()
    assert "Câu 1." in text and "Họ, tên thí sinh" in text and "Trang 1/1" in text
