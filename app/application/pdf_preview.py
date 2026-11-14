"""Freemium 2-page PDF preview slicer & watermarker using the PDF engine adapter (pypdfium2 + pypdf + reportlab).

When a PRO study material / HSG document is uploaded or viewed by a Free account:
- The full PDF remains locked inside the private vault (`bronze-raw` / `get_bronze_storage()`).
- PyMuPDF extracts only the first `max_pages` (default 2) pages, stamps a watermark header/footer,
  and stores the preview PDF under `previews/...` in public storage (`get_storage()`).
"""

import asyncio
import hashlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.pdf_engine import PDFEngineAdapter, Rect, TextStamp, slice_pdf, stamp_pages
from app.infrastructure.storage import Storage, StorageError
from app.models import LibraryDocument

log = logging.getLogger(__name__)

PREVIEW_MAX_PAGES = 2


def slice_pdf_preview(pdf_bytes: bytes, max_pages: int = PREVIEW_MAX_PAGES) -> tuple[bytes, int]:
    """Return `(preview_pdf_bytes, total_page_count)` containing at most the first `max_pages` pages of `pdf_bytes`."""
    with PDFEngineAdapter.open(pdf_bytes) as src:
        total_pages = src.page_count
    take = min(max(1, max_pages), total_pages)
    preview = slice_pdf(pdf_bytes, 0, take - 1)

    def banner(index: int, width: float, height: float) -> list[TextStamp]:
        text = (
            f"BAN XEM THU ({index + 1}/{take} - Tong {total_pages} trang) · "
            "Nang cap tai khoan PRO de xem tron bo tai lieu"
        )
        footer = Rect(16, height - 22, width - 16, height - 4)
        return [TextStamp(text, footer, 8.5, (0.45, 0.18, 0.05), "center", background=(0.95, 0.96, 0.98))]

    return stamp_pages(preview, banner), total_pages


def count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        with PDFEngineAdapter.open(pdf_bytes) as src:
            return src.page_count
    except Exception:
        return 0


def stamp_user_watermark(pdf_bytes: bytes, watermark_text: str) -> bytes:
    """Optionally stamp the Pro user's email/name onto the corner of each PDF page to deter sharing."""
    try:
        return stamp_pages(
            pdf_bytes,
            lambda _index, width, _height: [
                TextStamp(watermark_text, Rect(16, 4, width - 16, 18), 7.5, (0.55, 0.55, 0.55), "right")
            ],
        )
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
