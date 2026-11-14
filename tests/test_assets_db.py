"""Integration tests for B03 assets DB service, RLS policies, preview slicing, and /api/v1/assets router."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.modules.assets.internal.models import Asset
from app.modules.assets.router import router as assets_router
from app.modules.outbox.internal.models import OutboxEvent
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction
from tests.pdf_fixtures import PdfSketch


def run(coro):
    return asyncio.run(coro)


def _make_pdf_bytes(num_pages: int = 5) -> bytes:
    sketch = PdfSketch()
    for idx in range(num_pages):
        page = sketch.new_page(width=400, height=600)
        page.text(50, 100, f"Exam Page {idx + 1} - Secret Solution Content")
    return sketch.tobytes()


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(assets_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    roles: list[str] | None = None,
    entitlement_tier: str = "FREE",
    user_id: uuid.UUID | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=roles or ["STUDENT"],
        entitlement_tier=entitlement_tier,
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
def _ensure_assets_schema() -> None:
    """Create B03 assets table, RLS policies, and runtime grants before _clean_tables truncates."""
    ddl_statements = (
        """
        CREATE TABLE IF NOT EXISTS assets (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            parent_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
            asset_role VARCHAR(40) NOT NULL
                CONSTRAINT ck_assets_asset_role
                CHECK (asset_role IN (
                    'RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF',
                    'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL'
                )),
            object_key VARCHAR(512) UNIQUE NOT NULL,
            mime_type VARCHAR(120) NOT NULL,
            sha256 CHAR(64) NOT NULL,
            size_bytes BIGINT NOT NULL
                CONSTRAINT ck_assets_size_bytes_non_negative CHECK (size_bytes >= 0),
            is_immutable_bronze BOOLEAN GENERATED ALWAYS AS (asset_role = 'RAW_SOURCE') STORED,
            metadata_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_assets_id_tenant_id UNIQUE (id, tenant_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_assets_tenant_sha256 ON assets (tenant_id, sha256)",
        "ALTER TABLE assets ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE assets FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS hybrid_write_policy ON assets",
        "DROP POLICY IF EXISTS hybrid_read_policy ON assets",
        """
        CREATE POLICY hybrid_write_policy ON assets FOR ALL
            USING (tenant_id = (SELECT current_app_tenant_id()))
            WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))
        """,
        """
        CREATE POLICY hybrid_read_policy ON assets FOR SELECT USING (
            tenant_id = (SELECT current_app_tenant_id())
            OR (tenant_id = system_public_tenant_id() AND asset_role <> 'RAW_SOURCE')
        )
        """,
        "GRANT SELECT, INSERT, UPDATE, DELETE ON assets TO ulp_app_rw",
        "GRANT SELECT, INSERT ON assets TO ulp_worker_ingestion",
    )

    async def create() -> None:
        async with engine.begin() as conn:
            for stmt in ddl_statements:
                await conn.execute(text(stmt))
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    run(create())


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create() -> tuple[uuid.UUID, uuid.UUID]:
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a = Tenant(slug=f"assets-a-{suffix}", name="Assets Tenant A")
            b = Tenant(slug=f"assets-b-{suffix}", name="Assets Tenant B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


def test_upload_finalize_preview_slicing_and_outbox_event(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, _ = tenants
    pdf_bytes = _make_pdf_bytes(num_pages=5)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"], entitlement_tier="TEACHER_PRO")

    init_resp = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers,
        json={
            "mime_type": "application/pdf",
            "sha256": sha,
            "size_bytes": len(pdf_bytes),
            "filename": "de-thi-toan.pdf",
            "asset_role": "RAW_SOURCE",
            "requires_pro": True,
        },
    )
    assert init_resp.status_code == 201, init_resp.text
    init_body = init_resp.json()
    assert init_body["deduplicated"] is False
    assert init_body["put_descriptor"]["ttl_seconds"] == 900
    asset_id = init_body["asset"]["id"]
    assert init_body["asset"]["is_immutable_bronze"] is True
    assert init_body["asset"]["finalized"] is False

    # Signed URL before finalize must be blocked (422)
    pre_signed = client.get(f"/api/v1/assets/{asset_id}/signed-url", headers=headers)
    assert pre_signed.status_code == 422
    assert pre_signed.headers["content-type"].startswith("application/problem+json")

    fin_resp = client.post(
        f"/api/v1/assets/{asset_id}/finalize",
        headers=headers,
        json={
            "scan_status": "CLEAN",
            "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        },
    )
    assert fin_resp.status_code == 200, fin_resp.text
    fin_body = fin_resp.json()
    assert fin_body["session_status"] == "FINALIZED"
    assert fin_body["scan_status"] == "CLEAN"
    assert fin_body["asset"]["finalized"] is True
    assert fin_body["asset"]["page_count"] == 5
    assert fin_body["asset"]["preview_pages"] == 2
    assert fin_body["preview_asset"] is not None
    assert fin_body["preview_asset"]["asset_role"] == "PREVIEW_PDF"
    assert fin_body["preview_asset"]["parent_asset_id"] == asset_id
    assert fin_body["preview_asset"]["is_immutable_bronze"] is False

    async def verify_db() -> None:
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            preview_row = (
                await session.execute(
                    select(Asset).where(
                        Asset.parent_asset_id == uuid.UUID(asset_id),
                        Asset.asset_role == "PREVIEW_PDF",
                    )
                )
            ).scalar_one()
            assert preview_row.is_immutable_bronze is False

            events = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(asset_id))
                )
            ).scalars().all()
            assert len(events) == 1
            assert events[0].event_type == "asset.finalized"

    run(verify_db())


