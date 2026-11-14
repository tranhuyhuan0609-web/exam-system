"""Database service layer for A05 Document Import, Parser Registry & Extraction Runs."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.infrastructure.pdf_engine import PDFEngineAdapter, PDFEngineError, PDFPasswordRequired
from app.infrastructure.storage import Storage, get_bronze_storage
from app.modules.assets.public import get_asset_by_id
from app.modules.ingestion.public import (
    DuplicateIdempotencyConflictError,
    IngestionValidationError,
    submit_import,
)
from app.modules.outbox.public import enqueue
from app.modules.parsers.internal.models import (
    ExtractionRun as ExtractionRunRow,
)
from app.modules.parsers.internal.models import (
    ParserDefinition as ParserDefinitionRow,
)
from app.modules.parsers.internal.routing import (
    MAX_DOCUMENT_XML_BYTES,
    DocumentFormat,
    DocumentXmlTooLargeError,
    ParserRoutingError,
    ParserStrategy,
    build_libreoffice_job_spec,
    guard_docx_xml_size,
    plan_page_reprocess_run,
    route_document_parser,
)
from app.modules.parsers.internal.routing import (
    ExtractionRun as ExtractionRunSpec,
)
from app.modules.parsers.internal.routing import (
    ParserDefinition as ParserDefinitionSpec,
)
from app.platform.file_magic import FileSignatureError, inspect_uploaded_bytes
from app.platform.storage_objects import sanitize_download_filename

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC_MIME = "application/msword"
PDF_MIME = "application/pdf"
IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})

_DEFAULT_PARSER_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "code": ParserStrategy.NATIVE_DOCX_XML.value,
        "version": "docx-ooxml-v1",
        "supported_mimes": [DOCX_MIME],
        "config_schema_json": {"max_document_xml_bytes": MAX_DOCUMENT_XML_BYTES},
        "is_active": True,
    },
    {
        "code": ParserStrategy.LIBREOFFICE_HEADLESS.value,
        "version": "libreoffice-24.2",
        "supported_mimes": [DOC_MIME, DOCX_MIME],
        "config_schema_json": {"isolated_user_installation": True, "timeout_seconds": 60},
        "is_active": True,
    },
    {
        "code": ParserStrategy.PDF_ENGINE.value,
        "version": "pdfium-pdfplumber-v1",
        "supported_mimes": [PDF_MIME],
        "config_schema_json": {"min_chars_per_page": 40},
        "is_active": True,
    },
    {
        "code": ParserStrategy.OCR_PIPELINE.value,
        "version": "ocr-layout-v1",
        "supported_mimes": [PDF_MIME, "image/png", "image/jpeg", "image/webp"],
        "config_schema_json": {"dpi": 300},
        "is_active": True,
    },
)


class ParserValidationError(ValueError):
    """Raised when uploaded document, XML size, or extraction arguments fail validation."""


class DocumentNotFoundError(LookupError):
    """Raised when a document or document version is missing or outside caller tenant."""


class ExtractionRunNotFoundError(LookupError):
    """Raised when an extraction run does not exist or is invisible to caller tenant."""


@dataclass(frozen=True, slots=True)
class DocumentVersionSummary:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_document_id: uuid.UUID
    source_id: uuid.UUID
    version_no: int
    raw_asset_id: uuid.UUID
    checksum_sha256: str
    page_count: int | None
    mime_type: str
    title: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentUploadView:
    source_document_id: uuid.UUID
    document_version_id: uuid.UUID
    source_id: uuid.UUID
    version_no: int
    raw_asset_id: uuid.UUID
    acquisition_job_id: uuid.UUID
    checksum_sha256: str
    mime_type: str
    page_count: int | None
    deduplicated: bool
    routing_strategy: str
    routing_reasons: tuple[str, ...]
    detected_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtractionRunView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    acquisition_job_id: uuid.UUID | None
    profile_code: str
    parser_version: str
    ocr_engine_version: str | None
    confidence_score: Decimal | None
    silver_artifact_key: str | None
    parent_run_id: uuid.UUID | None
    target_pages: tuple[int, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExtractionRunContextView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    raw_asset_id: uuid.UUID
    mime_type: str
    profile_code: str
    parser_version: str
    ocr_engine_version: str | None


def _parse_reprocess_metadata(silver_artifact_key: str | None) -> tuple[uuid.UUID | None, tuple[int, ...]]:
    """Decode `reprocess:<uuid>:pages=1,2` metadata stored without changing DDL columns."""
    if not silver_artifact_key or not silver_artifact_key.startswith("reprocess:"):
        return None, ()
    parts = silver_artifact_key.split(":", 2)
    parent_id: uuid.UUID | None = None
    pages: tuple[int, ...] = ()
    if len(parts) >= 2:
        try:
            parent_id = uuid.UUID(parts[1])
        except ValueError:
            parent_id = None
    if len(parts) == 3 and parts[2].startswith("pages="):
        raw_pages = parts[2].removeprefix("pages=")
        parsed: list[int] = []
        for item in raw_pages.split(","):
            if item.isdigit():
                parsed.append(int(item))
        pages = tuple(parsed)
    return parent_id, pages


def _to_run_view(row: ExtractionRunRow) -> ExtractionRunView:
    parent_id, pages = _parse_reprocess_metadata(row.silver_artifact_key)
    return ExtractionRunView(
        id=row.id,
        tenant_id=row.tenant_id,
        document_version_id=row.document_version_id,
        acquisition_job_id=row.acquisition_job_id,
        profile_code=row.profile_code,
        parser_version=row.parser_version,
        ocr_engine_version=row.ocr_engine_version,
        confidence_score=row.confidence_score,
        silver_artifact_key=row.silver_artifact_key,
        parent_run_id=parent_id,
        target_pages=pages,
        created_at=row.created_at,
    )


def _read_bounded_docx_document_xml(raw_bytes: bytes) -> bytes:
    """Safely extract `word/document.xml` enforcing the 25 MB A05 XML bomb cap."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
            if "word/document.xml" not in zf.namelist():
                raise ParserValidationError("DOCX archive is missing required word/document.xml")
            info = zf.getinfo("word/document.xml")
            if info.file_size > MAX_DOCUMENT_XML_BYTES:
                guard_docx_xml_size(info.file_size)
            with zf.open(info, "r") as handle:
                chunk = handle.read(MAX_DOCUMENT_XML_BYTES + 1)
                guard_docx_xml_size(len(chunk))
                return chunk
    except zipfile.BadZipFile as exc:
        raise ParserValidationError(f"invalid DOCX zip container: {exc}") from exc


