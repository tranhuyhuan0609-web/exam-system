"""Integration tests for A01 ingestion service, FSM, DLQ, magic-byte guards, RLS, and /api/v1/imports router."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import time
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
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
from tests.pdf_fixtures import PdfSketch


def run(coro):
    return asyncio.run(coro)


def _make_pdf_bytes(label: str = "Sample Exam", num_pages: int = 3) -> bytes:
    sketch = PdfSketch()
    for i in range(num_pages):
        page = sketch.new_page(width=400, height=600)
        page.text(50, 100, f"{label} - Page {i + 1}")
    return sketch.tobytes()


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
        roles=roles or ["CONTENT_AUTHOR"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


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
