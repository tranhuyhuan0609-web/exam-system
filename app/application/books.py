"""Our own books, typeset from the question bank: one per subject (or per textbook series of a subject), chapters
and lessons from the table of contents, the answers and worked solutions after each chapter.

A book is printed again only when its questions changed (the checksum of their ids and update times). Books go
to the library (section "Sách", folder "Tuyển tập câu hỏi"), read online; the sources of the questions are named
on the last page.
"""

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exam_pdf import PrintedPart, Renderer, build_parts, chromium_pdf
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import Category, LibraryDocument, LibraryKind, LibraryStatus, Question
from app.templating import templates

log = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))
FOLDER = "Tuyển tập câu hỏi"
MIN_QUESTIONS = 20  # fewer: no book yet
MAX_PER_VOLUME = 400  # larger books come in volumes (whole chapters each)


@dataclass
class Chapter:
    name: str
    lessons: list[tuple[str, list[Question]]]  # lesson heading ("" = the chapter itself) and its questions

    @property
    def count(self) -> int:
        return sum(len(qs) for _, qs in self.lessons)


@dataclass
class Book:
    key: str  # "book:<category id>[:<volume>]"
    title: str
    root: str
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def questions(self) -> list[Question]:
        return [q for c in self.chapters for _, qs in c.lessons for q in qs]


def _volumes(key: str, title: str, root: str, chapters: list[Chapter]) -> list[Book]:
    volumes: list[list[Chapter]] = [[]]
    for chapter in chapters:
        if volumes[-1] and sum(c.count for c in volumes[-1]) + chapter.count > MAX_PER_VOLUME:
            volumes.append([])
        volumes[-1].append(chapter)
    if len(volumes) > 1 and sum(c.count for c in volumes[-1]) < MAX_PER_VOLUME // 4:  # no thin last volume
        last = volumes.pop()
        volumes[-1] += last
    if len(volumes) == 1:
        return [Book(key, title, root, volumes[0])]
    return [Book(f"{key}:{n}", f"{title} – Tập {n}", root, v) for n, v in enumerate(volumes, start=1)]


async def plan_books(session: AsyncSession) -> list[Book]:
    categories = {c.id: c for c in (await session.scalars(select(Category))).all()}
    children: dict[int | None, list[Category]] = defaultdict(list)
    for c in sorted(categories.values(), key=lambda c: (c.position, c.name)):
        children[c.parent_id].append(c)
    questions: dict[int, list[Question]] = defaultdict(list)
    for q in (await session.scalars(select(Question).order_by(Question.id))).all():
        questions[q.category_id].append(q)

    def lessons(node: Category, heading: str = "") -> list[tuple[str, list[Question]]]:
        out = [(heading, questions[node.id])] if questions[node.id] else []
        for child in children[node.id]:
            out += lessons(child, child.name if not heading else f"{heading} › {child.name}")
        return out

    def chapters(unit: Category) -> list[Chapter]:
        out = [Chapter("Tổng hợp", [("", questions[unit.id])])] if questions[unit.id] else []
        out += [Chapter(c.name, ls) for c in children[unit.id] if (ls := lessons(c))]
        return out

    books = []
    for root in children[None]:
        # A subject with textbook series (Kết nối tri thức …): one book per series, and one for the rest
        # (questions filed at the subject, chapters outside any series). Otherwise one book for the subject.
        series = [c for c in children[root.id] if children[c.id]]
        units = [(f"book:{c.id}", f"{root.name} – {c.name}", chapters(c)) for c in series]
        rest = [Chapter("Tổng hợp", [("", questions[root.id])])] if questions[root.id] else []
        rest += [Chapter(c.name, ls) for c in children[root.id] if c not in series and (ls := lessons(c))]
        units.append((f"book:{root.id}", root.name, rest))
        for key, name, found in units:
            if sum(c.count for c in found) >= MIN_QUESTIONS:
                books += _volumes(key, f"Tuyển tập câu hỏi {name}", root.name, found)
    return books


def checksum(book: Book) -> str:
    stamp = "|".join(f"{q.id}:{q.updated_at.isoformat() if q.updated_at else ''}" for q in book.questions)
    return hashlib.sha256(f"{book.title}\n{stamp}".encode()).hexdigest()


def _sources(book: Book) -> list[str]:
    hosts = {urlparse(q.source_url).netloc.removeprefix("www.") for q in book.questions if q.source_url}
    return sorted(h for h in hosts if h)


async def book_html(book: Book) -> str:
    storage = get_storage()
    printed: list[tuple[Chapter, list[PrintedPart]]] = []
    for chapter in book.chapters:
        parts = []
        for heading, qs in chapter.lessons:
            found = await build_parts(qs, storage)
            for part in found:
                if heading:  # the lesson's name; its question type too when the lesson mixes several
                    part.title = heading if len(found) == 1 else f"{heading} – {part.title}"
                parts.append(part)
        printed.append((chapter, parts))
    return templates.get_template("pdf/book.html").render(
        title=book.title,
        chapters=printed,
        total=len(book.questions),
        sources=_sources(book),
        date=datetime.now(VN).strftime("%m/%Y"),
    )


async def compile_books(session: AsyncSession, render: Renderer | None = None, only: str | None = None) -> dict:
    """Print the books whose questions changed and file them in the library."""
    stats = {"printed": 0, "unchanged": 0, "failed": 0}
    render, bronze = render or chromium_pdf, get_bronze_storage()
    for book in await plan_books(session):
        if only and only.casefold() not in book.title.casefold():
            continue
        digest = checksum(book)
        doc = await session.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == book.key))
        if doc is not None and doc.checksum == digest:
            stats["unchanged"] += 1
            continue
        try:
            pdf = await render(await book_html(book), book.title)
        except Exception:
            log.exception("Book %s could not be printed", book.title)
            stats["failed"] += 1
            continue
        key = f"books/{book.key.replace(':', '-')}/{digest[:16]}.pdf"
        await bronze.put(key, pdf, "application/pdf")
        if doc is None:
            doc = LibraryDocument(source="book", source_file_id=book.key, kind=LibraryKind.BOOK, mime="application/pdf")
            session.add(doc)
        doc.title, doc.folder_path, doc.status = book.title[:500], [FOLDER, book.root], LibraryStatus.PUBLISHED
        doc.storage_key, doc.size, doc.checksum, doc.mime = key, len(pdf), digest, "application/pdf"
        doc.authors, doc.year = "Exam System biên soạn", datetime.now(VN).year
        sources = ", ".join(_sources(book)[:4])
        doc.license_note = f"Exam System biên soạn{' · câu hỏi tổng hợp từ ' + sources if sources else ''}"[:300]
        await session.commit()
        stats["printed"] += 1
        log.info("Book printed: %s (%s questions)", book.title, len(book.questions))
    return stats
