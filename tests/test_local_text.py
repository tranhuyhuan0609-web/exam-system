"""Exams read on this machine (pandoc, PyMuPDF): no AI call to read Word files and PDFs with a text layer."""

import asyncio

import docx
import pytest
from docx.oxml import parse_xml

from app.ai.local_text import parse_text_exam, read_docx
from app.ai.pipeline import run_pipeline
from app.infrastructure.storage import get_storage
from tests.test_ai_import import FakeProvider

OMML = (
    '<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
    "<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num><m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>"
    "<m:r><m:t>+</m:t></m:r><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>"
    "</m:oMath>"
)


def make_docx(path, questions: int = 6, answers: str | None = "ĐÁP ÁN", underline: int | None = None):
    document = docx.Document()
    document.add_paragraph("KỲ THI THỬ – MÔN TOÁN")
    for n in range(1, questions + 1):
        stem = document.add_paragraph(f"Câu {n}: Giá trị của biểu thức ")
        stem._p.append(parse_xml(OMML))  # a Word Equation: 1/2 + x^2
        stem.add_run(f" khi x = {n} là")
        options = document.add_paragraph()
        for k, label in enumerate("ABCD"):
            options.add_run(f"{label}. ").bold = True
            run = options.add_run(f"{n + k}.  ")
            run.underline = underline is not None and n == 1 and k == underline
    document.add_paragraph("------ HẾT ------")
    if answers:
        document.add_paragraph(answers)
        document.add_paragraph("  ".join(f"{n}.{'ABCD'[n % 4]}" for n in range(1, questions + 1)))
    document.save(path)


def test_a_word_equation_becomes_latex_and_the_answer_table_is_read(tmp_path):
    make_docx(tmp_path / "de.docx")
    exam = read_docx(tmp_path / "de.docx")
    assert exam is not None and exam.source == "docx" and len(exam.questions) == 6
    first = exam.questions[0]
    assert "\\frac{1}{2}" in first.content and "x^{2}" in first.content and first.content.startswith("Giá trị")
    assert [c.content for c in first.choices] == ["1", "2", "3", "4"]
    assert exam.answers == {i: "ABCD"[(i + 1) % 4] for i in range(6)}  # "1.B 2.C 3.D 4.A …"


def test_an_underlined_option_is_the_answer(tmp_path):
    make_docx(tmp_path / "de.docx", answers=None, underline=2)
    assert read_docx(tmp_path / "de.docx").answers == {0: "C"}


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("\n".join(f"Câu {n}: Hỏi {n}\nA. 1 B. 2 C. 3 D. 4" for n in [*range(1, 7), *range(1, 7)]), "two papers"),
        ("\n".join(f"Câu {n}: Dựa vào hình vẽ, tìm x\nA. 1 B. 2 C. 3 D. 4" for n in range(1, 7)), "figure"),
        ("\n".join(f"Câu {n}: Hỏi {n} — đáp án ở trang sau" for n in range(1, 7)), "no options"),
        ("Câu 1: Hỏi\nA. 1 B. 2 C. 3 D. 4", "too short"),
    ],
)
def test_unsure_readings_are_left_to_the_page_images(text, why):
    assert parse_text_exam(text) is None, why


def test_a_word_file_goes_through_the_import_without_any_ai_reading(tmp_path, monkeypatch):
    make_docx(tmp_path / "de.docx")
    storage = get_storage()
    asyncio.run(storage.put("raw/local.docx", (tmp_path / "de.docx").read_bytes(), "application/octet-stream"))
    provider = FakeProvider()

    async def progress(*args):
        pass

    pages, drafts = asyncio.run(
        run_pipeline(
            job_id=990,
            source_key="raw/local.docx",
            storage=storage,
            provider=provider,
            soffice="definitely-not-installed-soffice",  # no LibreOffice: no page images, the text is enough
            default_difficulty="UNDERSTANDING",
            progress=progress,
        )
    )
    assert provider.extract_calls == [] and pages == 0 and len(drafts) == 6
    assert len(provider.solve_calls) == 1  # all six solved in one call, for their worked solutions
    # The fake AI answers C (index 2) for every question; the file's own answers win, disagreements are marked.
    assert [d["correct"] for d in drafts] == [(i + 1) % 4 for i in range(6)]
    assert ["key_mismatch" in d["flags"] for d in drafts] == [(i + 1) % 4 != 2 for i in range(6)]
