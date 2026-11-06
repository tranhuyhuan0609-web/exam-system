"""Open PDFs of harvested works, kept in the bronze-raw bucket so the library shows them in its own reader."""

import asyncio
import hashlib
import logging

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.infrastructure.storage import get_bronze_storage
from app.models import LibraryDocument
from app.pipeline.harvest.sources import USER_AGENT

log = logging.getLogger(__name__)


class NotAPdf(Exception):
    pass


async def _internet_archive_pdf(client: httpx.AsyncClient, identifier: str, max_bytes: int) -> str:
    """The item's PDF: the scan with a text layer when there is one."""
    response = await client.get(f"https://archive.org/metadata/{identifier}")
    response.raise_for_status()
    files = [f for f in response.json().get("files", []) if f.get("name", "").lower().endswith(".pdf")]
    files = [f for f in files if int(f.get("size") or 0) <= max_bytes]
    if not files:
        raise NotAPdf("no PDF in the item")
    files.sort(key=lambda f: (f.get("format") != "Text PDF", int(f.get("size") or 0)))
    return f"https://archive.org/download/{identifier}/{files[0]['name']}"


async def fetch_pdf(client: httpx.AsyncClient, url: str, max_bytes: int) -> bytes:
    if url.startswith("ia:"):
        url = await _internet_archive_pdf(client, url[3:], max_bytes)
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        if int(response.headers.get("content-length") or 0) > max_bytes:
            raise NotAPdf("too large")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data += chunk
            if len(data) > max_bytes:
                raise NotAPdf("too large")
    if not bytes(data[:1024]).lstrip().startswith(b"%PDF"):
        raise NotAPdf("not a PDF (a landing page or a login wall)")  # many "pdf_url"s are HTML pages
    return bytes(data)


async def store_files(limit: int = 200, max_mb: int = 40, concurrency: int = 4) -> dict[str, int]:
    """PDFs of documents listed with a file_url and no stored file yet. A link that gives no PDF is dropped,
    so the document stays listed with its link to the source and is not tried again."""
    stats = {"stored": 0, "failed": 0}
    async with SessionLocal() as session:
        ids = list(
            await session.scalars(
                select(LibraryDocument.id)
                .where(LibraryDocument.file_url.is_not(None), LibraryDocument.storage_key == "")
                .order_by(LibraryDocument.id)
                .limit(limit)
            )
        )
    if not ids:
        return stats
    storage, gate = get_bronze_storage(), asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:

        async def one(document_id: int) -> None:
            async with gate, SessionLocal() as session:
                doc = await session.get(LibraryDocument, document_id)
                try:
                    data = await fetch_pdf(client, doc.file_url, max_mb * 1024 * 1024)
                except (httpx.HTTPError, NotAPdf, ValueError) as exc:
                    log.info("No PDF for %s (%s): %s", doc.id, doc.file_url, exc)
                    doc.file_url = None
                    stats["failed"] += 1
                else:
                    digest = hashlib.sha256(data).hexdigest()
                    key = f"open/{doc.source}/{digest[:2]}/{digest}.pdf"
                    await storage.put(key, data, "application/pdf")
                    doc.storage_key, doc.mime, doc.size, doc.checksum = key, "application/pdf", len(data), digest
                    stats["stored"] += 1
                await session.commit()

        await asyncio.gather(*(one(i) for i in ids))
    return stats
