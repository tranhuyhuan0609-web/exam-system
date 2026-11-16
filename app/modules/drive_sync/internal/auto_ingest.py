"""A03 auto-ingest: the PDFs of a Google Drive folder -> the question bank (exams) or the library (documents).

Drive v3 is read over HTTPS with `httpx`: `files.list` on the folder (`'<folder>' in parents and trashed = false`)
and `files.get?alt=media` per file. The credential is, in this order:
* the connection's `secret_vault_ref` when it is `env:<VARIABLE>`: an OAuth bearer token kept in that environment
  variable (the deployment's secret store fills it; nothing is stored in the database);
* `Settings.google_drive_api_key`, for a folder shared publicly ("anyone with the link");
otherwise the sync is refused (`DRIVE_CREDENTIALS_MISSING`), never attempted anonymously.

Each PDF is registered as a bronze asset + document version through A01 (`submit_import`: magic bytes, size cap,
dedup on SHA-256, so a second run skips what was already ingested), then split by
`extract_exam_structure_from_asset` (PDF and Word):
* an exam (at least one question with options or a key, or `mode=QUESTIONS`): every question is solved and tagged by
  the AI solver and stored as a DRAFT of the bank (`solve_and_ingest`, dedup on the content hash);
* a document (`mode=DOCUMENTS`, or nothing that reads as a question): a DOCUMENT content item (DRAFT) with the text
  of its first pages, in the library.

Three phases, so no database connection is held while Drive or the AI is slow: (1) a short transaction reads the
connection and the topics; (2) listing, download, extraction and solving run with NO transaction open (the AI at
most `SOLVE_CONCURRENCY` questions at a time; a short read in between skips what was ingested before, so it is
never solved again); (3) one short transaction per file registers it and stores its questions or document.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.infrastructure.ai_solver import AiSolver
from app.infrastructure.pdf_engine import PDFEngineAdapter, PDFEngineError, PDFPasswordRequired
from app.modules.content.public import ContentBlockDraft, ContentBlockType, create_content_item
from app.modules.drive_sync.internal.models import SourceConnection
from app.modules.drive_sync.internal.service import (
    DriveSyncNotFoundError,
    DriveSyncPolicyError,
    DriveSyncValidationError,
)
from app.modules.ingestion.public import IngestionValidationError, submit_import
from app.modules.parsers.public import extract_exam_structure_from_asset
from app.modules.questions.public import (
    PreparedQuestion,
    RawQuestion,
    load_taxonomy_hints,
    pictures_for,
    solve_many,
    store_prepared,
)
from app.platform.tenancy import DbRole, tenant_transaction

DRIVE_API = "https://www.googleapis.com/drive/v3"
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# What a Drive folder may hold that becomes questions or a document: PDFs and Word files (Google Docs are not read).
INGESTIBLE_MIMES = {PDF_MIME: ".pdf", DOCX_MIME: ".docx"}
MAX_DRIVE_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_RUN = 50
SOLVE_CONCURRENCY = 4
MODES = ("AUTO", "QUESTIONS", "DOCUMENTS")
QUESTION_RIGHTS = frozenset({"OWNED", "LICENSED", "ALLOWED_DERIVATIVE"})
_FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")
_DOCUMENT_TEXT_CHARS = 8000


@dataclass(frozen=True, slots=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    size: int | None


@dataclass(frozen=True, slots=True)
class DriveFileOutcome:
    drive_file_id: str
    name: str
    kind: str  # EXAM | DOCUMENT | SKIPPED
    reason: str | None = None
    document_version_id: uuid.UUID | None = None
    question_ids: tuple[uuid.UUID, ...] = ()
    duplicate_question_ids: tuple[uuid.UUID, ...] = ()
    unresolved_questions: int = 0
    content_item_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class DriveIngestResult:
    connection_id: uuid.UUID
    folder_id: str
    listed: int
    files: tuple[DriveFileOutcome, ...] = field(default_factory=tuple)

    @property
    def created_question_count(self) -> int:
        return sum(len(f.question_ids) for f in self.files)

    @property
    def content_item_count(self) -> int:
        return sum(1 for f in self.files if f.content_item_id is not None)


class DriveApiClient:
    """Drive v3 over httpx; `transport` replaces the network in tests."""

    def __init__(self, *, api_key: str | None = None, bearer_token: str | None = None, transport: Any = None) -> None:
        if not api_key and not bearer_token:
            raise DriveSyncPolicyError("DRIVE_CREDENTIALS_MISSING", "no Drive API key nor OAuth token is configured")
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.transport = transport

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        if self.bearer_token:
            return {"Authorization": f"Bearer {self.bearer_token}"}, {}
        return {}, {"key": str(self.api_key)}

    async def list_folder(self, folder_id: str, *, limit: int) -> list[DriveFile]:
        headers, params = self._auth()
        files: list[DriveFile] = []
        page_token: str | None = None
        async with httpx.AsyncClient(transport=self.transport, timeout=30.0) as client:
            while len(files) < limit:
                query = {
                    **params,
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "nextPageToken, files(id, name, mimeType, size)",
                    "pageSize": str(min(100, limit)),
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                }
                if page_token:
                    query["pageToken"] = page_token
                response = await client.get(f"{DRIVE_API}/files", params=query, headers=headers)
                if response.status_code in (401, 403, 404):
                    raise DriveSyncPolicyError(
                        "DRIVE_ACCESS_DENIED", f"Drive answered {response.status_code} for folder {folder_id}"
                    )
                response.raise_for_status()
                body = response.json()
                for item in body.get("files") or []:
                    size = item.get("size")
                    files.append(
                        DriveFile(
                            id=str(item["id"]),
                            name=str(item.get("name") or item["id"]),
                            mime_type=str(item.get("mimeType") or ""),
                            size=int(size) if size is not None else None,
                        )
                    )
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return files[:limit]

    async def download(self, file_id: str, *, max_bytes: int = MAX_DRIVE_FILE_BYTES) -> bytes:
        headers, params = self._auth()
        async with (
            httpx.AsyncClient(transport=self.transport, timeout=60.0) as client,
            client.stream(
                "GET", f"{DRIVE_API}/files/{file_id}", params={**params, "alt": "media"}, headers=headers
            ) as response,
        ):
            if response.status_code != 200:
                raise DriveSyncValidationError("DRIVE_DOWNLOAD_FAILED", f"HTTP {response.status_code}")
            out = bytearray()
            async for chunk in response.aiter_bytes():
                out += chunk
                if len(out) > max_bytes:
                    raise DriveSyncValidationError("DRIVE_FILE_TOO_LARGE", f"larger than {max_bytes} bytes")
            return bytes(out)


def drive_client_for(
    secret_vault_ref: str, *, settings: Settings | None = None, transport: Any = None
) -> DriveApiClient:
    """The client of a connection: `env:<VAR>` holds an OAuth token, else the API key of the settings."""
    cfg = settings or get_settings()
    token = None
    if secret_vault_ref.startswith("env:"):
        token = os.environ.get(secret_vault_ref.removeprefix("env:").strip()) or None
    return DriveApiClient(api_key=cfg.google_drive_api_key or None, bearer_token=token, transport=transport)


def _slug(name: str) -> str:
    plain = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:60] or "tai-lieu"


def _pdf_text(raw: bytes, *, pages: int = 5) -> str:
    try:
        with PDFEngineAdapter.open(raw) as pdf:
            text_out = "\n".join(pdf.text(i) for i in range(min(pages, pdf.page_count)))
    except (PDFEngineError, PDFPasswordRequired):
        return ""
    return re.sub(r"[ \t]+", " ", text_out).strip()[:_DOCUMENT_TEXT_CHARS]


def _raw_questions(raw: bytes, mime_type: str, filename: str) -> list[RawQuestion]:
    artifact = extract_exam_structure_from_asset(mime_type=mime_type, filename=filename, raw_bytes=raw)
    drafts = [q for section in artifact.sections for q in section.questions]
    pictures = pictures_for(raw, drafts) if mime_type == PDF_MIME else [(None, ())] * len(drafts)
    return [
        RawQuestion(
            item_type=q.item_type,
            stem_text=q.stem_md,
            options=q.options_md,
            source_answer=q.paired_answer,
            points=q.points,
            source_label=q.original_question_label,
            image_png=image,
            figure_pngs=figures,
        )
        for q, (image, figures) in zip(drafts, pictures, strict=True)
    ]


def _looks_like_exam(questions: Sequence[RawQuestion]) -> bool:
    return any(q.options or q.source_answer for q in questions)


async def _active_connection(session: AsyncSession, *, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> Any:
    conn = (
        await session.execute(
            select(SourceConnection).where(
                SourceConnection.id == connection_id, SourceConnection.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise DriveSyncNotFoundError(f"Drive connection {connection_id} not found")
    if conn.status != "CONNECTED":
        raise DriveSyncPolicyError("DRIVE_CONNECTION_NOT_ACTIVE", f"connection status is {conn.status}")
    source = (
        await session.execute(
            text("SELECT policy_status, rights_status FROM sources WHERE id = :s AND tenant_id = :t"),
            {"s": conn.source_id, "t": tenant_id},
        )
    ).one_or_none()
    if source is None:
        raise DriveSyncNotFoundError(f"Source {conn.source_id} not found")
    if source.policy_status != "ACTIVE":
        raise DriveSyncPolicyError("SOURCE_POLICY_NOT_ACTIVE", f"source policy_status is {source.policy_status}")
    return conn, source.rights_status


@dataclass(slots=True)
class _Downloaded:
    item: DriveFile
    raw: bytes
    sha256: str
    questions: list[RawQuestion]
    is_exam: bool = False
    prepared: list[PreparedQuestion] = field(default_factory=list)
    document_text: str = ""


async def fetch_and_ingest_folder(
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    folder_id: str,
    created_by: uuid.UUID,
    solver: AiSolver,
    mode: str = "AUTO",
    max_files: int = 20,
    transport: Any = None,
    settings: Settings | None = None,
) -> DriveIngestResult:
    """List the folder, ingest every new PDF in it (see the module docstring); other files are reported, not read."""
    if mode not in MODES:
        raise DriveSyncValidationError("INVALID_MODE", f"mode must be one of {', '.join(MODES)}")
    if not _FOLDER_ID.fullmatch(folder_id):
        raise DriveSyncValidationError("INVALID_FOLDER_ID", "folder_id is not a Drive id")
    if not 1 <= max_files <= MAX_FILES_PER_RUN:
        raise DriveSyncValidationError("INVALID_MAX_FILES", f"max_files must be within 1..{MAX_FILES_PER_RUN}")

    # phase 1: the connection and the topics (short transaction)
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        conn, source_rights = await _active_connection(session, tenant_id=tenant_id, connection_id=connection_id)
        source_id, vault_ref = conn.source_id, conn.secret_vault_ref
        hints = await load_taxonomy_hints(session)
    rights = source_rights if source_rights in QUESTION_RIGHTS else "PENDING_REVIEW"

    # phase 2a: Drive (no transaction)
    client = drive_client_for(vault_ref, settings=settings, transport=transport)
    listed = await client.list_folder(folder_id, limit=max_files)
    outcomes: dict[str, DriveFileOutcome] = {}
    downloaded: list[_Downloaded] = []
    for item in listed:
        if item.mime_type not in INGESTIBLE_MIMES:
            outcomes[item.id] = DriveFileOutcome(item.id, item.name, "SKIPPED", f"UNSUPPORTED_MIME:{item.mime_type}")
            continue
        if item.size is not None and item.size > MAX_DRIVE_FILE_BYTES:
            outcomes[item.id] = DriveFileOutcome(item.id, item.name, "SKIPPED", "DRIVE_FILE_TOO_LARGE")
            continue
        try:
            raw = await client.download(item.id)
        except DriveSyncValidationError as exc:
            outcomes[item.id] = DriveFileOutcome(item.id, item.name, "SKIPPED", exc.code)
            continue
        downloaded.append(_Downloaded(item, raw, hashlib.sha256(raw).hexdigest(), []))

    # phase 2b: what was ingested before is skipped (a short read), the rest is split and solved (no transaction)
    known: dict[str, uuid.UUID] = {}
    if downloaded:
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            rows = await session.execute(
                text("SELECT checksum_sha256, id FROM document_versions WHERE checksum_sha256 = ANY(:s)"),
                {"s": [d.sha256 for d in downloaded]},
            )
            known = {r.checksum_sha256: r.id for r in rows}
    todo: list[_Downloaded] = []
    for d in downloaded:
        if d.sha256 in known:
            outcomes[d.item.id] = DriveFileOutcome(
                d.item.id, d.item.name, "SKIPPED", "ALREADY_INGESTED", document_version_id=known[d.sha256]
            )
        else:
            todo.append(d)
    for d in todo:
        ext = INGESTIBLE_MIMES[d.item.mime_type]
        d.questions = await asyncio.to_thread(_raw_questions, d.raw, d.item.mime_type, f"{_slug(d.item.name)}{ext}")
        d.is_exam = bool(d.questions) and (mode == "QUESTIONS" or (mode == "AUTO" and _looks_like_exam(d.questions)))
        if d.is_exam:
            d.prepared = await solve_many(solver, d.questions, hints, concurrency=SOLVE_CONCURRENCY)
        else:
            d.document_text = await asyncio.to_thread(_pdf_text, d.raw) if d.item.mime_type == PDF_MIME else ""

    # phase 3: one short transaction per file
    for d in todo:
        item = d.item
        ext = INGESTIBLE_MIMES[item.mime_type]
        filename = item.name if item.name.lower().endswith(ext) else f"{item.name}{ext}"
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            try:
                registered = await submit_import(
                    session,
                    tenant_id=tenant_id,
                    raw_stream=d.raw,
                    filename=_slug(filename.removesuffix(ext)) + ext,
                    job_type="DRIVE_SYNC",
                    source_id=source_id,
                    external_id=f"gdrive:{item.id}",
                    title=item.name,
                )
            except IngestionValidationError as exc:
                outcomes[item.id] = DriveFileOutcome(item.id, item.name, "SKIPPED", f"INVALID_FILE:{exc}"[:200])
                continue
            if registered.is_duplicate:
                outcomes[item.id] = DriveFileOutcome(
                    item.id, item.name, "SKIPPED", "ALREADY_INGESTED",
                    document_version_id=registered.document_version.id,
                )  # fmt: skip
                continue
            dv_id = registered.document_version.id
            if d.is_exam:
                created: list[uuid.UUID] = []
                duplicates: list[uuid.UUID] = []
                unresolved = 0
                for n, prepared in enumerate(d.prepared, start=1):
                    result = await store_prepared(
                        session,
                        prepared=prepared,
                        tenant_id=tenant_id,
                        created_by=created_by,
                        stable_code=f"GDRIVE-{item.id[:12]}-Q{n:03d}",
                        rights_status=rights,
                        provenance={
                            "drive_file_id": item.id,
                            "document_version_id": str(dv_id),
                            "label": prepared.raw.source_label,
                        },
                    )
                    if result.ingested is None:
                        unresolved += 1
                    elif result.ingested.created:
                        created.append(result.ingested.question.id)
                    else:
                        duplicates.append(result.ingested.question.id)
                outcomes[item.id] = DriveFileOutcome(
                    item.id,
                    item.name,
                    "EXAM",
                    None,
                    dv_id,
                    tuple(created),
                    tuple(dict.fromkeys(duplicates)),
                    unresolved,
                )
                continue
            body = d.document_text
            content, _version = await create_content_item(
                session,
                tenant_id=tenant_id,
                created_by=created_by,
                content_type="DOCUMENT",
                slug=f"gdrive-{_slug(item.name)}-{item.id[:8].lower()}",
                title=item.name[:500],
                summary_md=(body[:480] + "…") if len(body) > 480 else (body or None),
                blocks=(
                    ContentBlockDraft(
                        block_order=0,
                        block_type=ContentBlockType.THEORY_SUMMARY,
                        payload_json={
                            "text": body or item.name,
                            "source": {"drive_file_id": item.id, "document_version_id": str(dv_id)},
                        },
                    ),
                ),
                rights_status=rights,
            )
            outcomes[item.id] = DriveFileOutcome(
                item.id, item.name, "DOCUMENT", None, dv_id, content_item_id=content.id
            )
    return DriveIngestResult(
        connection_id=connection_id,
        folder_id=folder_id,
        listed=len(listed),
        files=tuple(outcomes[i.id] for i in listed if i.id in outcomes),
    )
