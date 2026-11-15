"""Database service layer for A03 Google Drive Sync (connections, differential sync, pairing, revoke)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.event_catalog import DriveFileDiscoveredPayload, build_catalog_envelope
from app.modules.dedup.public import (
    DEFAULT_AUTO_CONFIRM_THRESHOLD,
    DEFAULT_PROPOSE_THRESHOLD,
    PairingDocumentInput,
    auto_pair_exam_and_hdc_documents,
)
from app.modules.drive_sync.internal.models import (
    FilePairingCandidate,
    SourceConnection,
    SourceSyncCursor,
)
from app.modules.drive_sync.internal.safe_archive import (
    DEFAULT_POLICY,
    ArchiveSecurityViolationError,
    SafeArchivePolicy,
    inspect_zip,
)
from app.modules.outbox.public import enqueue

ALLOWED_DRIVE_SCOPES: frozenset[str] = frozenset(
    {
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/drive.install",
        "service_account.folder",
    }
)
ALLOWED_RIGHTS_STATUSES: frozenset[str] = frozenset(
    {"PENDING_REVIEW", "OWNED", "LICENSED", "ALLOWED_DERIVATIVE", "RESTRICTED", "TAKEDOWN"}
)
_ZIP_MIME_TYPES: frozenset[str] = frozenset(
    {"application/zip", "application/x-zip-compressed", "multipart/x-zip"}
)


class DriveSyncValidationError(ValueError):
    """Raised when Drive connection or sync inputs fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DriveSyncNotFoundError(LookupError):
    """Raised when a Drive connection, sync job, or pairing candidate is missing in tenant scope."""