def _inspect_and_route(
    *,
    filename: str,
    raw_bytes: bytes,
    declared_mime: str | None = None,
) -> tuple[str, int | None, str, tuple[str, ...], tuple[str, ...]]:
    """Inspect magic signature, enforce DOCX 25 MB XML cap, and compute A05 parser routing."""
    try:
        verified = inspect_uploaded_bytes(filename=filename, raw_bytes=raw_bytes)
    except FileSignatureError as exc:
        msg = str(exc)
        if "word/document.xml" in msg or "25 MB" in msg or "uncompressed" in msg.lower():
            raise DocumentXmlTooLargeError(msg) from exc
        raise ParserValidationError(msg) from exc

    mime_type = verified.mime_type
    if declared_mime is not None and declared_mime.strip().lower() != mime_type:
        raise ParserValidationError(
            f"declared mime_type {declared_mime!r} does not match detected {mime_type!r}"
        )

    page_count: int | None = None
    if mime_type == DOCX_MIME:
        xml_bytes = _read_bounded_docx_document_xml(raw_bytes)
        decision = route_document_parser(doc_format=DocumentFormat.DOCX, document_xml=xml_bytes)
        return (
            mime_type,
            None,
            decision.strategy.value,
            decision.reasons,
            decision.detected_markers,
        )

    if mime_type == DOC_MIME:
        decision = route_document_parser(doc_format=DocumentFormat.DOC, document_xml=None)
        # Validate command construction safety at routing time without spawning a subprocess.
        build_libreoffice_job_spec(
            job_id=f"doc-{uuid.uuid4().hex[:12]}",
            input_path="/tmp/input.doc",
            output_dir="/tmp/out",
        )
        return (
            mime_type,
            None,
            decision.strategy.value,
            decision.reasons,
            decision.detected_markers,
        )

    if mime_type == PDF_MIME:
        try:
            with PDFEngineAdapter.open(raw_bytes) as pdf:
                page_count = pdf.page_count
                total_chars = sum(len(pdf.text(idx).strip()) for idx in range(page_count))
                avg_chars = float(total_chars) / max(1, page_count)
        except PDFPasswordRequired as exc:
            raise ParserValidationError("encrypted/password-protected PDF is not supported") from exc
        except PDFEngineError as exc:
            raise ParserValidationError(f"corrupt or unreadable PDF: {exc}") from exc

        decision = route_document_parser(
            doc_format=DocumentFormat.PDF,
            document_xml=None,
            avg_text_chars_per_page=avg_chars,
        )
        return (
            mime_type,
            page_count,
            decision.strategy.value,
            decision.reasons,
            decision.detected_markers,
        )

    if mime_type in IMAGE_MIMES:
        return (
            mime_type,
            1,
            ParserStrategy.OCR_PIPELINE.value,
            ("raster image routed directly to OCR pipeline",),
            (),
        )

    raise ParserRoutingError(f"unsupported document mime type: {mime_type!r}")


