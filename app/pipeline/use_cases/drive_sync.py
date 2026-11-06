"""Google Drive folder -> library (and, for chosen files, the AI exam import).

configs/drive/<source_id>.yaml says which top-level folders to take and how: kind, whether they are published
right away (official exams) or wait for an admin (teacher contributions with unclear rights), whether teachers
may download them, which files to also turn into questions (AI import, Gemini quota permitting), and which
folders are only listed (`index_only`: copyrighted books — name, folder and Drive link, the file is not copied).

Two halves, joined by Kafka (raw.library.ingestion) so that crawling never waits for anything downstream:

    crawl_drive:  Drive -> bronze-raw bucket + MongoDB raw_documents (Bronze) -> one event per file
    ingest_library_event:  event -> library_documents, bundles cut into exams, AI import jobs queued

`sync_drive` joins them directly (no Kafka), for the command line and the tests. Files are fetched again only
when their Drive version (checksum, or modification time for Google Docs) changes.
"""

import asyncio
import hashlib
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import Storage
from app.models import LibraryDocument, LibraryKind, LibraryStatus
from app.pipeline.adapters.drive import Drive, DriveBlocked, DriveError
from app.pipeline.domain.models import RawDocument, doc_id
from app.pipeline.ports import RawStore
from app.pipeline.use_cases.library_split import queue_import, split_document
from app.pipeline.use_cases.toc import ensure_path

log = logging.getLogger(__name__)

