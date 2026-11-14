"""Database service layer for A01 Unified Ingestion Backbone."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.assets.public import (
    AssetVisibility,
    UploadValidationError,
    register_bronze_asset_from_bytes,
)
from app.modules.ingestion.internal.fingerprint import (
    DEFAULT_CHUNK_SIZE,
    StreamFingerprint,
    compute_stream_fingerprint,
    decide_duplicate_ingest,
)
from app.modules.ingestion.internal.models import (
    AcquisitionJob,
    DeadLetterItem,
    DocumentVersion,
    PipelineStageRun,
    Source,
    SourceDocument,
)
from app.modules.ingestion.internal.state import (
    AcquisitionJobState,
    AcquisitionJobStatus,
    CancelJobEvent,
    CompleteJobEvent,
    FailJobEvent,
    IngestionEvent,
    IngestionTransitionError,
    PipelineStageStatus,
    RetryJobEvent,
    StageCompletedEvent,
    StartJobEvent,
    apply_job_event,
)
from app.modules.outbox.public import enqueue
from app.platform.file_magic import (
    MAX_UPLOAD_BYTES,
    FileSignatureError,
    inspect_uploaded_bytes,
)

ALLOWED_JOB_TYPES = frozenset({"EXAM_IMPORT", "DRIVE_SYNC", "WEB_CRAWL", "REPROCESS_EXTRACTION"})
DEFAULT_STAGE_INGEST = "INGEST"
DEFAULT_STAGE_EXTRACT = "EXTRACT"


class IngestionValidationError(ValueError):
    """Raised when an uploaded document, filename, MIME, or job input fails validation."""


class ImportJobNotFoundError(LookupError):
    """Raised when an acquisition job does not exist or is invisible to the caller's tenant."""


class DuplicateIdempotencyConflictError(ValueError):
    """Raised when an idempotency_key is reused with a different content SHA-256 digest."""


@dataclass(frozen=True, slots=True)
class PipelineStageRunView:
    id: uuid.UUID
    stage_name: str
    status: str
    worker_version: str | None
    input_ref: str | None
    output_ref: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class DocumentVersionView:
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
class ImportJobView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    job_type: str
    job_status: str
    current_stage: str | None
    idempotency_key: str
    attempt_count: int
    last_error_code: str | None
    last_error_detail: str | None
    metrics_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    stages: tuple[PipelineStageRunView, ...]
    document_version: DocumentVersionView | None
    dead_letter_count: int


@dataclass(frozen=True, slots=True)
class SubmitImportResult:
    is_duplicate: bool
    idempotent_replay: bool
    job: ImportJobView
    document_version: DocumentVersionView


def _validate_safe_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise IngestionValidationError("filename must not be empty")
    if "\x00" in filename or "/" in filename or "\\" in filename or ".." in filename:
        raise IngestionValidationError("filename contains illegal path traversal sequence")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in filename):
        raise IngestionValidationError("filename contains control characters")
    return filename.strip()