async def ensure_default_parser_definitions(session: AsyncSession) -> list[ParserDefinitionRow]:
    """Idempotently seed standard A05 parser definitions into `parser_definitions`."""
    existing = (
        await session.execute(
            select(ParserDefinitionRow).order_by(ParserDefinitionRow.code.asc())
        )
    ).scalars().all()
    existing_codes = {row.code for row in existing}
    missing = [item for item in _DEFAULT_PARSER_DEFINITIONS if item["code"] not in existing_codes]
    if missing:
        stmt = pg_insert(ParserDefinitionRow).values(missing).on_conflict_do_nothing(
            index_elements=["code"]
        )
        await session.execute(stmt)
        rows = (
            await session.execute(
                select(ParserDefinitionRow).order_by(ParserDefinitionRow.code.asc())
            )
        ).scalars().all()
        return list(rows)
    return list(existing)


async def _persist_office_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_bytes: bytes,
    filename: str,
    detected_mime: str,
    strategy: str,
    reasons: tuple[str, ...],
    markers: tuple[str, ...],
    source_id: uuid.UUID | None,
    external_id: str | None,
    title: str | None,
    idempotency_key: str | None,
    requires_pro: bool,
    correlation_id: str,
    storage: Storage | None = None,
) -> DocumentUploadView:
    """Persist validated DOCX/DOC bronze asset, document version, and acquisition job under RLS."""
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    safe_filename = sanitize_download_filename(filename)

    if idempotency_key:
        idem_job = (
            await session.execute(
                text(
                    "SELECT id, source_id, document_version_id, metrics_json "
                    "FROM acquisition_jobs WHERE idempotency_key = :idem_key"
                ),
                {"idem_key": idempotency_key},
            )
        ).mappings().first()
        if idem_job is not None:
            stored_sha = (idem_job["metrics_json"] or {}).get("checksum_sha256")
            if stored_sha != sha256:
                raise DuplicateIdempotencyConflictError(
                    "idempotency_key already used with a different document payload"
                )
            dv = (
                await session.execute(
                    text(
                        "SELECT dv.id, dv.source_document_id, dv.version_no, dv.raw_asset_id, "
                        "dv.checksum_sha256, dv.page_count, sd.source_id "
                        "FROM document_versions dv "
                        "JOIN source_documents sd "
                        "ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id "
                        "WHERE dv.id = :dv_id"
                    ),
                    {"dv_id": idem_job["document_version_id"]},
                )
            ).mappings().one()
            return DocumentUploadView(
                source_document_id=dv["source_document_id"],
                document_version_id=dv["id"],
                source_id=dv["source_id"],
                version_no=dv["version_no"],
                raw_asset_id=dv["raw_asset_id"],
                acquisition_job_id=idem_job["id"],
                checksum_sha256=dv["checksum_sha256"],
                mime_type=detected_mime,
                page_count=dv["page_count"],
                deduplicated=True,
                routing_strategy=strategy,
                routing_reasons=reasons,
                detected_markers=markers,
            )

    resolved_source_id = source_id
    rights_status = "OWNED"
    if resolved_source_id is None:
        src_row = (
            await session.execute(
                text(
                    "SELECT id, rights_status FROM sources "
                    "WHERE source_type = 'MANUAL_UPLOAD' ORDER BY created_at ASC LIMIT 1"
                )
            )
        ).mappings().first()
        if src_row is not None:
            resolved_source_id = src_row["id"]
            rights_status = src_row["rights_status"]
        else:
            resolved_source_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO sources (id, tenant_id, source_type, name, rights_status, policy_status) "
                    "VALUES (:id, :tenant_id, 'MANUAL_UPLOAD', 'Default Manual Upload', 'OWNED', 'ACTIVE')"
                ),
                {"id": resolved_source_id, "tenant_id": tenant_id},
            )
    else:
        src_row = (
            await session.execute(
                text("SELECT id, rights_status FROM sources WHERE id = :sid"),
                {"sid": resolved_source_id},
            )
        ).mappings().first()
        if src_row is None:
            raise ParserValidationError(f"source {resolved_source_id} not found")
        rights_status = src_row["rights_status"]

    # Deduplicate identical content SHA-256 within tenant if no distinct external_id version exists
    dup_dv = (
        await session.execute(
            text(
                "SELECT dv.id, dv.source_document_id, dv.version_no, dv.raw_asset_id, "
                "dv.checksum_sha256, dv.page_count, sd.source_id "
                "FROM document_versions dv "
                "JOIN source_documents sd "
                "ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id "
                "WHERE dv.checksum_sha256 = :sha ORDER BY dv.version_no DESC LIMIT 1"
            ),
            {"sha": sha256},
        )
    ).mappings().first()
    if dup_dv is not None:
        latest_job = (
            await session.execute(
                text(
                    "SELECT id FROM acquisition_jobs "
                    "WHERE document_version_id = :dv_id ORDER BY created_at DESC LIMIT 1"
                ),
                {"dv_id": dup_dv["id"]},
            )
        ).scalar_one_or_none()
        return DocumentUploadView(
            source_document_id=dup_dv["source_document_id"],
            document_version_id=dup_dv["id"],
            source_id=dup_dv["source_id"],
            version_no=dup_dv["version_no"],
            raw_asset_id=dup_dv["raw_asset_id"],
            acquisition_job_id=latest_job or uuid.uuid4(),
            checksum_sha256=dup_dv["checksum_sha256"],
            mime_type=detected_mime,
            page_count=dup_dv["page_count"],
            deduplicated=True,
            routing_strategy=strategy,
            routing_reasons=reasons,
            detected_markers=markers,
        )

    asset_id = uuid.uuid4()
    ext = ".docx" if detected_mime == DOCX_MIME else ".doc"
    object_key = f"tenants/{tenant_id}/raw/{asset_id}{ext}"
    active_storage = storage or get_bronze_storage()
    await active_storage.put(object_key, raw_bytes, detected_mime)

    asset_meta = json.dumps(
        {
            "filename": safe_filename,
            "finalized": True,
            "requires_pro": requires_pro,
            "visibility": "TENANT_PRIVATE",
        }
    )
    await session.execute(
        text(
            "INSERT INTO assets (id, tenant_id, asset_role, object_key, mime_type, sha256, size_bytes, metadata_json) "
            "VALUES (:id, :tenant_id, 'RAW_SOURCE', :object_key, :mime_type, :sha256, "
            ":size_bytes, CAST(:metadata_json AS jsonb))"
        ),
        {
            "id": asset_id,
            "tenant_id": tenant_id,
            "object_key": object_key,
            "mime_type": detected_mime,
            "sha256": sha256,
            "size_bytes": len(raw_bytes),
            "metadata_json": asset_meta,
        },
    )

    eff_ext_id = (external_id or f"sha256:{sha256}")[:255]
    src_doc = (
        await session.execute(
            text(
                "SELECT id FROM source_documents "
                "WHERE source_id = :source_id AND external_id = :ext_id"
            ),
            {"source_id": resolved_source_id, "ext_id": eff_ext_id},
        )
    ).mappings().first()

    if src_doc is None:
        source_document_id = uuid.uuid4()
        inferred_meta = json.dumps(
            {
                "filename": safe_filename,
                "size_bytes": len(raw_bytes),
                "routing_strategy": strategy,
            }
        )
        await session.execute(
            text(
                "INSERT INTO source_documents "
                "(id, tenant_id, source_id, external_id, title, mime_type, "
                "acquisition_status, rights_status, inferred_metadata_json) "
                "VALUES (:id, :tenant_id, :source_id, :external_id, :title, :mime_type, "
                "'INGESTED', :rights_status, CAST(:inferred_meta AS jsonb))"
            ),
            {
                "id": source_document_id,
                "tenant_id": tenant_id,
                "source_id": resolved_source_id,
                "external_id": eff_ext_id,
                "title": title or safe_filename,
                "mime_type": detected_mime,
                "rights_status": rights_status,
                "inferred_meta": inferred_meta,
            },
        )
        next_version_no = 1
    else:
        source_document_id = src_doc["id"]
        max_ver = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(MAX(version_no), 0) "
                        "FROM document_versions WHERE source_document_id = :doc_id"
                    ),
                    {"doc_id": source_document_id},
                )
            ).scalar_one()
        )
        next_version_no = max_ver + 1

    document_version_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO document_versions "
            "(id, tenant_id, source_document_id, version_no, raw_asset_id, checksum_sha256, page_count) "
            "VALUES (:id, :tenant_id, :doc_id, :version_no, :asset_id, :sha256, NULL)"
        ),
        {
            "id": document_version_id,
            "tenant_id": tenant_id,
            "doc_id": source_document_id,
            "version_no": next_version_no,
            "asset_id": asset_id,
            "sha256": sha256,
        },
    )

    job_id = uuid.uuid4()
    eff_idem_key = idempotency_key or f"doc:{tenant_id}:{job_id.hex}"
    metrics_json = json.dumps(
        {
            "checksum_sha256": sha256,
            "size_bytes": len(raw_bytes),
            "routing_strategy": strategy,
        }
    )
    now_utc = datetime.now(UTC)
    await session.execute(
        text(
            "INSERT INTO acquisition_jobs "
            "(id, tenant_id, source_id, document_version_id, job_type, job_status, current_stage, "
            "idempotency_key, attempt_count, metrics_json, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :source_id, :dv_id, 'EXAM_IMPORT', 'RUNNING', 'INGEST', "
            ":idem_key, 1, CAST(:metrics AS jsonb), :now_utc, :now_utc)"
        ),
        {
            "id": job_id,
            "tenant_id": tenant_id,
            "source_id": resolved_source_id,
            "dv_id": document_version_id,
            "idem_key": eff_idem_key,
            "metrics": metrics_json,
            "now_utc": now_utc,
        },
    )

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="parser.document_registered",
            tenant_id=tenant_id,
            aggregate_type="document_version",
            aggregate_id=document_version_id,
            correlation_id=correlation_id,
            payload={
                "source_document_id": str(source_document_id),
                "document_version_id": str(document_version_id),
                "raw_asset_id": str(asset_id),
                "routing_strategy": strategy,
            },
        ),
    )

    return DocumentUploadView(
        source_document_id=source_document_id,
        document_version_id=document_version_id,
        source_id=resolved_source_id,
        version_no=next_version_no,
        raw_asset_id=asset_id,
        acquisition_job_id=job_id,
        checksum_sha256=sha256,
        mime_type=detected_mime,
        page_count=None,
        deduplicated=False,
        routing_strategy=strategy,
        routing_reasons=reasons,
        detected_markers=markers,
    )


