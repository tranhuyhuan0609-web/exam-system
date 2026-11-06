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
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.normalize import NormalizeError, docx_to_pdf
from app.config import get_settings
from app.models import LibraryDocument
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


async def split_stored(session: AsyncSession, doc: LibraryDocument, bronze, soffice: str) -> int:
    """Split a document already in the library (admin button, `library-split` command). Parts only, no AI import."""
    if doc.parent_id is not None:
        return 0
    parts = await split_document(session, doc, await bronze.get(doc.storage_key), bronze, soffice)
    await session.commit()
    return len(parts)
