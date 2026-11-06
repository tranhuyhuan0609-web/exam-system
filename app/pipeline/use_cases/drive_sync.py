"""Google Drive folder -> library (and, for chosen files, the AI exam import).

configs/drive/<source_id>.yaml says which top-level folders to take and how: kind, whether they are published
right away (official exams) or wait for an admin (teacher contributions with unclear rights), whether teachers
may download them, and which files to also turn into questions (AI import, Gemini quota permitting).

    Drive -> bronze-raw bucket + MongoDB raw_documents (Bronze) -> library_documents (+ import_jobs)

Files are fetched again only when their Drive checksum changes. A file bundling many exams is also cut into
one document per exam (use_cases/library_split.py).
"""

import hashlib
import logging
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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


class DriveSource(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    folder_id: str
    max_files: int = 200  # per run
    include: list[FolderRule]


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
    stats = {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0, "import_jobs": 0, "split_parts": 0}
    budget = max_files or source.max_files
    rules = {_key(rule.folder): rule for rule in source.include}
    for top in await drive.children(source.folder_id):
        rule = rules.get(_key(top.name))
        if rule is None or not top.is_folder:
            continue
        async for path, file in _walk(drive, top.id, [top.name]):
            if budget <= 0:
                return stats
            ext = KEEP.get(file.mime)
            if ext is None:
                stats["skipped"] += 1
                continue
            existing = await session.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == file.id))
            if existing is not None and file.md5 and existing.checksum == file.md5:
                stats["unchanged"] += 1
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
            checksum = file.md5 or hashlib.sha256(data).hexdigest()
            key = f"drive/{source.source_id}/{file.id}{ext}"
            await bronze.put(key, data, mime)
            await store.save(
                RawDocument(
                    doc_id(f"drive_{source.source_id}", file.id, checksum),
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
            doc = existing or LibraryDocument(source=f"drive:{source.source_id}", source_file_id=file.id)
            doc.title = (PurePosixPath(file.name).stem or file.name)[:500]
            doc.kind, doc.folder_path = rule.kind, path
            doc.source_url = f"https://drive.google.com/file/d/{file.id}/view"
            doc.checksum, doc.storage_key, doc.mime, doc.size = checksum, key, mime, len(data)
            doc.updated_at = datetime.now(UTC)
            if existing is None:  # an admin's later decisions (status, download) are not overwritten
                doc.status, doc.downloadable = rule.status, rule.downloadable
                doc.license_note = rule.license_note or None
                session.add(doc)
            stats["updated" if existing else "new"] += 1
            where = unicodedata.normalize("NFC", " / ".join([*path, file.name]))
            target = next((t for t in rule.targets if re.search(t.match, where, re.I)), None)
            if target:
                doc.category_id = (await ensure_path(session, target.category)).id
            await session.flush()
            # A bundle of many exams becomes one document (and one AI import) per exam.
            parts = await split_document(session, doc, data, bronze, soffice)
            stats["split_parts"] += len(parts)
            if target and ext in IMPORTABLE and doc.import_job_id is None:
                stage = f"Thư viện: {source.name}"
                pieces = [(f"{doc.title} – {c.title}.pdf", c, part, ".pdf") for c, part in parts]
                for name, holder, payload, suffix in pieces or [(file.name, doc, data, ext)]:
                    job = await queue_import(session, storage, name, payload, suffix, holder.category_id, stage)
                    holder.import_job_id = job.id
                    doc.import_job_id = doc.import_job_id or job.id
                    stats["import_jobs"] += 1
            await session.commit()
    return stats