async def register_and_ingest_document(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_bytes: bytes,
    filename: str,
    mime_type: str | None = None,
    source_id: uuid.UUID | None = None,
    external_id: str | None = None,
    title: str | None = None,
    idempotency_key: str | None = None,
    requires_pro: bool = False,
    correlation_id: str = "a05-document-register",
) -> DocumentUploadView:
    """Validate signature/XML size, compute A05 routing strategy, and persist document version."""
    if not raw_bytes:
        raise ParserValidationError("uploaded document must not be empty")

    detected_mime, page_count, strategy, reasons, markers = _inspect_and_route(
        filename=filename,
        raw_bytes=raw_bytes,
        declared_mime=mime_type,
    )

    if detected_mime in (DOCX_MIME, DOC_MIME):
        return await _persist_office_document(
            session,
            tenant_id=tenant_id,
            raw_bytes=raw_bytes,
            filename=filename,
            detected_mime=detected_mime,
            strategy=strategy,
            reasons=reasons,
            markers=markers,
            source_id=source_id,
            external_id=external_id,
            title=title,
            idempotency_key=idempotency_key,
            requires_pro=requires_pro,
            correlation_id=correlation_id,
        )

    try:
        ingest_result = await submit_import(
            session,
            tenant_id=tenant_id,
            raw_stream=raw_bytes,
            filename=filename,
            mime_type=detected_mime,
            job_type="EXAM_IMPORT",
            source_id=source_id,
            external_id=external_id,
            title=title,
            idempotency_key=idempotency_key,
            requires_pro=requires_pro,
            correlation_id=correlation_id,
        )
    except DuplicateIdempotencyConflictError:
        raise
    except IngestionValidationError as exc:
        raise ParserValidationError(str(exc)) from exc

    doc_ver = ingest_result.document_version
    return DocumentUploadView(
        source_document_id=doc_ver.source_document_id,
        document_version_id=doc_ver.id,
        source_id=doc_ver.source_id,
        version_no=doc_ver.version_no,
        raw_asset_id=doc_ver.raw_asset_id,
        acquisition_job_id=ingest_result.job.id,
        checksum_sha256=doc_ver.checksum_sha256,
        mime_type=detected_mime,
        page_count=doc_ver.page_count if doc_ver.page_count is not None else page_count,
        deduplicated=ingest_result.is_duplicate,
        routing_strategy=strategy,
        routing_reasons=reasons,
        detected_markers=markers,
    )