KEEP = {  # what the library stores (preview: PDF; the rest is download-only)
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.google-apps.document": ".pdf",
    "application/vnd.google-apps.presentation": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
IMPORTABLE = {".pdf", ".docx", ".png", ".jpg"}  # what the AI exam import reads
INDEX_ONLY_NOTE = "Chỉ lập chỉ mục: sách có bản quyền, chưa sao chép về hệ thống"

Event = dict[str, Any]
Emit = Callable[[Event], Awaitable[None]]
Known = Callable[[str], Awaitable[str | None]]  # Drive file id -> the version we already have


class Target(BaseModel):
    """Files whose name matches go to the AI import under this category path (our table of contents)."""

    match: str
    category: list[str]


class FolderRule(BaseModel):
    folder: str  # exact name of a folder directly under the source folder
    kind: LibraryKind = LibraryKind.OTHER
    status: LibraryStatus = LibraryStatus.PENDING_REVIEW
    downloadable: bool = False
    license_note: str = ""
    targets: list[Target] = []
    index_only: bool = False  # list the files (name, folder, link) without copying them


class DriveSource(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    folder_id: str
    max_files: int = 30  # downloads per run: Google blocks bursts of automated downloads
    every_hours: float = 3  # the scheduler runs the source again after this long (sooner after a block: never)
    download_delay: float = 2.0  # seconds between downloads
    include: list[FolderRule]

    def rule_for(self, top_folder: str) -> FolderRule | None:
        return next((r for r in self.include if _key(r.folder) == _key(top_folder)), None)


def load_sources(directory: str | Path) -> dict[str, DriveSource]:
    out = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        source = DriveSource.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        out[source.source_id] = source
    return out


def _key(name: str) -> str:
    """Folder names compared NFC + case-insensitive (Drive may store decomposed Vietnamese)."""
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


async def _walk(drive: Drive, folder_id: str, path: list[str]):
    for item in await drive.children(folder_id):
        if item.is_folder:
            async for found in _walk(drive, item.id, [*path, item.name]):
                yield found
        else:
            yield path, item


def known_in(session: AsyncSession) -> Known:
    async def known(file_id: str) -> str | None:
        return await session.scalar(select(LibraryDocument.checksum).where(LibraryDocument.source_file_id == file_id))

    return known


async def crawl_drive(
    source: DriveSource,
    drive: Drive,
    bronze: Storage,
    store: RawStore,
    emit: Emit,
    known: Known,
    max_files: int | None = None,
    pause: float | None = None,
) -> dict[str, int]:
    """Fetch new and changed files into Bronze and announce each one. Stops at Google's block page; the next
    run resumes (files already fetched are skipped)."""
    stats = {"new": 0, "unchanged": 0, "indexed": 0, "skipped": 0, "failed": 0}
    budget = max_files or source.max_files
    delay = source.download_delay if pause is None else pause
    for top in await drive.children(source.folder_id):
        rule = source.rule_for(top.name)
        if rule is None or not top.is_folder:
            continue
        async for path, file in _walk(drive, top.id, [top.name]):
            ext = KEEP.get(file.mime)
            if ext is None:
                stats["skipped"] += 1
                continue
            version = file.version
            if version and await known(file.id) == version:
                stats["unchanged"] += 1
                continue
            event = {
                "source_id": source.source_id,
                "file_id": file.id,
                "name": file.name,
                "path": path,
                "ext": ext,
                "mime": file.mime,
                "size": file.size,
                "version": version,
                "index_only": rule.index_only,
            }
            if rule.index_only:  # copyrighted books: listed, not copied
                await emit(event)
                stats["indexed"] += 1
                continue
            if budget <= 0:  # left for the next run; listing (and indexing books) goes on, it downloads nothing
                stats["more"] = 1
                continue
            budget -= 1
            try:
                data, mime = await drive.download(file)
            except DriveBlocked as exc:  # Google asks automated traffic to stop: stop; the next run resumes
                log.warning("Drive sync paused: %s", exc)
                stats["blocked"] = 1
                return stats
            except (DriveError, OSError) as exc:
                log.warning("Drive file %s failed: %s", file.name, exc)
                stats["failed"] += 1
                continue
            checksum = version or hashlib.sha256(data).hexdigest()
            key = f"drive/{source.source_id}/{file.id}{ext}"
            await bronze.put(key, data, mime)
            raw_id = doc_id(f"drive_{source.source_id}", file.id, checksum)
            await store.save(
                RawDocument(
                    raw_id,
                    f"drive_{source.source_id}",
                    f"https://drive.google.com/file/d/{file.id}",
                    "pdf" if ext == ".pdf" else ("docx" if ext == ".docx" else "image"),
                    datetime.now(UTC),
                    checksum,
                    storage_key=key,
                    title=file.name,
                    context={"folder": " / ".join(path)},
                )
            )
            await emit({**event, "mime": mime, "size": len(data), "version": checksum, "storage_key": key})
            stats["new"] += 1
            if delay:
                await asyncio.sleep(delay)
    return stats


async def ingest_library_event(
    session: AsyncSession,
    event: Event,
    source: DriveSource,
    bronze: Storage,
    storage: Storage,
    soffice: str = "soffice",
) -> dict[str, int]:
    """One fetched (or listed) Drive file -> its library document; a bundle is cut into exams, and the files
    matching an import target are queued for the AI (which works whenever it has quota)."""
    stats = {"split_parts": 0, "import_jobs": 0}
    path, name, ext = list(event["path"]), event["name"], event["ext"]
    rule = source.rule_for(path[0])
    if rule is None:  # the folder was removed from the configuration since
        return stats
    existing = await session.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == event["file_id"]))
    doc = existing or LibraryDocument(source=f"drive:{source.source_id}", source_file_id=event["file_id"])
    doc.title = (PurePosixPath(name).stem or name)[:500]
    doc.kind, doc.folder_path = rule.kind, path
    doc.source_url = f"https://drive.google.com/file/d/{event['file_id']}/view"
    doc.checksum, doc.mime, doc.size = event["version"], event["mime"], event["size"]
    doc.storage_key = event.get("storage_key") or ""  # "" = listed only (index_only)
    doc.updated_at = datetime.now(UTC)
    if existing is None:  # an admin's later decisions (status, download) are not overwritten
        doc.status, doc.downloadable = rule.status, rule.downloadable
        doc.license_note = rule.license_note or None
        if event.get("index_only"):
            doc.status, doc.downloadable, doc.license_note = LibraryStatus.HIDDEN, False, INDEX_ONLY_NOTE
        session.add(doc)
    if event.get("index_only"):
        await session.commit()
        return stats
    where = unicodedata.normalize("NFC", " / ".join([*path, name]))
    target = next((t for t in rule.targets if re.search(t.match, where, re.I)), None)
    if target:
        doc.category_id = (await ensure_path(session, target.category)).id
    await session.flush()
    data = await bronze.get(doc.storage_key)
    # A bundle of many exams becomes one document (and one AI import) per exam.
    parts = await split_document(session, doc, data, bronze, soffice)
    stats["split_parts"] = len(parts)
    if target and ext in IMPORTABLE and doc.import_job_id is None:
        stage = f"Thư viện: {source.name}"
        pieces = [(f"{doc.title} – {c.title}.pdf", c, part, ".pdf") for c, part in parts]
        for title, holder, payload, suffix in pieces or [(name, doc, data, ext)]:
            job = await queue_import(session, storage, title, payload, suffix, holder.category_id, stage)
            holder.import_job_id = job.id
            doc.import_job_id = doc.import_job_id or job.id
            stats["import_jobs"] += 1
    await session.commit()
    return stats


async def sync_drive(
    session: AsyncSession,
    source: DriveSource,
    drive: Drive,
    bronze: Storage,
    store: RawStore,
    storage: Storage,
    max_files: int | None = None,
    soffice: str = "soffice",
) -> dict[str, int]:
    """Both halves in one go, without Kafka (command line, tests)."""
    totals = {"split_parts": 0, "import_jobs": 0}

    async def ingest(event: Event) -> None:
        for name, n in (await ingest_library_event(session, event, source, bronze, storage, soffice)).items():
            totals[name] += n

    stats = await crawl_drive(source, drive, bronze, store, ingest, known_in(session), max_files, pause=0)
    return {**stats, **totals}