def _read_bounded_bytes(
    source: BinaryIO | bytes | bytearray | Iterable[bytes | bytearray | memoryview],
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if not data:
            raise IngestionValidationError("uploaded file is empty")
        if len(data) > max_bytes:
            raise IngestionValidationError(f"uploaded file exceeds maximum size {max_bytes} bytes")
        return data

    buf = bytearray()
    read_fn = getattr(source, "read", None)
    if callable(read_fn):
        while True:
            chunk = read_fn(DEFAULT_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise IngestionValidationError(f"uploaded file exceeds maximum size {max_bytes} bytes")
    else:
        for chunk in source:
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise IngestionValidationError(f"uploaded file exceeds maximum size {max_bytes} bytes")

    if not buf:
        raise IngestionValidationError("uploaded file is empty")
    return bytes(buf)


def _to_stage_view(row: PipelineStageRun) -> PipelineStageRunView:
    return PipelineStageRunView(
        id=row.id,
        stage_name=row.stage_name,
        status=row.status,
        worker_version=row.worker_version,
        input_ref=row.input_ref,
        output_ref=row.output_ref,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _to_doc_version_view(doc_ver: DocumentVersion, src_doc: SourceDocument) -> DocumentVersionView:
    return DocumentVersionView(
        id=doc_ver.id,
        tenant_id=doc_ver.tenant_id,
        source_document_id=doc_ver.source_document_id,
        source_id=src_doc.source_id,
        version_no=doc_ver.version_no,
        raw_asset_id=doc_ver.raw_asset_id,
        checksum_sha256=doc_ver.checksum_sha256,
        page_count=doc_ver.page_count,
        mime_type=src_doc.mime_type,
        title=src_doc.title,
        created_at=doc_ver.created_at,
    )


async def _load_job_view(session: AsyncSession, job: AcquisitionJob) -> ImportJobView:
    stages = (
        await session.execute(
            select(PipelineStageRun)
            .where(PipelineStageRun.acquisition_job_id == job.id)
            .order_by(PipelineStageRun.started_at.asc(), PipelineStageRun.stage_name.asc())
        )
    ).scalars().all()

    doc_view: DocumentVersionView | None = None
    if job.document_version_id is not None:
        doc_ver = (
            await session.execute(select(DocumentVersion).where(DocumentVersion.id == job.document_version_id))
        ).scalar_one_or_none()
        if doc_ver is not None:
            src_doc = (
                await session.execute(
                    select(SourceDocument).where(SourceDocument.id == doc_ver.source_document_id)
                )
            ).scalar_one()
            doc_view = _to_doc_version_view(doc_ver, src_doc)

    dlq_count = int(
        (
            await session.execute(
                select(func.count(DeadLetterItem.id)).where(DeadLetterItem.acquisition_job_id == job.id)
            )
        ).scalar_one()
    )

    return ImportJobView(
        id=job.id,
        tenant_id=job.tenant_id,
        source_id=job.source_id,
        document_version_id=job.document_version_id,
        job_type=job.job_type,
        job_status=job.job_status,
        current_stage=job.current_stage,
        idempotency_key=job.idempotency_key,
        attempt_count=job.attempt_count,
        last_error_code=job.last_error_code,
        last_error_detail=job.last_error_detail,
        metrics_json=dict(job.metrics_json or {}),
        created_at=job.created_at,
        updated_at=job.updated_at,
        stages=tuple(_to_stage_view(s) for s in stages),
        document_version=doc_view,
        dead_letter_count=dlq_count,
    )


def _hydrate_state(job: AcquisitionJob, stages: list[PipelineStageRun]) -> AcquisitionJobState:
    metrics = dict(job.metrics_json or {})
    stage_map: dict[str, PipelineStageStatus] = {}
    for s in stages:
        stage_map[s.stage_name] = PipelineStageStatus(s.status)
    applied_raw = metrics.get("applied_event_ids") or []
    max_attempts = int(metrics.get("max_attempts", 3))
    return AcquisitionJobState(
        job_id=str(job.id),
        status=AcquisitionJobStatus(job.job_status),
        attempt_count=job.attempt_count,
        max_attempts=max_attempts,
        current_stage=job.current_stage,
        last_error_code=job.last_error_code,
        last_error_detail=job.last_error_detail,
        stage_statuses=tuple(sorted(stage_map.items())),
        applied_event_ids=frozenset(str(x) for x in applied_raw),
    )


async def _get_or_create_source(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID | None,
) -> Source:
    if source_id is not None:
        src = (
            await session.execute(select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id))
        ).scalar_one_or_none()
        if src is None:
            raise IngestionValidationError(f"source {source_id} not found for tenant")
        return src

    existing = (
        await session.execute(
            select(Source)
            .where(Source.tenant_id == tenant_id, Source.source_type == "MANUAL_UPLOAD")
            .order_by(Source.created_at.asc())
        )
    ).scalars().first()
    if existing is not None:
        return existing

    created = Source(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_type="MANUAL_UPLOAD",
        name="Default Manual Upload",
        rights_status="OWNED",
        policy_status="ACTIVE",
    )
    session.add(created)
    await session.flush()
    return created


async def _apply_state_transition(
    session: AsyncSession,
    *,
    job: AcquisitionJob,
    event: IngestionEvent,
    correlation_id: str,
    worker_version: str = "ingestion-v1",
) -> None:
    existing_stages = (
        await session.execute(
            select(PipelineStageRun)
            .where(PipelineStageRun.acquisition_job_id == job.id)
            .order_by(PipelineStageRun.started_at.asc())
        )
    ).scalars().all()
    prev_state = _hydrate_state(job, list(existing_stages))
    next_state = apply_job_event(prev_state, event)
    if next_state == prev_state:
        return

    now_utc = datetime.now(UTC)
    by_stage: dict[str, PipelineStageRun] = {s.stage_name: s for s in existing_stages}
    for stage_name, target_status in next_state.stage_statuses:
        stage_row = by_stage.get(stage_name)
        is_terminal_stage = target_status in (
            PipelineStageStatus.COMPLETED,
            PipelineStageStatus.FAILED,
            PipelineStageStatus.SKIPPED,
        )
        if stage_row is None:
            new_row = PipelineStageRun(
                id=uuid.uuid4(),
                tenant_id=job.tenant_id,
                acquisition_job_id=job.id,
                stage_name=stage_name,
                status=target_status.value,
                worker_version=worker_version,
                finished_at=now_utc if is_terminal_stage else None,
            )
            session.add(new_row)
            by_stage[stage_name] = new_row
        elif stage_row.status != target_status.value:
            stage_row.status = target_status.value
            stage_row.worker_version = worker_version
            if target_status == PipelineStageStatus.RUNNING:
                stage_row.started_at = now_utc
                stage_row.finished_at = None
            elif is_terminal_stage:
                stage_row.finished_at = now_utc

    metrics = dict(job.metrics_json or {})
    metrics["applied_event_ids"] = sorted(next_state.applied_event_ids)
    job.job_status = next_state.status.value
    job.attempt_count = next_state.attempt_count
    job.current_stage = next_state.current_stage
    job.last_error_code = next_state.last_error_code
    job.last_error_detail = next_state.last_error_detail
    job.metrics_json = metrics
    job.updated_at = now_utc
    await session.flush()

    if next_state.dead_letter is not None:
        dlq = next_state.dead_letter
        dlq_row = DeadLetterItem(
            id=uuid.uuid4(),
            tenant_id=job.tenant_id,
            acquisition_job_id=job.id,
            source_queue="ingestion.pipeline",
            payload_json={
                "acquisition_job_id": dlq.acquisition_job_id,
                "failed_stage": dlq.failed_stage,
                "attempt_count": dlq.attempt_count,
                "error_code": dlq.error_code,
            },
            error_trace=dlq.error_trace,
        )
        session.add(dlq_row)
        await session.flush()
        await enqueue(
            session,
            EventEnvelopeContract(
                event_type="ingestion.job_dead_lettered",
                tenant_id=job.tenant_id,
                aggregate_type="acquisition_job",
                aggregate_id=job.id,
                correlation_id=correlation_id,
                payload={
                    "job_id": str(job.id),
                    "dead_letter_id": str(dlq_row.id),
                    "failed_stage": dlq.failed_stage,
                    "error_code": dlq.error_code,
                },
            ),
        )

    event_name_map = {
        StartJobEvent: "ingestion.job_started",
        StageCompletedEvent: "ingestion.stage_completed",
        FailJobEvent: "ingestion.job_failed",
        RetryJobEvent: "ingestion.job_retried",
        CompleteJobEvent: "ingestion.job_completed",
        CancelJobEvent: "ingestion.job_cancelled",
    }
    outbox_type = event_name_map.get(type(event), "ingestion.job_updated")
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=outbox_type,
            tenant_id=job.tenant_id,
            aggregate_type="acquisition_job",
            aggregate_id=job.id,
            correlation_id=correlation_id,
            payload={
                "job_id": str(job.id),
                "job_status": job.job_status,
                "current_stage": job.current_stage,
                "attempt_count": job.attempt_count,
            },
        ),
    )