def test_deduplication_scoped_to_tenant(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants
    pdf_bytes = _make_pdf_bytes(num_pages=2)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    headers_b = _auth_headers(tenant_id=tenant_b, roles=["TEACHER"])

    r1 = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers_a,
        json={"mime_type": "application/pdf", "sha256": sha, "size_bytes": len(pdf_bytes), "filename": "a.pdf"},
    )
    assert r1.status_code == 201
    asset_a_id = r1.json()["asset"]["id"]

    fin = client.post(
        f"/api/v1/assets/{asset_a_id}/finalize",
        headers=headers_a,
        json={"scan_status": "CLEAN", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert fin.status_code == 200

    # Same tenant + same SHA-256 deduplicates (200)
    r_dup = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers_a,
        json={"mime_type": "application/pdf", "sha256": sha, "size_bytes": len(pdf_bytes), "filename": "dup.pdf"},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["deduplicated"] is True
    assert r_dup.json()["asset"]["id"] == asset_a_id

    # Different tenant + same SHA-256 must NOT cross-dedup
    r_other = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers_b,
        json={"mime_type": "application/pdf", "sha256": sha, "size_bytes": len(pdf_bytes), "filename": "b.pdf"},
    )
    assert r_other.status_code == 201
    assert r_other.json()["deduplicated"] is False
    assert r_other.json()["asset"]["id"] != asset_a_id


def test_signed_url_freemium_preview_vs_pro_and_idor_rls(tenants: tuple[uuid.UUID, uuid.UUID]) -> None:
    tenant_a, tenant_b = tenants
    pdf_bytes = _make_pdf_bytes(num_pages=4)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    client = _api_client()

    teacher_pro_a = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"], entitlement_tier="TEACHER_PRO")
    student_free_a = _auth_headers(tenant_id=tenant_a, roles=["STUDENT"], entitlement_tier="FREE")
    student_pro_a = _auth_headers(tenant_id=tenant_a, roles=["STUDENT"], entitlement_tier="STUDENT_PRO")
    other_tenant_b = _auth_headers(tenant_id=tenant_b, roles=["ADMIN"], entitlement_tier="SCHOOL_ENTERPRISE")

    init = client.post(
        "/api/v1/assets/upload-sessions",
        headers=teacher_pro_a,
        json={
            "mime_type": "application/pdf",
            "sha256": sha,
            "size_bytes": len(pdf_bytes),
            "filename": "pro-exam.pdf",
            "requires_pro": True,
        },
    )
    asset_id = init.json()["asset"]["id"]
    fin = client.post(
        f"/api/v1/assets/{asset_id}/finalize",
        headers=teacher_pro_a,
        json={"scan_status": "CLEAN", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert fin.status_code == 200
    preview_key = fin.json()["asset"]["preview_object_key"]
    original_key = fin.json()["asset"]["object_key"]

    # FREE user receives strictly capped 2-page PREVIEW_PDF
    free_url = client.get(f"/api/v1/assets/{asset_id}/signed-url?ttl_seconds=300", headers=student_free_a)
    assert free_url.status_code == 200
    assert free_url.json()["variant"] == "PREVIEW_PDF"
    assert free_url.json()["page_limit"] == 2
    assert free_url.json()["object_key"] == preview_key
    assert free_url.json()["headers"]["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in free_url.json()["headers"]

    # PRO user receives ORIGINAL_PDF
    pro_url = client.get(f"/api/v1/assets/{asset_id}/signed-url?ttl_seconds=900", headers=student_pro_a)
    assert pro_url.status_code == 200
    assert pro_url.json()["variant"] == "ORIGINAL_PDF"
    assert pro_url.json()["page_limit"] is None
    assert pro_url.json()["object_key"] == original_key

    # TTL > 900s must fail with RFC 7807 problem+json (422)
    bad_ttl = client.get(f"/api/v1/assets/{asset_id}/signed-url?ttl_seconds=901", headers=student_pro_a)
    assert bad_ttl.status_code == 422
    assert bad_ttl.headers["content-type"].startswith("application/problem+json")

    # Cross-tenant IDOR guard: Tenant B gets 404 (never 403/200)
    idor_resp = client.get(f"/api/v1/assets/{asset_id}/signed-url", headers=other_tenant_b)
    assert idor_resp.status_code == 404
    assert idor_resp.headers["content-type"].startswith("application/problem+json")

    idor_fin = client.post(
        f"/api/v1/assets/{asset_id}/finalize",
        headers=other_tenant_b,
        json={"scan_status": "CLEAN"},
    )
    assert idor_fin.status_code == 404


def test_security_guards_path_traversal_spoofing_magic_and_infected(
    tenants: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tenant_a, tenant_b = tenants
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_a, roles=["TEACHER"])
    fake_sha = "a" * 64

    # 1. Missing JWT -> 401
    assert (
        client.post(
            "/api/v1/assets/upload-sessions",
            json={"mime_type": "application/pdf", "sha256": fake_sha, "size_bytes": 100},
        ).status_code
        == 401
    )

    # 2. X-Tenant-Id spoofing -> 403
    spoof_resp = client.post(
        "/api/v1/assets/upload-sessions",
        headers={**headers, "X-Tenant-Id": str(tenant_b)},
        json={"mime_type": "application/pdf", "sha256": fake_sha, "size_bytes": 100},
    )
    assert spoof_resp.status_code == 403
    assert spoof_resp.headers["content-type"].startswith("application/problem+json")

    # 3. Path traversal in filename -> 422
    traversal_resp = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers,
        json={
            "mime_type": "application/pdf",
            "sha256": fake_sha,
            "size_bytes": 100,
            "filename": "../../etc/passwd",
        },
    )
    assert traversal_resp.status_code == 422
    assert traversal_resp.headers["content-type"].startswith("application/problem+json")

    # 4. Spoofed EXE bytes pretending to be PDF -> 422 on finalize
    spoof_bytes = b"MZ\x90\x00This is an executable disguised as PDF"
    spoof_sha = hashlib.sha256(spoof_bytes).hexdigest()
    s1 = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers,
        json={
            "mime_type": "application/pdf",
            "sha256": spoof_sha,
            "size_bytes": len(spoof_bytes),
            "filename": "fake.pdf",
        },
    )
    assert s1.status_code == 201
    f1 = client.post(
        f"/api/v1/assets/{s1.json()['asset']['id']}/finalize",
        headers=headers,
        json={"scan_status": "CLEAN", "content_base64": base64.b64encode(spoof_bytes).decode("ascii")},
    )
    assert f1.status_code == 422

    # 5. Virus scan INFECTED -> 422
    pdf_bytes = _make_pdf_bytes(num_pages=1)
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    s2 = client.post(
        "/api/v1/assets/upload-sessions",
        headers=headers,
        json={
            "mime_type": "application/pdf",
            "sha256": pdf_sha,
            "size_bytes": len(pdf_bytes),
            "filename": "virus.pdf",
        },
    )
    assert s2.status_code == 201
    f2 = client.post(
        f"/api/v1/assets/{s2.json()['asset']['id']}/finalize",
        headers=headers,
        json={"scan_status": "INFECTED", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert f2.status_code == 422


def test_hybrid_rls_hides_system_public_raw_source_but_exposes_preview() -> None:
    client = _api_client()
    pdf_bytes = _make_pdf_bytes(num_pages=3)
    sha = hashlib.sha256(pdf_bytes).hexdigest()

    sys_admin_headers = _auth_headers(
        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
        roles=["PLATFORM_ADMIN"],
        entitlement_tier="TEACHER_PRO",
    )
    init = client.post(
        "/api/v1/assets/upload-sessions",
        headers=sys_admin_headers,
        json={
            "mime_type": "application/pdf",
            "sha256": sha,
            "size_bytes": len(pdf_bytes),
            "filename": "public-book.pdf",
            "visibility": "SYSTEM_PUBLIC",
            "requires_pro": True,
        },
    )
    assert init.status_code == 201
    raw_id = uuid.UUID(init.json()["asset"]["id"])
    fin = client.post(
        f"/api/v1/assets/{raw_id}/finalize",
        headers=sys_admin_headers,
        json={"scan_status": "CLEAN", "content_base64": base64.b64encode(pdf_bytes).decode("ascii")},
    )
    assert fin.status_code == 200
    preview_id = uuid.UUID(fin.json()["preview_asset"]["id"])

    async def verify_rls() -> None:
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            t = Tenant(slug=f"rls-reader-{suffix}", name="RLS Reader")
            session.add(t)
            await session.flush()
            reader_tenant_id = t.id

        async with tenant_transaction(reader_tenant_id, role=DbRole.APP_RW) as session:
            raw_visible = (await session.execute(select(Asset).where(Asset.id == raw_id))).scalar_one_or_none()
            preview_visible = (
                await session.execute(select(Asset).where(Asset.id == preview_id))
            ).scalar_one_or_none()
            assert raw_visible is None  # hybrid_read_policy blocks SYSTEM_PUBLIC RAW_SOURCE
            assert preview_visible is not None  # PREVIEW_PDF is shared cleanly

    run(verify_rls())
