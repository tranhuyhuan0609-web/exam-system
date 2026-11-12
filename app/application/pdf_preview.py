"""Freemium 2-page PDF preview slicer & watermarker using PyMuPDF (fitz).

When a PRO study material / HSG document is uploaded or viewed by a Free account:
- The full PDF remains locked inside the private vault (`bronze-raw` / `get_bronze_storage()`).
- PyMuPDF extracts only the first `max_pages` (default 2) pages, stamps a watermark header/footer,
  and stores the preview PDF under `previews/...` in public storage (`get_storage()`).
"""

import asyncio
import hashlib
import logging

import pymupdf
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import Storage, StorageError
from app.models import LibraryDocument

log = logging.getLogger(__name__)

PREVIEW_MAX_PAGES = 2


def slice_pdf_preview(pdf_bytes: bytes, max_pages: int = PREVIEW_MAX_PAGES) -> tuple[bytes, int]:
    """Return `(preview_pdf_bytes, total_page_count)` containing at most the first `max_pages` pages of `pdf_bytes`."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as src:
        total_pages = len(src)
        take = min(max(1, max_pages), total_pages)
        with pymupdf.open() as preview:
            preview.insert_pdf(src, from_page=0, to_page=take - 1)
            for idx, page in enumerate(preview, start=1):
                rect = page.rect
                banner = (
                    f"BAN XEM THU ({idx}/{take} - Tong {total_pages} trang) · "
                    "Nang cap tai khoan PRO de xem tron bo tai lieu"
                )
                footer_rect = pymupdf.Rect(16, rect.height - 22, rect.width - 16, rect.height - 4)
                page.draw_rect(footer_rect, color=(0.95, 0.96, 0.98), fill=(0.95, 0.96, 0.98))
                page.insert_textbox(
                    footer_rect,
                    banner,
                    fontsize=8.5,
                    color=(0.45, 0.18, 0.05),
                    align=pymupdf.TEXT_ALIGN_CENTER,
                )
            return preview.tobytes(garbage=3, deflate=True), total_pages


def count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as src:
            return len(src)
    except Exception:
        return 0


def stamp_user_watermark(pdf_bytes: bytes, watermark_text: str) -> bytes:
    """Optionally stamp the Pro user's email/name onto the corner of each PDF page to deter sharing."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                rect = page.rect
                box = pymupdf.Rect(16, 4, rect.width - 16, 18)
                page.insert_textbox(
                    box,
                    watermark_text,
                    fontsize=7.5,
                    color=(0.55, 0.55, 0.55),
                    align=pymupdf.TEXT_ALIGN_RIGHT,
                )
            return doc.tobytes(garbage=2, deflate=True)
    except Exception:
        return pdf_bytes


async def ensure_document_preview(
    session: AsyncSession,
    doc: LibraryDocument,
    bronze: Storage,
    public_storage: Storage,
    max_pages: int = PREVIEW_MAX_PAGES,
) -> tuple[bytes, int] | None:
    """Ensure `doc.preview_storage_key` exists in `public_storage` and return `(preview_bytes, total_pages)`."""
    if doc.mime != "application/pdf" or not doc.storage_key:
        return None
    try:
        raw_pdf = await bronze.get(doc.storage_key)
    except StorageError:
        return None

    try:
        preview_bytes, total_pages = await asyncio.to_thread(slice_pdf_preview, raw_pdf, max_pages)
    except Exception as exc:
        log.warning("Cannot slice preview for doc %s: %s", doc.id, exc)
        return None

    digest = (doc.checksum or hashlib.sha256(raw_pdf).hexdigest())[:12]
    key = doc.preview_storage_key or f"previews/doc-{doc.id or 'tmp'}-{digest}-p{max_pages}.pdf"
    if not await public_storage.exists(key):
        await public_storage.put(key, preview_bytes, "application/pdf")
    if doc.preview_storage_key != key:
        doc.preview_storage_key = key
        await session.commit()
    return preview_bytes, total_pages

