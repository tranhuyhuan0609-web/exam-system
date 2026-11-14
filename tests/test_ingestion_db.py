"""Integration tests for A01 ingestion service, FSM, DLQ, magic-byte guards, RLS, and /api/v1/imports router."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import time
import uuid
import zipfile

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.modules.ingestion.internal.models import (
    AcquisitionJob,
    DeadLetterItem,
    DocumentVersion,
)
from app.modules.ingestion.internal.service import apply_import_job_event
from app.modules.ingestion.internal.state import FailJobEvent
from app.modules.ingestion.router import router as ingestion_router
from app.modules.outbox.internal.models import OutboxEvent
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction


def run(coro):
    return asyncio.run(coro)


def _make_pdf_bytes(label: str = "Sample Exam", num_pages: int = 3) -> bytes:
    with pymupdf.open() as doc:
        for i in range(num_pages):
            page = doc.new_page(width=400, height=600)
            page.insert_text((50, 100), f"{label} - Page {i + 1}")
        return doc.tobytes()


def _make_xxe_docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><doc/>',
        )
    return buf.getvalue()


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(ingestion_router)
    return TestClient(test_app)


def _auth_headers(*, tenant_id: uuid.UUID, roles: list[str] | None = None) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles or ["TEACHER"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
def _ensure_ingestion_schema() -> None:
    """Create B03 assets + A01 ingestion tables, RLS policies, and runtime grants."""
    ddl_statements = (
        """
        CREATE TABLE IF NOT EXISTS assets (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            parent_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
            asset_role VARCHAR(40) NOT NULL
                CHECK (asset_role IN (
                    'RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF',
                    'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL'
                )),
            object_key VARCHAR(512) UNIQUE NOT NULL,
            mime_type VARCHAR(120) NOT NULL,
            sha256 CHAR(64) NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
            is_immutable_bronze BOOLEAN GENERATED ALWAYS AS (asset_role = 'RAW_SOURCE') STORED,
            metadata_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_assets_tenant_sha256 ON assets (tenant_id, sha256)",
        """
        CREATE TABLE IF NOT EXISTS sources (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_type VARCHAR(40) NOT NULL
                CHECK (source_type IN ('MANUAL_UPLOAD', 'GOOGLE_DRIVE', 'WEB_CRAWLER', 'PARTNER_API')),
            name VARCHAR(255) NOT NULL,
            base_uri VARCHAR(512),
            adapter_key VARCHAR(80),
            config_json JSONB NOT NULL DEFAULT '{}',
            rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
                CHECK (rights_status IN (
                    'PENDING_REVIEW', 'OWNED', 'LICENSED',
                    'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN'
                )),
            attribution_text VARCHAR(500),
            policy_status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
                CHECK (policy_status IN ('ACTIVE', 'PAUSED_DRIFT', 'PAUSED_MANUAL', 'REVOKED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS source_documents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            external_id VARCHAR(255) NOT NULL,
            title VARCHAR(500),
            mime_type VARCHAR(120) NOT NULL,
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'INGESTED'
                CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
            rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
                CHECK (rights_status IN (
                    'PENDING_REVIEW', 'OWNED', 'LICENSED',
                    'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN'
                )),
            inferred_metadata_json JSONB NOT NULL DEFAULT '{}',
            paired_solution_doc_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (source_id, external_id),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_document_id UUID NOT NULL,
            version_no INT NOT NULL,
            raw_asset_id UUID NOT NULL,
            checksum_sha256 CHAR(64) NOT NULL,
            page_count INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (source_document_id, version_no),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (source_document_id, tenant_id)
                REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
            FOREIGN KEY (raw_asset_id, tenant_id)
                REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS acquisition_jobs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_id UUID,
            document_version_id UUID,
            job_type VARCHAR(40) NOT NULL
                CHECK (job_type IN ('EXAM_IMPORT', 'DRIVE_SYNC', 'WEB_CRAWL', 'REPROCESS_EXTRACTION')),
            job_status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
                CHECK (job_status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')),
            current_stage VARCHAR(50),
            idempotency_key VARCHAR(128) UNIQUE NOT NULL,
            attempt_count INT NOT NULL DEFAULT 0,
            metrics_json JSONB NOT NULL DEFAULT '{}',
            last_error_code VARCHAR(80),
            last_error_detail TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id),
            FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_acquisition_jobs_status_created ON acquisition_jobs (job_status, created_at)",
        """
        CREATE TABLE IF NOT EXISTS pipeline_stage_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            acquisition_job_id UUID NOT NULL,
            stage_name VARCHAR(50) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'RUNNING'
                CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')),
            worker_version VARCHAR(40),
            input_ref VARCHAR(512),
            output_ref VARCHAR(512),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            FOREIGN KEY (acquisition_job_id, tenant_id)
                REFERENCES acquisition_jobs(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dead_letter_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            acquisition_job_id UUID,
            source_queue VARCHAR(80) NOT NULL,
            payload_json JSONB NOT NULL,
            error_trace TEXT NOT NULL,
            replayed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )

    strict_tables = (
        "sources",
        "source_documents",
        "document_versions",
        "acquisition_jobs",
        "pipeline_stage_runs",
        "dead_letter_items",
    )

    async def create() -> None:
        async with engine.begin() as conn:
            for stmt in ddl_statements:
                await conn.execute(text(stmt))
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)

            await conn.execute(text("ALTER TABLE assets ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text("ALTER TABLE assets FORCE ROW LEVEL SECURITY"))
            await conn.execute(text("DROP POLICY IF EXISTS hybrid_write_policy ON assets"))
            await conn.execute(text("DROP POLICY IF EXISTS hybrid_read_policy ON assets"))
            await conn.execute(
                text(
                    "CREATE POLICY hybrid_write_policy ON assets FOR ALL "
                    "USING (tenant_id = (SELECT current_app_tenant_id())) "
                    "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                )
            )
            await conn.execute(
                text(
                    "CREATE POLICY hybrid_read_policy ON assets FOR SELECT USING ("
                    "tenant_id = (SELECT current_app_tenant_id()) "
                    "OR (tenant_id = system_public_tenant_id() AND asset_role <> 'RAW_SOURCE')"
                    ")"
                )
            )
            await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON assets TO ulp_app_rw"))
            await conn.execute(text("GRANT SELECT, INSERT ON assets TO ulp_worker_ingestion"))

            for tbl in strict_tables:
                await conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {tbl}"))
                await conn.execute(
                    text(
                        f"CREATE POLICY tenant_isolation_policy ON {tbl} "
                        "USING (tenant_id = (SELECT current_app_tenant_id())) "
                        "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                    )
                )
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO ulp_app_rw"))

            await conn.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE ON sources, source_documents, "
                    "document_versions, acquisition_jobs, pipeline_stage_runs TO ulp_worker_ingestion"
                )
            )
            await conn.execute(text("GRANT SELECT, INSERT ON dead_letter_items TO ulp_worker_ingestion"))

    run(create())


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create() -> tuple[uuid.UUID, uuid.UUID]:
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a = Tenant(slug=f"ing-a-{suffix}", name="Ingestion Tenant A")
            b = Tenant(slug=f"ing-b-{suffix}", name="Ingestion Tenant B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


def test_submit_import_multipart_dedup_and_idempotency(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants
    pdf_bytes = _make_pdf_bytes("Physics Midterm", num_pages=3)
    expected_sha = hashlib.sha256(pdf_bytes).hexdigest()
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a)
    headers_b = _auth_headers(tenant_id=tenant_b)

    # 1. Multipart upload -> 201 Created
    resp = client.post(
        "/api/v1/imports",
        headers=headers_a,
        files={"file": ("physics-midterm.pdf", pdf_bytes, "application/pdf")},
        data={"title": "Physics Midterm", "idempotency_key": "idem-phys-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_duplicate"] is False
    assert body["idempotent_replay"] is False
    assert body["document_version"]["checksum_sha256"] == expected_sha
    assert body["document_version"]["page_count"] == 3
    assert body["document_version"]["version_no"] == 1
    assert body["job"]["job_status"] == "RUNNING"
    assert body["job"]["current_stage"] == "EXTRACT"
    stage_map = {s["stage_name"]: s["status"] for s in body["job"]["stages"]}
    assert stage_map == {"INGEST": "COMPLETED", "EXTRACT": "RUNNING"}

    job_id = body["job"]["id"]
    doc_ver_id = body["document_version"]["id"]

    # 2. Same idempotency_key & same payload -> 200 idempotent replay
    replay = client.post(
        "/api/v1/imports",
        headers=headers_a,
        json={
            "filename": "physics-midterm.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "idempotency_key": "idem-phys-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["id"] == job_id

    # 3. Same idempotency_key with different content -> 422 PAYLOAD_HASH_MISMATCH
    other_pdf = _make_pdf_bytes("Different Paper", num_pages=1)
    conflict = client.post(
        "/api/v1/imports",
        headers=headers_a,
        json={
            "filename": "other.pdf",
            "content_base64": base64.b64encode(other_pdf).decode("ascii"),
            "idempotency_key": "idem-phys-1",
        },
    )
    assert conflict.status_code == 422
    assert conflict.json()["error_code"] == "PAYLOAD_HASH_MISMATCH"
    assert conflict.headers["content-type"].startswith("application/problem+json")

    # 4. Same file uploaded without idempotency key in SAME tenant -> 200 duplicate
    dup = client.post(
        "/api/v1/imports",
        headers=headers_a,
        files={"file": ("reupload.pdf", pdf_bytes, "application/pdf")},
    )
    assert dup.status_code == 200
    assert dup.json()["is_duplicate"] is True
    assert dup.json()["document_version"]["id"] == doc_ver_id

    # 5. Same file uploaded in Tenant B -> 201 Created (strictly isolated per tenant)
    other_tenant = client.post(
        "/api/v1/imports",
        headers=headers_b,
        files={"file": ("physics-midterm.pdf", pdf_bytes, "application/pdf")},
    )
    assert other_tenant.status_code == 201
    assert other_tenant.json()["is_duplicate"] is False
    assert other_tenant.json()["document_version"]["id"] != doc_ver_id

    async def check_counts() -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            dv_count = int(
                (await session.execute(select(func.count(DocumentVersion.id)))).scalar_one()
            )
            assert dv_count == 1
            outbox_count = int(
                (
                    await session.execute(
                        select(func.count(OutboxEvent.event_id)).where(
                            OutboxEvent.aggregate_id == uuid.UUID(job_id)
                        )
                    )
                ).scalar_one()
            )
            assert outbox_count >= 2

    run(check_counts())


def test_retry_preserves_completed_stages_then_transitions_to_dead_letter(
    tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tenant_a, _ = tenants
    pdf_bytes = _make_pdf_bytes("Retry Exam", num_pages=2)
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_a)

    submit = client.post(
        "/api/v1/imports",
        headers=headers,
        json={
            "filename": "retry.pdf",
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "max_attempts": 2,
        },
    )
    assert submit.status_code == 201
    job_id = uuid.UUID(submit.json()["job"]["id"])

    # Fail EXTRACT on attempt 1 -> FAILED (with INGEST still COMPLETED)
    async def fail_extract(event_id: str) -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await apply_import_job_event(
                session,
                tenant_id=tenant_a,
                job_id=job_id,
                event=FailJobEvent(
                    event_id=event_id,
                    stage_name="EXTRACT",
                    error_code="OCR_TIMEOUT",
                    error_detail="Worker timed out on page 2",
                ),
            )

    run(fail_extract("fail-1"))

    polled = client.get(f"/api/v1/imports/{job_id}", headers=headers)
    assert polled.status_code == 200
    assert polled.json()["job_status"] == "FAILED"
    stages = {s["stage_name"]: s for s in polled.json()["stages"]}
    assert stages["INGEST"]["status"] == "COMPLETED"
    assert stages["EXTRACT"]["status"] == "FAILED"
    ingest_finished_before = stages["INGEST"]["finished_at"]

    # Retry -> attempt_count becomes 2, INGEST untouched, only EXTRACT reopened to RUNNING
    retried = client.post(f"/api/v1/imports/{job_id}/retry", headers=headers, json={})
    assert retried.status_code == 200
    assert retried.json()["job_status"] == "RUNNING"
    assert retried.json()["attempt_count"] == 2
    stages_after = {s["stage_name"]: s for s in retried.json()["stages"]}
    assert stages_after["INGEST"]["status"] == "COMPLETED"
    assert stages_after["INGEST"]["finished_at"] == ingest_finished_before
    assert stages_after["EXTRACT"]["status"] == "RUNNING"
    assert stages_after["EXTRACT"]["finished_at"] is None

    # Cannot retry a RUNNING job
    bad_retry = client.post(f"/api/v1/imports/{job_id}/retry", headers=headers, json={})
    assert bad_retry.status_code == 422
    assert bad_retry.json()["transition_code"] == "RETRY_NOT_ALLOWED"

    # Second failure when attempt_count == max_attempts (2) moves job to DEAD_LETTER + dead_letter_items
    run(fail_extract("fail-2"))
    dlq_poll = client.get(f"/api/v1/imports/{job_id}", headers=headers)
    assert dlq_poll.status_code == 200
    assert dlq_poll.json()["job_status"] == "DEAD_LETTER"
    assert dlq_poll.json()["dead_letter_count"] == 1

    async def verify_dlq_row() -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            dlq_rows = (
                await session.execute(select(DeadLetterItem).where(DeadLetterItem.acquisition_job_id == job_id))
            ).scalars().all()
            assert len(dlq_rows) == 1
            assert dlq_rows[0].payload_json["error_code"] == "OCR_TIMEOUT"

    run(verify_dlq_row())


def test_cancel_terminal_guard_security_and_cross_tenant_rls(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants
    pdf_bytes = _make_pdf_bytes("Cancel Exam", num_pages=1)
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a)
    headers_b = _auth_headers(tenant_id=tenant_b)

    # 1. Missing auth -> 401
    assert client.post("/api/v1/imports", json={"filename": "a.pdf", "content_base64": "QQ=="}).status_code == 401

    # 2. X-Tenant-Id spoofing -> 403
    spoof = client.post(
        "/api/v1/imports",
        headers={**headers_a, "X-Tenant-Id": str(tenant_b)},
        json={"filename": "a.pdf", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert spoof.status_code == 403

    # 3. Magic-byte spoofing (.pdf with MZ header) -> 422
    exe_spoof = client.post(
        "/api/v1/imports",
        headers=headers_a,
        files={"file": ("malware.pdf", b"MZ\x90\x00not a pdf", "application/pdf")},
    )
    assert exe_spoof.status_code == 422

    # 4. XXE XML bomb inside DOCX -> 422
    xxe_resp = client.post(
        "/api/v1/imports",
        headers=headers_a,
        files={
            "file": (
                "bomb.docx",
                _make_xxe_docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert xxe_resp.status_code == 422

    # 5. Path traversal filename -> 422
    trav = client.post(
        "/api/v1/imports",
        headers=headers_a,
        json={"filename": "../secret.pdf", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert trav.status_code == 422

    # 6. Create valid job, verify cross-tenant 404 + RLS, then cancel + verify terminal cancel guard
    created = client.post(
        "/api/v1/imports",
        headers=headers_a,
        json={"filename": "valid.pdf", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert created.status_code == 201
    job_id = created.json()["job"]["id"]

    # Cross-tenant GET / retry / cancel -> 404 (never 403/200)
    assert client.get(f"/api/v1/imports/{job_id}", headers=headers_b).status_code == 404
    assert client.post(f"/api/v1/imports/{job_id}/retry", headers=headers_b, json={}).status_code == 404
    assert client.post(f"/api/v1/imports/{job_id}/cancel", headers=headers_b, json={}).status_code == 404

    async def verify_rls_isolation() -> None:
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            visible = (
                await session.execute(select(AcquisitionJob).where(AcquisitionJob.id == uuid.UUID(job_id)))
            ).scalar_one_or_none()
            assert visible is None

    run(verify_rls_isolation())

    cancelled = client.post(
        f"/api/v1/imports/{job_id}/cancel",
        headers=headers_a,
        json={"reason": "No longer needed"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["job_status"] == "CANCELLED"

    # Re-cancelling terminal CANCELLED job -> 422 CANCEL_FROM_TERMINAL_STATE
    re_cancel = client.post(f"/api/v1/imports/{job_id}/cancel", headers=headers_a, json={})
    assert re_cancel.status_code == 422
    assert re_cancel.json()["transition_code"] == "CANCEL_FROM_TERMINAL_STATE"
    assert re_cancel.headers["content-type"].startswith("application/problem+json")
