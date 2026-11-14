"""Official answer keys: read from the answer table of a paper, the reference for publishing imports by itself."""

import asyncio

import pytest
from sqlalchemy import select

from app import worker
from app.ai.schemas import PaperKey
from app.application import library_publish
from app.application.answer_keys import compare, is_answer_key, paper_code
from app.infrastructure.storage import get_storage
from app.models import (
    AnswerKey,
    Difficulty,
    Exam,
    ImportJob,
    JobStatus,
    LibraryDocument,
    LibraryKind,
    LibraryStatus,
)
from app.pipeline.use_cases.toc import ensure_path
from tests.pdf_fixtures import png


@pytest.mark.parametrize(
    ("title", "parent", "expected"),
    [
        ("4. Đáp án Toán THPTQG2024", None, True),
        ("0. Dap an, thang diem mon Ngu van", None, True),
        ("ĐÁP ÁN CHI TIẾT 5 MÃ ĐỀ MÔN TOÁN 2024", None, False),  # worked solutions, not a table
        ("Lời giải đáp án đề 101", None, False),
        ("Đáp án mã đề 104", 7, False),  # a part cut from a bundle of solutions
        ("Mã đề 101", 7, False),
    ],
)
def test_answer_tables_are_told_apart_from_exams_and_solutions(title, parent, expected):
    assert is_answer_key(LibraryDocument(title=title, parent_id=parent)) is expected


def test_paper_codes():
    assert paper_code("Mã đề 101") == "101" and paper_code("0102") == "102" and paper_code("Đề thi 2024") == "2024"
    assert paper_code("Phần 1") is None


def _drafts():
    mc = [{"type": "MULTIPLE_CHOICE", "correct": i % 4, "flags": []} for i in range(4)]
    tf = [{"type": "TRUE_FALSE", "truths": [True, False, True, True], "flags": []}]
    sa = [{"type": "SHORT_ANSWER", "answers": ["0.5"], "flags": []}]
    return mc + tf + sa


def test_every_kind_of_answer_is_compared():
    official = {"multiple_choice": ["A", "B", "C", "D"], "true_false": ["Đ S Đ Đ"], "short_answer": ["0,5"]}
    assert compare(_drafts(), official) == []
    drafts = _drafts()
    wrong = {"multiple_choice": ["A", "B", "C", "A"], "true_false": ["ĐSSĐ"], "short_answer": ["?"]}
    problems = compare(drafts, wrong)
    assert problems == [
        "Phần trắc nghiệm câu 4: AI D, đáp án chính thức A",
        "Phần đúng/sai câu 1: AI ĐSĐĐ, đáp án chính thức ĐSSĐ",
        "Phần trả lời ngắn câu 1: AI 0.5, đáp án chính thức ?",  # unreadable cell: cannot vouch for it
    ]
    assert [d.get("official") for d in drafts] == [None, None, None, "A", "ĐSSĐ", "?"]
    assert compare(_drafts(), {"multiple_choice": ["A", "B", "C"]})[0].startswith("Phần trắc nghiệm: đề đọc được 4")


class KeyReader:
    models_used = {"gemini"}

    async def read_answer_key(self, pages):
        assert pages and pages[0].png.startswith(b"\x89PNG")
        return [PaperKey(code="0101", multiple_choice=["B"] * 6), PaperKey(code="102")]  # 102: empty, skipped


async def _printer(html, footer):
    return b"%PDF-1.4 fake"


def test_reading_the_answer_table_publishes_the_codes_that_were_waiting_for_it(db, monkeypatch):
    monkeypatch.setattr(library_publish, "chromium_pdf", _printer)
    picture = png((20, 20))
    asyncio.run(get_storage().put("raw/key.png", picture, "image/png"))

    async def make(s):
        category = await ensure_path(s, ["Ôn thi THPT – Toán", "Đề thi THPT 2024"])
        common = {
            "root_id": category.root_id,
            "category_id": category.id,
            "default_difficulty": Difficulty.UNDERSTANDING,
        }
        key_job = ImportJob(original_filename="Đáp án.png", stored_path="raw/key.png", **common)
        drafts = [
            {
                "number": n,
                "page": 1,
                "type": "MULTIPLE_CHOICE",
                "content": f"Câu hỏi {n}: $x^{n}$",
                "choices": ["$0$", "$1$", "$2$", "$3$"],
                "correct": 1,
                "truths": [],
                "answers": [],
                "explanation": "giải",
                "difficulty": "RECOGNITION",
                "figure": None,
                "flags": [],
                "question_id": None,
            }
            for n in range(1, 7)
        ]
        waiting = ImportJob(
            original_filename="Mã đề 101.pdf",
            stored_path="raw/x.pdf",
            status=JobStatus.DONE,
            drafts=drafts,
            stage="Đã đọc 6 câu · Chờ admin duyệt: chưa có đáp án chính thức để đối chiếu",
            **common,
        )
        s.add_all([key_job, waiting])
        await s.flush()
        doc = {
            "kind": LibraryKind.EXAM,
            "status": LibraryStatus.PUBLISHED,
            "source": "drive:demo",
            "folder_path": ["THPT 2024"],
        }
        s.add(
            LibraryDocument(
                title="4. Đáp án Toán THPTQG2024",
                source_file_id="k",
                storage_key="k.png",
                mime="image/png",
                import_job_id=key_job.id,
                **doc,
            )
        )
        s.add(
            LibraryDocument(
                title="Mã đề 101",
                source_file_id="e",
                storage_key="e.pdf",
                mime="application/pdf",
                import_job_id=waiting.id,
                **doc,
            )
        )
        return key_job.id, waiting.id

    key_job, waiting = db(make)
    asyncio.run(worker.process(key_job, provider=KeyReader()))

    job = db(lambda s: s.get(ImportJob, key_job))
    assert job.status == JobStatus.DONE and job.stage == "Đã đọc đáp án chính thức của 1 mã đề"
    keys = db(lambda s: s.scalars(select(AnswerKey)))
    assert [(k.code, k.answers["multiple_choice"]) for k in keys] == [("101", ["B"] * 6)]
    exam = db(lambda s: s.scalar(select(Exam)))
    assert exam is not None and exam.title == "THPTQG-Toán-001"  # the waiting code went out by itself
    assert "Chờ admin duyệt" not in db(lambda s: s.get(ImportJob, waiting)).stage