async def submit_import(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_stream: BinaryIO | bytes | bytearray | Iterable[bytes | bytearray | memoryview],
    filename: str,
    mime_type: str | None = None,
    job_type: str = "EXAM_IMPORT",
    source_id: uuid.UUID | None = None,
    external_id: str | None = None,
    title: str | None = None,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    requires_pro: bool = False,
    start_immediately: bool = True,
    correlation_id: str = "ingestion-import",
) -> SubmitImportResult:
    """Validate file magic bytes, fingerprint stream, deduplicate, register bronze asset, and enqueue job."""
    if job_type not in ALLOWED_JOB_TYPES:
        raise IngestionValidationError(f"unsupported job_type: {job_type!r}")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise IngestionValidationError("max_attempts must be an integer >= 1")

    safe_filename = _validate_safe_filename(filename)
    raw_bytes = _read_bounded_bytes(raw_stream)
    fingerprint: StreamFingerprint = compute_stream_fingerprint(io.BytesIO(raw_bytes))

    try:
        verified = inspect_uploaded_bytes(filename=safe_filename, raw_bytes=raw_bytes)
    except FileSignatureError as exc:
        raise IngestionValidationError(f"file validation failed: {exc}") from exc

    if mime_type is not None and mime_type.strip().lower() != verified.mime_type:
        raise IngestionValidationError(
            f"declared mime_type {mime_type!r} does not match detected {verified.mime_type!r}"
        )

    if idempotency_key:
        idem_job = (
            await session.execute(
                select(AcquisitionJob).where(
                    AcquisitionJob.tenant_id == tenant_id,
                    AcquisitionJob.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if idem_job is not None:
            stored_sha = (idem_job.metrics_json or {}).get("checksum_sha256")
            if stored_sha != fingerprint.sha256:
                raise DuplicateIdempotencyConflictError(
                    "idempotency_key already used with a different document payload"
                )
            idem_view = await _load_job_view(session, idem_job)
            assert idem_view.document_version is not None
            return SubmitImportResult(
                is_duplicate=True,
                idempotent_replay=True,
                job=idem_view,
                document_version=idem_view.document_version,
            )

    existing_versions = (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant_id,
                DocumentVersion.checksum_sha256 == fingerprint.sha256,
            )
        )
    ).scalars().all()
    by_sha: dict[str, DocumentVersion] = {v.checksum_sha256.lower(): v for v in existing_versions}
    dup_decision = decide_duplicate_ingest(fingerprint, by_sha)
    if dup_decision.is_duplicate and dup_decision.existing_record is not None:
        existing_dv = dup_decision.existing_record
        src_doc = (
            await session.execute(select(SourceDocument).where(SourceDocument.id == existing_dv.source_document_id))
        ).scalar_one()
        latest_job = (
            await session.execute(
                select(AcquisitionJob)
                .where(
                    AcquisitionJob.tenant_id == tenant_id,
                    AcquisitionJob.document_version_id == existing_dv.id,
                )
                .order_by(AcquisitionJob.created_at.desc())
            )
        ).scalars().first()
        dv_view = _to_doc_version_view(existing_dv, src_doc)
        if latest_job is not None:
            job_view = await _load_job_view(session, latest_job)
        else:
            job_view = ImportJobView(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                source_id=src_doc.source_id,
                document_version_id=existing_dv.id,
                job_type=job_type,
                job_status=AcquisitionJobStatus.COMPLETED.value,
                current_stage=None,
                idempotency_key=f"dup:{existing_dv.id}",
                attempt_count=0,
                last_error_code=None,
                last_error_detail=None,
                metrics_json={"checksum_sha256": fingerprint.sha256},
                created_at=existing_dv.created_at,
                updated_at=existing_dv.created_at,
                stages=(),
                document_version=dv_view,
                dead_letter_count=0,
            )
        return SubmitImportResult(
            is_duplicate=True,
            idempotent_replay=False,
            job=job_view,
            document_version=dv_view,
        )

    source = await _get_or_create_source(session, tenant_id=tenant_id, source_id=source_id)

    try:
        asset_view, _ = await register_bronze_asset_from_bytes(
            session,
            tenant_id=tenant_id,
            raw_bytes=raw_bytes,
            filename=safe_filename,
            mime_type=verified.mime_type,
            requires_pro=requires_pro,
            visibility=AssetVisibility.TENANT_PRIVATE,
            correlation_id=correlation_id,
        )
    except UploadValidationError as exc:
        raise IngestionValidationError(str(exc)) from exc

    effective_external_id = (external_id or f"sha256:{fingerprint.sha256}")[:255]
    src_doc = (
        await session.execute(
            select(SourceDocument).where(
                SourceDocument.source_id == source.id,
                SourceDocument.external_id == effective_external_id,
            )
        )
    ).scalar_one_or_none()
    if src_doc is None:
        src_doc = SourceDocument(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_id=source.id,
            external_id=effective_external_id,
            title=title or safe_filename,
            mime_type=verified.mime_type,
            acquisition_status="INGESTED",
            rights_status=source.rights_status,
            inferred_metadata_json={
                "filename": safe_filename,
                "size_bytes": fingerprint.size_bytes,
                "has_ooxml_mathtype_or_drawings": verified.has_ooxml_mathtype_or_drawings,
            },
        )
        session.add(src_doc)
        await session.flush()
        next_version_no = 1
    else:
        max_ver = int(
            (
                await session.execute(
                    select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
                        DocumentVersion.source_document_id == src_doc.id
                    )
                )
            ).scalar_one()
        )
        next_version_no = max_ver + 1

    doc_version = DocumentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_document_id=src_doc.id,
        version_no=next_version_no,
        raw_asset_id=asset_view.id,
        checksum_sha256=fingerprint.sha256,
        page_count=asset_view.page_count,
    )
    session.add(doc_version)
    await session.flush()

    job_id = uuid.uuid4()
    eff_idem_key = idempotency_key or f"import:{tenant_id}:{job_id.hex}"
    job = AcquisitionJob(
        id=job_id,
        tenant_id=tenant_id,
        source_id=source.id,
        document_version_id=doc_version.id,
        job_type=job_type,
        job_status=AcquisitionJobStatus.QUEUED.value,
        current_stage=None,
        idempotency_key=eff_idem_key,
        attempt_count=0,
        metrics_json={
            "checksum_sha256": fingerprint.sha256,
            "size_bytes": fingerprint.size_bytes,
            "max_attempts": max_attempts,
            "applied_event_ids": [],
        },
    )
    session.add(job)
    await session.flush()

    if start_immediately:
        await _apply_state_transition(
            session,
            job=job,
            event=StartJobEvent(event_id=f"start:{job.id}:1", stage_name=DEFAULT_STAGE_INGEST),
            correlation_id=correlation_id,
        )
        await _apply_state_transition(
            session,
            job=job,
            event=StageCompletedEvent(
                event_id=f"complete-ingest:{job.id}",
                stage_name=DEFAULT_STAGE_INGEST,
                next_stage=DEFAULT_STAGE_EXTRACT,
            ),
            correlation_id=correlation_id,
        )

    await session.refresh(job)
    await session.refresh(doc_version)
    job_view = await _load_job_view(session, job)
    assert job_view.document_version is not None
    return SubmitImportResult(
        is_duplicate=False,
        idempotent_replay=False,
        job=job_view,
        document_version=job_view.document_version,
    )