async def list_document_versions(
    session: AsyncSession,
    *,
    source_document_id: uuid.UUID,
) -> list[DocumentVersionSummary]:
    """List immutable SHA-256 versions of one source document visible under current RLS tenant."""
    src_row = (
        await session.execute(
            text("SELECT id, source_id, mime_type, title FROM source_documents WHERE id = :doc_id"),
            {"doc_id": source_document_id},
        )
    ).mappings().first()
    if src_row is None:
        raise DocumentNotFoundError(f"document {source_document_id} not found")

    version_rows = (
        await session.execute(
            text(
                """
                SELECT id, tenant_id, source_document_id, version_no, raw_asset_id,
                       checksum_sha256, page_count, created_at
                FROM document_versions
                WHERE source_document_id = :doc_id
                ORDER BY version_no ASC
                """
            ),
            {"doc_id": source_document_id},
        )
    ).mappings().all()

    return [
        DocumentVersionSummary(
            id=row["id"],
            tenant_id=row["tenant_id"],
            source_document_id=row["source_document_id"],
            source_id=src_row["source_id"],
            version_no=row["version_no"],
            raw_asset_id=row["raw_asset_id"],
            checksum_sha256=row["checksum_sha256"],
            page_count=row["page_count"],
            mime_type=src_row["mime_type"],
            title=src_row["title"],
            created_at=row["created_at"],
        )
        for row in version_rows
    ]


