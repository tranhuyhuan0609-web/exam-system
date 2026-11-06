"""Anthologies: open papers of one folder (a field, a subject) bound as one PDF, with a cover and a table of contents
that names every paper's authors, year and source and licence (CC BY asks for that).

Papers are bound in fixed groups of SIZE in the order they were stored, and only full groups: an anthology never
changes once printed, the next one starts where it stopped.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exam_pdf import Renderer, chromium_pdf
from app.infrastructure.storage import StorageError, get_bronze_storage
from app.models import LibraryDocument, LibraryKind, LibraryStatus
from app.templating import templates

log = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))
SIZE = 20
FOLDER = "Tuyển tập nghiên cứu"
SOURCES = ("openalex", "semanticscholar", "crossref")


def _bind(cover: bytes, papers: list[bytes]) -> tuple[bytes, list[int]]:
    """The cover followed by the papers; returns the PDF and the first page of each paper (1-based)."""
    import fitz  # PyMuPDF

    out, starts = fitz.open(stream=cover, filetype="pdf"), []
    for data in papers:
        starts.append(out.page_count + 1)
        with fitz.open(stream=data, filetype="pdf") as paper:
            out.insert_pdf(paper)
    return out.tobytes(garbage=3, deflate=True), starts


async def _cover(title: str, papers: list[LibraryDocument], starts: list[int] | None, render: Renderer) -> bytes:
    html = templates.get_template("pdf/anthology.html").render(
        title=title, papers=papers, starts=starts, date=datetime.now(VN).strftime("%m/%Y")
    )
    return await render(html, title)


async def compile_anthologies(session: AsyncSession, render: Renderer | None = None, limit: int = 20) -> dict:
    """Up to `limit` new anthologies, from the folders with a full group of stored papers not yet bound."""
    stats = {"printed": 0, "failed": 0}
    render, bronze = render or chromium_pdf, get_bronze_storage()
    stored = (
        (LibraryDocument.source.in_(SOURCES))
        & (LibraryDocument.kind == LibraryKind.PAPER)
        & (LibraryDocument.storage_key.like("open/%"))
        & (LibraryDocument.status == LibraryStatus.PUBLISHED)
        # Creative Commons only: "open access" alone does not always allow printing a paper again
        & (LibraryDocument.license_note.ilike("cc%"))
    )
    folders = (
        await session.execute(
            select(LibraryDocument.folder_path, func.count()).where(stored).group_by(LibraryDocument.folder_path)
        )
    ).all()
    for folder, count in sorted(folders, key=lambda row: -row[1]):
        for number in range(1, count // SIZE + 1):
            if stats["printed"] >= limit:
                return stats
            key = "anthology:" + hashlib.sha1("/".join(folder).encode()).hexdigest()[:16] + f":{number}"
            if await session.scalar(select(LibraryDocument.id).where(LibraryDocument.source_file_id == key)):
                continue
            papers = (
                await session.scalars(
                    select(LibraryDocument)
                    .where(stored, LibraryDocument.folder_path == folder)
                    .order_by(LibraryDocument.id)
                    .offset((number - 1) * SIZE)
                    .limit(SIZE)
                )
            ).all()
            title = f"Tuyển tập nghiên cứu {folder[-1] if folder else ''} – Số {number}".replace("  ", " ")
            try:
                files = [await bronze.get(p.storage_key) for p in papers]
                first = await _cover(title, papers, None, render)  # page count of the cover, then the real one
                _, starts = await asyncio.to_thread(_bind, first, files)
                pdf, _ = await asyncio.to_thread(_bind, await _cover(title, papers, starts, render), files)
            except (StorageError, RuntimeError, ValueError) as exc:  # a broken paper PDF: try again with others later
                log.warning("Anthology %s: %s", title, exc)
                stats["failed"] += 1
                continue
            digest = hashlib.sha256(pdf).hexdigest()
            storage_key = f"books/anthology/{digest[:2]}/{digest}.pdf"
            await bronze.put(storage_key, pdf, "application/pdf")
            years = sorted({p.year for p in papers if p.year})
            session.add(
                LibraryDocument(
                    title=title,
                    kind=LibraryKind.PAPER,
                    status=LibraryStatus.PUBLISHED,
                    folder_path=[FOLDER, *folder],
                    source="anthology",
                    source_file_id=key,
                    storage_key=storage_key,
                    mime="application/pdf",
                    size=len(pdf),
                    checksum=digest,
                    authors=f"Exam System tuyển chọn · {len(papers)} bài",
                    year=years[-1] if years else None,
                    license_note="Tuyển tập bài báo mở; tác giả, nguồn và giấy phép từng bài ở mục lục",
                )
            )
            await session.commit()
            stats["printed"] += 1
            log.info("Anthology printed: %s", title)
    return stats
