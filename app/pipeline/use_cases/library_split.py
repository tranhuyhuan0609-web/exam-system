"""Cut a library document that bundles many exams into one document per exam (see domain/exam_split.py).

The parts are PDFs stored next to the original, listed in a folder named after it, and inherit its status,
kind and download rights (an admin can still change each part). Word files are converted to PDF first;
scanned pages are read with Tesseract OCR (header and footer only) when TESSDATA_DIR is set.
"""

import asyncio
import hashlib
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pymupdf
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.normalize import NormalizeError, docx_to_pdf
from app.config import get_settings
from app.models import Category, Difficulty, ImportJob, JobStatus, LibraryDocument
from app.pipeline.domain.exam_split import Segment, find_exams

log = logging.getLogger(__name__)
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


SCANNED = 30  # a page with fewer characters of text is a scan: read its header and footer with OCR
HEAD, FOOT = 0.35, 0.08  # parts of the page height where the exam code and the page number are printed


def _ocr(page: pymupdf.Page, clip: pymupdf.Rect, tessdata: str) -> str:
    pixmap = page.get_pixmap(dpi=120, clip=clip)  # RGB: Tesseract reads nothing from a grey pixmap here
    with pymupdf.open("pdf", pixmap.pdfocr_tobytes(language="vie", tessdata=tessdata)) as scanned:
        return scanned[0].get_text("text")


def page_texts(pdf: bytes, tessdata: str = "") -> list[str]:
    """The text of every page; scanned pages are read with Tesseract (top and bottom only) when configured."""
    texts = []
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for page in document:
            text = page.get_text("text")
            if tessdata and len(text.strip()) < SCANNED:
                r = page.rect
                head = _ocr(page, pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * HEAD), tessdata)
                foot = _ocr(page, pymupdf.Rect(r.x0, r.y1 - r.height * FOOT, r.x1, r.y1), tessdata)
                text = head + "\n" + foot
            texts.append(text)
    return texts


def cut(pdf: bytes, segment: Segment) -> bytes:
    with pymupdf.open(stream=pdf, filetype="pdf") as source, pymupdf.open() as part:
        part.insert_pdf(source, from_page=segment.first, to_page=segment.last)
        return part.tobytes(garbage=3, deflate=True)


async def as_pdf(data: bytes, mime: str, soffice: str) -> bytes | None:
    if mime == PDF:
        return data
    if mime != DOCX:
        return None
    folder = Path(tempfile.mkdtemp(prefix="split-"))
    source = folder / "document.docx"
    source.write_bytes(data)
    try:
        return (await docx_to_pdf(source, soffice)).read_bytes()
    except NormalizeError as exc:  # no LibreOffice on this machine: the Word file stays whole
        log.info("Not splitting a Word file: %s", exc)
        return None


async def split_document(
    session: AsyncSession, doc: LibraryDocument, data: bytes, bronze, soffice: str
) -> list[tuple[LibraryDocument, bytes]]:
    """Replace `doc`'s parts with the exams found in it; [] when it is not a bundle. Needs doc.id (flushed)."""
    pdf = await as_pdf(data, doc.mime, soffice)
    if pdf is None:
        return []
    try:
        segments = find_exams(await asyncio.to_thread(page_texts, pdf, get_settings().tessdata_dir))
    except (RuntimeError, ValueError) as exc:  # damaged or encrypted PDF
        log.warning("Cannot read %s: %s", doc.title, exc)
        return []
    if not segments:
        return []
    await session.execute(delete(LibraryDocument).where(LibraryDocument.parent_id == doc.id))
    stem = str(PurePosixPath(doc.storage_key).with_suffix(""))
    parts = []
    for segment in segments:
        part = await asyncio.to_thread(cut, pdf, segment)
        pages = f"p{segment.first + 1}-{segment.last + 1}"
        key = f"{stem}__{pages}.pdf"
        await bronze.put(key, part, PDF)
        child = LibraryDocument(
            title=segment.label,
            kind=doc.kind,
            status=doc.status,
            folder_path=[*doc.folder_path, doc.title],
            category_id=doc.category_id,
            source=doc.source,
            source_file_id=f"{doc.source_file_id or doc.id}#{pages}"[:200],
            source_url=doc.source_url,
            checksum=hashlib.sha256(part).hexdigest(),
            storage_key=key,
            mime=PDF,
            size=len(part),
            downloadable=doc.downloadable,
            license_note=doc.license_note,
            parent_id=doc.id,
            page_from=segment.first + 1,
            page_to=segment.last + 1,
            updated_at=datetime.now(UTC),
        )
        session.add(child)
        parts.append((child, part))
    await session.flush()
    log.info("Split %s into %d exams", doc.title, len(parts))
    return parts


async def queue_import(
    session: AsyncSession, storage, name: str, data: bytes, ext: str, category_id: int, stage: str
) -> ImportJob:
    """Hand a file to the AI exam import (the worker turns it into questions under this category)."""
    category = await session.get(Category, category_id)
    raw_key = f"raw/library-{hashlib.sha256(data).hexdigest()[:24]}{ext}"
    await storage.put(raw_key, data, "application/pdf" if ext == ".pdf" else "application/octet-stream")
    job = ImportJob(
        original_filename=name[:300],
        stored_path=raw_key,
        status=JobStatus.QUEUED,
        stage=stage,
        root_id=category.root_id,
        category_id=category.id,
        default_difficulty=Difficulty.UNDERSTANDING,
    )
    session.add(job)
    await session.flush()
    return job


async def split_stored(session: AsyncSession, doc: LibraryDocument, bronze, storage, soffice: str) -> int:
    """Split a document already in the library (admin button, `library-split` command). If the AI import of the
    whole bundle has not started yet, it is replaced by one import per exam."""
    if doc.parent_id is not None or not doc.storage_key:  # a part, or a book listed without its file
        return 0
    # Imports still waiting for the worker: the bundle's own, and those of the parts about to be replaced.
    children = select(LibraryDocument.import_job_id).where(LibraryDocument.parent_id == doc.id)
    waiting = (
        await session.scalars(
            select(ImportJob).where(
                ImportJob.status == JobStatus.QUEUED,
                (ImportJob.id == doc.import_job_id) | ImportJob.id.in_(children),
            )
        )
    ).all()
    parts = await split_document(session, doc, await bronze.get(doc.storage_key), bronze, soffice)
    if parts and waiting and doc.category_id is not None:
        stage = waiting[0].stage or "Thư viện"
        doc.import_job_id = None
        for job in waiting:
            await session.delete(job)
        for child, data in parts:
            name = f"{doc.title} – {child.title}.pdf"
            child.import_job_id = (await queue_import(session, storage, name, data, ".pdf", doc.category_id, stage)).id
            doc.import_job_id = doc.import_job_id or child.import_job_id
    await session.commit()
    return len(parts)