async def start_document_extraction(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    document_version_id: uuid.UUID | None = None,
    profile_code: str | None = None,
    ocr_engine_version: str | None = None,
    acquisition_job_id: uuid.UUID | None = None,
    correlation_id: str = "a05-document-extract",
    storage: Storage | None = None,
) -> ExtractionRunView:
    """Resolve document version + active parser profile, persist ExtractionRun, and emit outbox."""
    if document_version_id is not None:
        dv_row = (
            await session.execute(
                text(
                    """
                    SELECT dv.id, dv.tenant_id, dv.source_document_id, dv.raw_asset_id, sd.mime_type
                    FROM document_versions dv
                    JOIN source_documents sd
                      ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                    WHERE dv.id = :ver_id AND dv.source_document_id = :doc_id
                    """
                ),
                {"ver_id": document_version_id, "doc_id": document_id},
            )
        ).mappings().first()
    else:
        dv_row = (
            await session.execute(
                text(
                    """
                    SELECT dv.id, dv.tenant_id, dv.source_document_id, dv.raw_asset_id, sd.mime_type
                    FROM document_versions dv
                    JOIN source_documents sd
                      ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                    WHERE dv.source_document_id = :doc_id
                    ORDER BY dv.version_no DESC
                    LIMIT 1
                    """
                ),
                {"doc_id": document_id},
            )
        ).mappings().first()
        if dv_row is None:
            dv_row = (
                await session.execute(
                    text(
                        """
                        SELECT dv.id, dv.tenant_id, dv.source_document_id, dv.raw_asset_id, sd.mime_type
                        FROM document_versions dv
                        JOIN source_documents sd
                          ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                        WHERE dv.id = :doc_id
                        """
                    ),
                    {"doc_id": document_id},
                )
            ).mappings().first()

    if dv_row is None:
        raise DocumentNotFoundError(f"document {document_id} not found")

    defs = await ensure_default_parser_definitions(session)
    defs_by_code = {d.code: d for d in defs}

    selected_code = profile_code
    if selected_code is None:
        active_storage = storage or get_bronze_storage()
        asset = await get_asset_by_id(session, asset_id=dv_row["raw_asset_id"])
        raw_bytes = await active_storage.get(asset.object_key)
        _, _, detected_strategy, _, _ = _inspect_and_route(
            filename=str(asset.metadata_json.get("filename") or f"doc_{dv_row['id']}"),
            raw_bytes=raw_bytes,
            declared_mime=dv_row["mime_type"],
        )
        selected_code = detected_strategy

    parser_row = defs_by_code.get(selected_code)
    if parser_row is None or not parser_row.is_active:
        raise ParserValidationError(f"parser definition {selected_code!r} is missing or inactive")

    run_id = uuid.uuid4()
    row = ExtractionRunRow(
        id=run_id,
        tenant_id=tenant_id,
        document_version_id=dv_row["id"],
        acquisition_job_id=acquisition_job_id,
        profile_code=parser_row.code,
        parser_version=parser_row.version,
        ocr_engine_version=ocr_engine_version,
    )
    session.add(row)
    await session.flush()

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="parser.extraction_requested",
            tenant_id=tenant_id,
            aggregate_type="extraction_run",
            aggregate_id=row.id,
            correlation_id=correlation_id,
            payload={
                "extraction_run_id": str(row.id),
                "document_version_id": str(row.document_version_id),
                "profile_code": row.profile_code,
                "parser_version": row.parser_version,
            },
        ),
    )
    await session.refresh(row)
    return _to_run_view(row)