class DriveSyncPolicyError(PermissionError):
    """Raised when OAuth scopes, connection state, or archive safety checks reject an operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DriveFileChange:
    """Single file delta reported by Google Drive Changes API / Picker."""

    drive_file_id: str
    title: str
    mime_type: str
    sha256: str
    size_bytes: int
    object_key: str
    folder_id: str | None = None
    folder_path: str = "/"
    removed: bool = False
    local_archive_path: str | Path | None = None
    subject_code: str | None = None
    question_count: int | None = None
    rights_status: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DriveConnectionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_id: uuid.UUID
    provider: str
    secret_vault_ref: str
    granted_scopes: tuple[str, ...]
    status: str
    cursor_token: str | None
    last_synced_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FilePairingView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    exam_doc_id: uuid.UUID
    solution_doc_id: uuid.UUID
    similarity_score: Decimal
    signals_json: dict[str, Any]
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DriveSyncJobView:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    source_id: uuid.UUID
    job_status: str
    cursor_token: str
    ingested_count: int
    renamed_or_moved_count: int
    skipped_count: int
    pairings_created: tuple[FilePairingView, ...]
    created_at: datetime


def validate_drive_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    """Enforce least-privilege OAuth scopes (`drive.file` + Picker or Service Account only)."""
    cleaned = tuple(s.strip() for s in scopes if s and s.strip())
    if not cleaned:
        raise DriveSyncValidationError("DRIVE_SCOPES_EMPTY", "At least one granted scope is required")
    disallowed = [scope for scope in cleaned if scope not in ALLOWED_DRIVE_SCOPES]
    if disallowed:
        raise DriveSyncPolicyError(
            "DRIVE_SCOPE_TOO_BROAD",
            f"Broad or unsupported Drive scopes are forbidden: {', '.join(disallowed)}",
        )
    return cleaned


def _validate_vault_ref(secret_vault_ref: str) -> str:
    cleaned = secret_vault_ref.strip()
    if len(cleaned) < 3 or cleaned.startswith("ya29.") or any(ch.isspace() for ch in cleaned):
        raise DriveSyncValidationError(
            "INVALID_SECRET_VAULT_REF",
            "secret_vault_ref must reference a vault secret rather than raw credential material",
        )
    return cleaned


def _validate_sha256(sha256: str) -> str:
    cleaned = sha256.strip().lower()
    if len(cleaned) != 64 or any(ch not in "0123456789abcdef" for ch in cleaned):
        raise DriveSyncValidationError("INVALID_SHA256", "sha256 must be a 64-character hex digest")
    return cleaned


def _to_connection_view(
    conn: SourceConnection,
    cursor: SourceSyncCursor | None,
) -> DriveConnectionView:
    return DriveConnectionView(
        id=conn.id,
        tenant_id=conn.tenant_id,
        source_id=conn.source_id,
        provider=conn.provider,
        secret_vault_ref=conn.secret_vault_ref,
        granted_scopes=tuple(conn.granted_scopes),
        status=conn.status,
        cursor_token=cursor.cursor_token if cursor else None,
        last_synced_at=cursor.last_synced_at if cursor else None,
        created_at=conn.created_at,
    )


def _to_pairing_view(row: FilePairingCandidate) -> FilePairingView:
    return FilePairingView(
        id=row.id,
        tenant_id=row.tenant_id,
        exam_doc_id=row.exam_doc_id,
        solution_doc_id=row.solution_doc_id,
        similarity_score=Decimal(str(row.similarity_score)),
        signals_json=dict(row.signals_json),
        status=row.status,
        created_at=row.created_at,
    )


async def connect_google_drive(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    secret_vault_ref: str,
    granted_scopes: Sequence[str] = ("https://www.googleapis.com/auth/drive.file",),
    provider: str = "GOOGLE_DRIVE",
    base_uri: str | None = None,
    rights_status: str = "PENDING_REVIEW",
    initial_cursor_token: str = "cursor:init",
    source_id: uuid.UUID | None = None,
) -> DriveConnectionView:
    """Register a least-privilege Google Drive source connection and initial delta cursor."""
    if not name.strip():
        raise DriveSyncValidationError("INVALID_SOURCE_NAME", "Connection name is required")
    if rights_status not in ALLOWED_RIGHTS_STATUSES:
        raise DriveSyncValidationError("INVALID_RIGHTS_STATUS", f"Unsupported rights_status: {rights_status}")
    validated_scopes = validate_drive_scopes(granted_scopes)
    validated_ref = _validate_vault_ref(secret_vault_ref)
    if not initial_cursor_token.strip():
        raise DriveSyncValidationError("INVALID_CURSOR_TOKEN", "initial_cursor_token must not be blank")

    resolved_source_id = source_id
    if resolved_source_id is None:
        resolved_source_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO sources (
                    id, tenant_id, source_type, name, base_uri, adapter_key,
                    config_json, rights_status, policy_status
                ) VALUES (
                    :id, :tenant_id, 'GOOGLE_DRIVE', :name, :base_uri, 'google_drive_v3',
                    CAST(:config_json AS jsonb), :rights_status, 'ACTIVE'
                )
                """
            ),
            {
                "id": resolved_source_id,
                "tenant_id": tenant_id,
                "name": name.strip(),
                "base_uri": base_uri,
                "config_json": json.dumps({"granted_scopes": list(validated_scopes)}),
                "rights_status": rights_status,
            },
        )
    else:
        source_row = (
            await session.execute(
                text(
                    "SELECT source_type FROM sources "
                    "WHERE id = :source_id AND tenant_id = :tenant_id"
                ),
                {"source_id": resolved_source_id, "tenant_id": tenant_id},
            )
        ).mappings().one_or_none()
        if source_row is None:
            raise DriveSyncNotFoundError(f"Source {resolved_source_id} not found for tenant")
        if source_row["source_type"] != "GOOGLE_DRIVE":
            raise DriveSyncValidationError(
                "INVALID_SOURCE_TYPE",
                "Attached source must have source_type='GOOGLE_DRIVE'",
            )

    conn = SourceConnection(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_id=resolved_source_id,
        provider=provider.strip() or "GOOGLE_DRIVE",
        secret_vault_ref=validated_ref,
        granted_scopes=list(validated_scopes),
        status="CONNECTED",
    )
    cursor = SourceSyncCursor(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_connection_id=conn.id,
        cursor_token=initial_cursor_token.strip(),
    )
    session.add(conn)
    await session.flush()
    session.add(cursor)
    await session.flush()
    await session.refresh(conn)
    await session.refresh(cursor)
    return _to_connection_view(conn, cursor)