async def get_import_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
) -> ImportJobView:
    """Fetch an acquisition job and its stage runs scoped strictly by tenant RLS."""
    job = (await session.execute(select(AcquisitionJob).where(AcquisitionJob.id == job_id))).scalar_one_or_none()
    if job is None or job.tenant_id != tenant_id:
        raise ImportJobNotFoundError(f"import job {job_id} not found")
    return await _load_job_view(session, job)


async def apply_import_job_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    event: IngestionEvent,
    correlation_id: str = "ingestion-event",
) -> ImportJobView:
    """Apply any IngestionEvent cleanly to a persisted AcquisitionJob + its PipelineStageRun rows."""
    job = (await session.execute(select(AcquisitionJob).where(AcquisitionJob.id == job_id))).scalar_one_or_none()
    if job is None or job.tenant_id != tenant_id:
        raise ImportJobNotFoundError(f"import job {job_id} not found")
    await _apply_state_transition(
        session,
        job=job,
        event=event,
        correlation_id=correlation_id,
    )
    await session.refresh(job)
    return await _load_job_view(session, job)


async def retry_import_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    event_id: str | None = None,
    correlation_id: str = "ingestion-retry",
) -> ImportJobView:
    """Retry a FAILED job, keeping COMPLETED stages intact and reopening only FAILED stages."""
    ev_id = event_id or f"retry:{job_id}:{uuid.uuid4().hex[:12]}"
    return await apply_import_job_event(
        session,
        tenant_id=tenant_id,
        job_id=job_id,
        event=RetryJobEvent(event_id=ev_id),
        correlation_id=correlation_id,
    )


async def cancel_import_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    reason: str = "Cancelled by user",
    event_id: str | None = None,
    correlation_id: str = "ingestion-cancel",
) -> ImportJobView:
    """Cancel a non-terminal acquisition job; raises CANCEL_FROM_TERMINAL_STATE on terminal jobs."""
    ev_id = event_id or f"cancel:{job_id}:{uuid.uuid4().hex[:12]}"
    return await apply_import_job_event(
        session,
        tenant_id=tenant_id,
        job_id=job_id,
        event=CancelJobEvent(event_id=ev_id, reason=reason),
        correlation_id=correlation_id,
    )


__all__ = [
    "ALLOWED_JOB_TYPES",
    "DocumentVersionView",
    "DuplicateIdempotencyConflictError",
    "ImportJobNotFoundError",
    "ImportJobView",
    "IngestionTransitionError",
    "IngestionValidationError",
    "PipelineStageRunView",
    "SubmitImportResult",
    "apply_import_job_event",
    "cancel_import_job",
    "get_import_job",
    "retry_import_job",
    "submit_import",
]
