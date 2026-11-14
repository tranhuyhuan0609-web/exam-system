"""Our own books from the question bank, and anthologies of open papers, filed in the library."""

import asyncio

from sqlalchemy import select

from app.application import books
from app.application.categories import create_category
from app.infrastructure.pdf_engine import PDFEngineAdapter
from app.infrastructure.storage import get_bronze_storage
from app.models import Difficulty, LibraryDocument, LibraryKind, LibraryStatus, Question, QuestionType
from app.pipeline.harvest.anthologies import SIZE, compile_anthologies
from tests.pdf_fixtures import blank_pdf


def _pdf(pages: int = 1) -> bytes:
    return blank_pdf(pages)


class Printer:
    def __init__(self):
        self.html: list[str] = []

    async def __call__(self, html, footer):
        self.html.append(html)
        return _pdf()


def _bank(db, per_lesson: int = 12):
    async def make(s):
        root = await create_category(s, "Vật lí 10", slug="vat-li-10")
        series = await create_category(s, "Kết nối tri thức", parent=root)
        for c in range(1, 3):
            chapter = await create_category(s, f"Chương {c}", parent=series)
            lesson = await create_category(s, f"Bài {c}", parent=chapter)
            for n in range(per_lesson):
                s.add(
                    Question(
                        root_id=root.id,
                        category_id=lesson.id,
                        difficulty=Difficulty.UNDERSTANDING,
                        question_type=QuestionType.MULTIPLE_CHOICE,
                        content=f"Câu {c}.{n}: $v = {n}$ m/s là",
                        choices=[{"id": i, "content": i.upper(), "is_correct": i == "b"} for i in "abcd"],
                        explanation=f"Lời giải {c}.{n}",
                        source_url="https://vietjack.com/x",
                    )
                )
        await s.flush()

    db(make)


def test_the_bank_is_bound_as_books_printed_again_only_when_questions_change(db, monkeypatch):
    _bank(db)
    printer = Printer()
    stats = db(lambda s: books.compile_books(s, render=printer))
    assert stats == {"printed": 1, "unchanged": 0, "failed": 0}
    html = printer.html[0]
    assert "Tuyển tập câu hỏi Vật lí 10 – Kết nối tri thức" in html and "Chương 2" in html and "Bài 1" in html
    assert "Đáp án và lời giải – Chương 1" in html and "Lời giải 2.11" in html and "vietjack.com" in html
    doc = db(lambda s: s.scalar(select(LibraryDocument).where(LibraryDocument.source == "book")))
    assert doc.kind == LibraryKind.BOOK and doc.status == LibraryStatus.PUBLISHED and doc.storage_key
    assert doc.folder_path == ["Tuyển tập câu hỏi", "Vật lí 10"] and doc.mime == "application/pdf"

    assert db(lambda s: books.compile_books(s, render=printer))["unchanged"] == 1  # nothing new: not printed
    db(lambda s: s.execute(Question.__table__.update().where(Question.id == 1).values(explanation="Sửa lời giải")))
    assert db(lambda s: books.compile_books(s, render=printer))["printed"] == 1  # a question changed

    monkeypatch.setattr(books, "MAX_PER_VOLUME", 12)  # a large book comes in volumes of whole chapters
    titles = [b.title for b in db(books.plan_books)]
    assert titles == [f"Tuyển tập câu hỏi Vật lí 10 – Kết nối tri thức – Tập {n}" for n in (1, 2)]


def test_open_papers_of_a_field_are_bound_with_a_table_of_contents(db):
    bronze = get_bronze_storage()

    async def papers(s):
        for n in range(SIZE + 3):  # one full group (and 3 waiting for the next)
            key = f"open/openalex/p{n}.pdf"
            await bronze.put(key, _pdf(2), "application/pdf")
            s.add(
                LibraryDocument(
                    title=f"Bài báo {n}",
                    kind=LibraryKind.PAPER,
                    status=LibraryStatus.PUBLISHED,
                    folder_path=["Giáo dục học"],
                    source="openalex",
                    source_file_id=f"doi:10.1/{n}",
                    storage_key=key,
                    mime="application/pdf",
                    authors=f"Tác giả {n}",
                    year=2020 + n % 3,
                    license_note="cc-by · OpenAlex" if n != 5 else "Open access · OpenAlex",
                )
            )
        await s.flush()

    db(papers)
    printer = Printer()
    assert db(lambda s: compile_anthologies(s, render=printer)) == {"printed": 1, "failed": 0}
    book = db(lambda s: s.scalar(select(LibraryDocument).where(LibraryDocument.source == "anthology")))
    assert book.title == "Tuyển tập nghiên cứu Giáo dục học – Số 1" and book.folder_path[0] == "Tuyển tập nghiên cứu"
    with PDFEngineAdapter.open(asyncio.run(bronze.get(book.storage_key))) as pdf:
        assert pdf.page_count == 1 + SIZE * 2  # the cover, then every paper in full
    toc = printer.html[-1]
    assert "Tác giả 0" in toc and "DOI 10.1/0" in toc and "cc-by" in toc and "Bài báo 5<" not in toc  # CC only
    assert db(lambda s: compile_anthologies(s, render=printer))["printed"] == 0  # the next group is not full yet