async def get_drive_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> DriveConnectionView:
    """Return a single Drive connection and its delta cursor within tenant scope."""
    conn = (
        await session.execute(
            select(SourceConnection).where(
                SourceConnection.id == connection_id,
                SourceConnection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise DriveSyncNotFoundError(f"Drive connection {connection_id} not found")
    cursor = (
        await session.execute(
            select(SourceSyncCursor).where(
                SourceSyncCursor.source_connection_id == conn.id,
                SourceSyncCursor.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    return _to_connection_view(conn, cursor)


async def revoke_drive_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> DriveConnectionView:
    """Revoke OAuth sync without deleting previously ingested documents or versions."""
    conn = (
        await session.execute(
            select(SourceConnection).where(
                SourceConnection.id == connection_id,
                SourceConnection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise DriveSyncNotFoundError(f"Drive connection {connection_id} not found")

    conn.status = "REVOKED"
    await session.execute(
        text(
            "UPDATE sources SET policy_status = 'REVOKED' "
            "WHERE id = :source_id AND tenant_id = :tenant_id"
        ),
        {"source_id": conn.source_id, "tenant_id": tenant_id},
    )
    await session.flush()
    cursor = (
        await session.execute(
            select(SourceSyncCursor).where(
                SourceSyncCursor.source_connection_id == conn.id,
                SourceSyncCursor.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    return _to_connection_view(conn, cursor)


def _check_archive_change(change: DriveFileChange, archive_policy: SafeArchivePolicy) -> None:
    if change.local_archive_path is None:
        if (
            change.mime_type.lower() in _ZIP_MIME_TYPES
            and change.size_bytes > archive_policy.max_total_uncompressed_bytes
        ):
            raise DriveSyncPolicyError(
                "ARCHIVE_SECURITY_VIOLATION",
                f"Zip archive {change.drive_file_id} exceeds max safe uncompressed limit",
            )
        return
    try:
        inspect_zip(Path(change.local_archive_path), policy=archive_policy)
    except ArchiveSecurityViolationError as exc:
        raise DriveSyncPolicyError("ARCHIVE_SECURITY_VIOLATION", str(exc)) from exc


async def _upsert_document_and_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    connection_id: uuid.UUID,
    default_rights_status: str,
    change: DriveFileChange,
    sha256: str,
) -> Literal["INGESTED", "RENAMED_OR_MOVED"]:
    doc_row = (
        await session.execute(
            text(
                "SELECT id, inferred_metadata_json FROM source_documents "
                "WHERE source_id = :source_id AND external_id = :external_id AND tenant_id = :tenant_id"
            ),
            {"source_id": source_id, "external_id": change.drive_file_id, "tenant_id": tenant_id},
        )
    ).mappings().one_or_none()

    merged_meta = dict(doc_row["inferred_metadata_json"]) if doc_row is not None else {}
    if change.metadata:
        merged_meta.update(change.metadata)
    if change.folder_id is not None:
        merged_meta["folder_id"] = change.folder_id
    merged_meta["folder_path"] = change.folder_path
    if change.subject_code is not None:
        merged_meta["subject_code"] = change.subject_code
    if change.question_count is not None:
        merged_meta["question_count"] = change.question_count

    if doc_row is not None:
        latest_version = (
            await session.execute(
                text(
                    "SELECT version_no, checksum_sha256 FROM document_versions "
                    "WHERE source_document_id = :doc_id AND tenant_id = :tenant_id "
                    "ORDER BY version_no DESC LIMIT 1"
                ),
                {"doc_id": doc_row["id"], "tenant_id": tenant_id},
            )
        ).mappings().one_or_none()

        # Rename/move (title/folder change) with identical content SHA-256 MUST NOT re-trigger OCR/ingestion.
        if latest_version is not None and str(latest_version["checksum_sha256"]).lower() == sha256:
            await session.execute(
                text(
                    "UPDATE source_documents SET title = :title, mime_type = :mime_type, "
                    "inferred_metadata_json = CAST(:meta AS jsonb) "
                    "WHERE id = :doc_id AND tenant_id = :tenant_id"
                ),
                {
                    "title": change.title,
                    "mime_type": change.mime_type,
                    "meta": json.dumps(merged_meta),
                    "doc_id": doc_row["id"],
                    "tenant_id": tenant_id,
                },
            )
            return "RENAMED_OR_MOVED"
        next_version_no = int(latest_version["version_no"]) + 1 if latest_version is not None else 1
        doc_id = doc_row["id"]
        await session.execute(
            text(
                "UPDATE source_documents SET title = :title, mime_type = :mime_type, "
                "acquisition_status = 'INGESTED', inferred_metadata_json = CAST(:meta AS jsonb) "
                "WHERE id = :doc_id AND tenant_id = :tenant_id"
            ),
            {
                "title": change.title,
                "mime_type": change.mime_type,
                "meta": json.dumps(merged_meta),
                "doc_id": doc_id,
                "tenant_id": tenant_id,
            },
        )
    else:
        doc_id = uuid.uuid4()
        next_version_no = 1
        rights_status = change.rights_status or default_rights_status
        if rights_status not in ALLOWED_RIGHTS_STATUSES:
            raise DriveSyncValidationError("INVALID_RIGHTS_STATUS", f"Unsupported rights_status: {rights_status}")
        await session.execute(
            text(
                """
                INSERT INTO source_documents (
                    id, tenant_id, source_id, external_id, title, mime_type,
                    acquisition_status, rights_status, inferred_metadata_json
                ) VALUES (
                    :id, :tenant_id, :source_id, :external_id, :title, :mime_type,
                    'INGESTED', :rights_status, CAST(:meta AS jsonb)
                )
                """
            ),
            {
                "id": doc_id,
                "tenant_id": tenant_id,
                "source_id": source_id,
                "external_id": change.drive_file_id,
                "title": change.title,
                "mime_type": change.mime_type,
                "rights_status": rights_status,
                "meta": json.dumps(merged_meta),
            },
        )

    asset_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO assets (
                id, tenant_id, asset_role, object_key, mime_type,
                sha256, size_bytes, metadata_json
            ) VALUES (
                :id, :tenant_id, 'RAW_SOURCE', :object_key, :mime_type,
                :sha256, :size_bytes, CAST(:meta AS jsonb)
            )
            """
        ),
        {
            "id": asset_id,
            "tenant_id": tenant_id,
            "object_key": change.object_key,
            "mime_type": change.mime_type,
            "sha256": sha256,
            "size_bytes": change.size_bytes,
            "meta": json.dumps({"drive_file_id": change.drive_file_id, "connection_id": str(connection_id)}),
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO document_versions (
                id, tenant_id, source_document_id, version_no, raw_asset_id, checksum_sha256
            ) VALUES (
                :id, :tenant_id, :source_document_id, :version_no, :raw_asset_id, :checksum_sha256
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "source_document_id": doc_id,
            "version_no": next_version_no,
            "raw_asset_id": asset_id,
            "checksum_sha256": sha256,
        },
    )

    envelope = build_catalog_envelope(
        event_type="drive.file.discovered",
        aggregate_type="source_connection",
        aggregate_id=connection_id,
        payload=DriveFileDiscoveredPayload(
            tenant_id=tenant_id,
            connection_id=connection_id,
            drive_file_id=change.drive_file_id,
            sha256=sha256,
            folder_path=change.folder_path,
        ),
    )
    await enqueue(session, envelope)
    return "INGESTED"


async def _run_auto_pairing(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    auto_confirm_threshold: Decimal = DEFAULT_AUTO_CONFIRM_THRESHOLD,
    propose_threshold: Decimal = DEFAULT_PROPOSE_THRESHOLD,
) -> tuple[FilePairingView, ...]:
    docs = (
        await session.execute(
            text(
                "SELECT id, title, inferred_metadata_json FROM source_documents "
                "WHERE source_id = :source_id AND tenant_id = :tenant_id "
                "ORDER BY created_at, id"
            ),
            {"source_id": source_id, "tenant_id": tenant_id},
        )
    ).mappings().all()

    pairing_inputs: list[PairingDocumentInput] = []
    for row in docs:
        title = row["title"]
        if not title:
            continue
        meta = dict(row["inferred_metadata_json"] or {})
        raw_qcount = meta.get("question_count")
        qcount = int(raw_qcount) if isinstance(raw_qcount, int) and raw_qcount >= 0 else None
        pairing_inputs.append(
            PairingDocumentInput(
                doc_id=str(row["id"]),
                filename=str(title),
                folder_id=meta.get("folder_id") or meta.get("folder_path"),
                subject_code=meta.get("subject_code"),
                question_count=qcount,
            )
        )

    computed = auto_pair_exam_and_hdc_documents(
        pairing_inputs,
        auto_confirm_threshold=auto_confirm_threshold,
        propose_threshold=propose_threshold,
    )
    if not computed:
        return ()

    existing_rows = (
        await session.execute(
            select(FilePairingCandidate).where(FilePairingCandidate.tenant_id == tenant_id)
        )
    ).scalars().all()
    existing_pairs = {(row.exam_doc_id, row.solution_doc_id) for row in existing_rows}

    created: list[FilePairingCandidate] = []
    for pairing in computed:
        exam_uuid = uuid.UUID(pairing.exam_doc_id)
        solution_uuid = uuid.UUID(pairing.solution_doc_id)
        if (exam_uuid, solution_uuid) in existing_pairs:
            continue
        signals_payload = {
            "filename_stem": str(pairing.signals.filename_similarity),
            "folder_proximity": str(pairing.signals.folder_proximity),
            "header_match": str(pairing.signals.subject_match),
            "item_count_match": str(pairing.signals.item_count_similarity),
        }
        candidate = FilePairingCandidate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            exam_doc_id=exam_uuid,
            solution_doc_id=solution_uuid,
            similarity_score=pairing.similarity_score,
            signals_json=signals_payload,
            status=pairing.status,
        )
        session.add(candidate)
        await session.flush()
        await session.refresh(candidate)
        created.append(candidate)
        if pairing.status == "CONFIRMED":
            await session.execute(
                text(
                    "UPDATE source_documents SET paired_solution_doc_id = :solution_doc_id "
                    "WHERE id = :exam_doc_id AND tenant_id = :tenant_id"
                ),
                {"solution_doc_id": solution_uuid, "exam_doc_id": exam_uuid, "tenant_id": tenant_id},
            )
    return tuple(_to_pairing_view(row) for row in created)


async def sync_drive_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    next_cursor_token: str,
    changes: Sequence[DriveFileChange],
    auto_pair: bool = True,
    archive_policy: SafeArchivePolicy = DEFAULT_POLICY,
    auto_confirm_threshold: Decimal = DEFAULT_AUTO_CONFIRM_THRESHOLD,
    propose_threshold: Decimal = DEFAULT_PROPOSE_THRESHOLD,
) -> DriveSyncJobView:
    """Apply incremental Drive changes without re-OCR on renames/moves and block unsafe archives."""
    if not next_cursor_token.strip():
        raise DriveSyncValidationError("INVALID_CURSOR_TOKEN", "next_cursor_token is required")

    conn = (
        await session.execute(
            select(SourceConnection).where(
                SourceConnection.id == connection_id,
                SourceConnection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise DriveSyncNotFoundError(f"Drive connection {connection_id} not found")
    if conn.status != "CONNECTED":
        raise DriveSyncPolicyError(
            "DRIVE_CONNECTION_NOT_ACTIVE",
            f"Drive connection {connection_id} has status={conn.status}",
        )

    source_row = (
        await session.execute(
            text(
                "SELECT policy_status, rights_status FROM sources "
                "WHERE id = :source_id AND tenant_id = :tenant_id"
            ),
            {"source_id": conn.source_id, "tenant_id": tenant_id},
        )
    ).mappings().one_or_none()
    if source_row is None:
        raise DriveSyncNotFoundError(f"Source {conn.source_id} not found")
    if source_row["policy_status"] != "ACTIVE":
        raise DriveSyncPolicyError(
            "SOURCE_POLICY_NOT_ACTIVE",
            f"Source {conn.source_id} has policy_status={source_row['policy_status']}",
        )

    for change in changes:
        if change.removed:
            continue
        _validate_sha256(change.sha256)
        if change.size_bytes < 0:
            raise DriveSyncValidationError("INVALID_SIZE_BYTES", "size_bytes must be non-negative")
        _check_archive_change(change, archive_policy)

    ingested_count = 0
    renamed_or_moved_count = 0
    skipped_count = 0
    for change in changes:
        if change.removed:
            skipped_count += 1
            continue
        outcome = await _upsert_document_and_version(
            session,
            tenant_id=tenant_id,
            source_id=conn.source_id,
            connection_id=conn.id,
            default_rights_status=str(source_row["rights_status"]),
            change=change,
            sha256=_validate_sha256(change.sha256),
        )
        if outcome == "INGESTED":
            ingested_count += 1
        else:
            renamed_or_moved_count += 1

    await session.execute(
        pg_insert(SourceSyncCursor)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            source_connection_id=conn.id,
            cursor_token=next_cursor_token.strip(),
        )
        .on_conflict_do_update(
            index_elements=[SourceSyncCursor.source_connection_id],
            set_={"cursor_token": next_cursor_token.strip(), "last_synced_at": text("NOW()")},
        )
    )

    pairings_created: tuple[FilePairingView, ...] = ()
    if auto_pair:
        pairings_created = await _run_auto_pairing(
            session,
            tenant_id=tenant_id,
            source_id=conn.source_id,
            auto_confirm_threshold=auto_confirm_threshold,
            propose_threshold=propose_threshold,
        )

    job_id = uuid.uuid4()
    metrics = {
        "connection_id": str(conn.id),
        "cursor_token": next_cursor_token.strip(),
        "ingested_count": ingested_count,
        "renamed_or_moved_count": renamed_or_moved_count,
        "skipped_count": skipped_count,
        "pairings_created_count": len(pairings_created),
    }
    job_row = (
        await session.execute(
            text(
                """
                INSERT INTO acquisition_jobs (
                    id, tenant_id, source_id, job_type, job_status, current_stage,
                    idempotency_key, attempt_count, metrics_json
                ) VALUES (
                    :id, :tenant_id, :source_id, 'DRIVE_SYNC', 'COMPLETED', 'SYNCED',
                    :idempotency_key, 1, CAST(:metrics_json AS jsonb)
                )
                RETURNING created_at
                """
            ),
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "source_id": conn.source_id,
                "idempotency_key": f"drive_sync:{conn.id}:{job_id}",
                "metrics_json": json.dumps(metrics),
            },
        )
    ).mappings().one()

    return DriveSyncJobView(
        job_id=job_id,
        tenant_id=tenant_id,
        connection_id=conn.id,
        source_id=conn.source_id,
        job_status="COMPLETED",
        cursor_token=next_cursor_token.strip(),
        ingested_count=ingested_count,
        renamed_or_moved_count=renamed_or_moved_count,
        skipped_count=skipped_count,
        pairings_created=pairings_created,
        created_at=job_row["created_at"],
    )


async def get_drive_sync_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
) -> DriveSyncJobView:
    """Fetch persisted status & metrics for a Drive sync job within tenant scope."""
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, source_id, job_status, metrics_json, created_at "
                "FROM acquisition_jobs "
                "WHERE id = :id AND tenant_id = :tenant_id AND job_type = 'DRIVE_SYNC'"
            ),
            {"id": job_id, "tenant_id": tenant_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise DriveSyncNotFoundError(f"Drive sync job {job_id} not found")
    metrics = dict(row["metrics_json"] or {})
    return DriveSyncJobView(
        job_id=row["id"],
        tenant_id=row["tenant_id"],
        connection_id=uuid.UUID(str(metrics["connection_id"])),
        source_id=row["source_id"],
        job_status=row["job_status"],
        cursor_token=str(metrics.get("cursor_token", "")),
        ingested_count=int(metrics.get("ingested_count", 0)),
        renamed_or_moved_count=int(metrics.get("renamed_or_moved_count", 0)),
        skipped_count=int(metrics.get("skipped_count", 0)),
        pairings_created=(),
        created_at=row["created_at"],
    )


async def confirm_or_reject_file_pairing(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    pairing_id: uuid.UUID,
    decision: Literal["CONFIRMED", "REJECTED"] = "CONFIRMED",
) -> FilePairingView:
    """Confirm or reject an automated exam + solution pairing candidate."""
    if decision not in {"CONFIRMED", "REJECTED"}:
        raise DriveSyncValidationError("INVALID_PAIRING_DECISION", f"Unsupported decision: {decision}")

    pairing = (
        await session.execute(
            select(FilePairingCandidate).where(
                FilePairingCandidate.id == pairing_id,
                FilePairingCandidate.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if pairing is None:
        raise DriveSyncNotFoundError(f"Pairing candidate {pairing_id} not found")

    pairing.status = decision
    paired_solution = pairing.solution_doc_id if decision == "CONFIRMED" else None
    await session.execute(
        text(
            "UPDATE source_documents SET paired_solution_doc_id = :paired_solution "
            "WHERE id = :exam_doc_id AND tenant_id = :tenant_id"
        ),
        {
            "paired_solution": paired_solution,
            "exam_doc_id": pairing.exam_doc_id,
            "tenant_id": tenant_id,
        },
    )
    await session.flush()
    await session.refresh(pairing)
    return _to_pairing_view(pairing)