async def reprocess_extraction_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    pages: list[int],
    profile_code: str | None = None,
    ocr_engine_version: str | None = None,
    correlation_id: str = "a05-extraction-reprocess",
) -> ExtractionRunView:
    """Create a page-scoped versioned reprocess ExtractionRun linked to its parent run."""
    prior = (
        await session.execute(select(ExtractionRunRow).where(ExtractionRunRow.id == run_id))
    ).scalar_one_or_none()
    if prior is None:
        raise ExtractionRunNotFoundError(f"extraction run {run_id} not found")

    defs = await ensure_default_parser_definitions(session)
    defs_by_code = {d.code: d for d in defs}

    target_code = profile_code or prior.profile_code
    parser_row = defs_by_code.get(target_code)
    if parser_row is None or not parser_row.is_active:
        raise ParserValidationError(f"parser definition {target_code!r} is missing or inactive")

    try:
        strategy_enum = ParserStrategy(parser_row.code)
        prior_strategy = ParserStrategy(prior.profile_code)
    except ValueError as exc:
        raise ParserValidationError(f"unsupported parser strategy: {exc}") from exc

    new_run_uuid = uuid.uuid4()
    plan = plan_page_reprocess_run(
        new_run_id=new_run_uuid.hex,
        prior_run=ExtractionRunSpec(
            run_id=prior.id.hex,
            document_id=str(prior.document_version_id),
            parser_code=prior.profile_code,
            parser_version=prior.parser_version,
            strategy=prior_strategy,
            target_pages=(),
        ),
        pages=tuple(pages),
        parser_def=ParserDefinitionSpec(
            parser_code=parser_row.code,
            parser_version=parser_row.version,
            strategy=strategy_enum,
            supports_formats=(DocumentFormat.PDF, DocumentFormat.DOCX, DocumentFormat.DOC),
        ),
    )

    pages_csv = ",".join(str(p) for p in plan.target_pages)
    new_row = ExtractionRunRow(
        id=new_run_uuid,
        tenant_id=tenant_id,
        document_version_id=prior.document_version_id,
        acquisition_job_id=prior.acquisition_job_id,
        profile_code=parser_row.code,
        parser_version=parser_row.version,
        ocr_engine_version=ocr_engine_version or prior.ocr_engine_version,
        silver_artifact_key=f"reprocess:{prior.id}:pages={pages_csv}",
    )
    session.add(new_row)
    await session.flush()

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="parser.extraction_reprocess_requested",
            tenant_id=tenant_id,
            aggregate_type="extraction_run",
            aggregate_id=new_row.id,
            correlation_id=correlation_id,
            payload={
                "extraction_run_id": str(new_row.id),
                "parent_run_id": str(prior.id),
                "document_version_id": str(prior.document_version_id),
                "target_pages": list(plan.target_pages),
                "profile_code": new_row.profile_code,
            },
        ),
    )
    await session.refresh(new_row)
    return _to_run_view(new_row)


async def get_extraction_run(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
) -> ExtractionRunContextView:
    """Read one ExtractionRun with its DocumentVersion raw_asset_id under current tenant RLS."""
    row = (
        await session.execute(
            text(
                """
                SELECT er.id, er.tenant_id, er.document_version_id, er.profile_code,
                       er.parser_version, er.ocr_engine_version,
                       dv.raw_asset_id, sd.mime_type
                FROM extraction_runs er
                JOIN document_versions dv
                  ON dv.id = er.document_version_id AND dv.tenant_id = er.tenant_id
                JOIN source_documents sd
                  ON sd.id = dv.source_document_id AND sd.tenant_id = dv.tenant_id
                WHERE er.id = :run_id
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().first()

    if row is None:
        raise ExtractionRunNotFoundError(f"extraction run {run_id} not found")

    return ExtractionRunContextView(
        id=row["id"],
        tenant_id=row["tenant_id"],
        document_version_id=row["document_version_id"],
        raw_asset_id=row["raw_asset_id"],
        mime_type=row["mime_type"],
        profile_code=row["profile_code"],
        parser_version=row["parser_version"],
        ocr_engine_version=row["ocr_engine_version"],
    )
