"""PostgreSQL + RLS + FastAPI integration tests for TASK-32 (Sprint 10):
- A02 crawler (`crawl_urls`, `crawl_fetches`, SSRF, robots, politeness, auto-pause PAUSED_DRIFT, WORKER_INGESTION + RLS)
- A03 drive_sync (`source_connections`, `source_sync_cursors`, `file_pairing_candidates`, least-privilege scopes,
  delta rename/move without re-OCR, zip bomb/traversal rejection, 4-signal pairing confirm, revoke preserves docs, RLS)
- E03 generation (`generation_requests`, grounded anchors, AI_GENERATED + C02 -> A09 queue, 0% direct publish, RLS)
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import time
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.contracts.enums import PublicationStatus, QuestionItemType, RightsStatus
from app.db import SessionLocal, engine
from app.modules.crawler.internal.models import CrawlFetch, CrawlUrl  # noqa: F401
from app.modules.crawler.public import record_crawl_fetch_and_evaluate_drift
from app.modules.crawler.router import router as crawler_router
from app.modules.drive_sync.internal.models import (  # noqa: F401
    FilePairingCandidate,
    SourceConnection,
    SourceSyncCursor,
)
from app.modules.drive_sync.public import (
    DriveFileChange,
    DriveSyncPolicyError,
    sync_drive_connection,
)
from app.modules.drive_sync.router import router as drive_sync_router
from app.modules.generation.internal.models import GenerationRequestRecord  # noqa: F401
from app.modules.generation.public import (
    AnchorInput,
    GenerationPolicyViolationError,
    create_ai_generation,
)
from app.modules.generation.router import router as generation_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.quality.public import AnswerCheck
from app.modules.taxonomy.internal.models import TaxonomyNode
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(crawler_router)
    test_app.include_router(drive_sync_router)
    test_app.include_router(generation_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["ADMIN"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture(scope="module", autouse=True)
def _ensure_task32_schema(_database: None):
    """Create TASK-32 tables, RLS policies and grants locally; DROP CASCADE on teardown."""
    ddl_statements = (
        """
        CREATE TABLE IF NOT EXISTS crawl_urls (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            url VARCHAR(1024) NOT NULL,
            url_hash CHAR(64) NOT NULL,
            etag VARCHAR(255),
            last_modified_header VARCHAR(120),
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'FETCHED', 'SKIPPED_304', 'FAILED', 'BLOCKED_ROBOTS')),
            last_fetched_at TIMESTAMPTZ,
            UNIQUE (source_id, url_hash),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS crawl_fetches (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            crawl_url_id UUID NOT NULL,
            http_status INT NOT NULL,
            raw_asset_id UUID,
            resolved_ip VARCHAR(64) NOT NULL,
            parse_yield_count INT NOT NULL DEFAULT 0,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (crawl_url_id, tenant_id) REFERENCES crawl_urls(id, tenant_id) ON DELETE CASCADE,
            FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS source_connections (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            provider VARCHAR(40) NOT NULL DEFAULT 'GOOGLE_DRIVE',
            secret_vault_ref VARCHAR(255) NOT NULL,
            granted_scopes VARCHAR(120)[] NOT NULL DEFAULT '{https://www.googleapis.com/auth/drive.file}',
            status VARCHAR(30) NOT NULL DEFAULT 'CONNECTED'
                CHECK (status IN ('CONNECTED', 'REAUTH_REQUIRED', 'REVOKED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS source_sync_cursors (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_connection_id UUID UNIQUE NOT NULL,
            cursor_token VARCHAR(512) NOT NULL,
            last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (source_connection_id, tenant_id)
                REFERENCES source_connections(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS file_pairing_candidates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            exam_doc_id UUID NOT NULL,
            solution_doc_id UUID NOT NULL,
            similarity_score NUMERIC(4,3) NOT NULL,
            signals_json JSONB NOT NULL DEFAULT '{}',
            status VARCHAR(30) NOT NULL DEFAULT 'PROPOSED'
                CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (exam_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
            FOREIGN KEY (solution_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS generation_requests (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            requested_by UUID NOT NULL REFERENCES users(id),
            taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
            target_difficulty VARCHAR(20) NOT NULL,
            source_anchors_json JSONB NOT NULL,
            generated_candidate_id UUID,
            status VARCHAR(30) NOT NULL DEFAULT 'DRAFT_CREATED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    )
    all_tables = (
        "crawl_urls",
        "crawl_fetches",
        "source_connections",
        "source_sync_cursors",
        "file_pairing_candidates",
        "generation_requests",
    )
    ingestion_rw_tables = (
        "crawl_urls",
        "crawl_fetches",
        "source_sync_cursors",
        "file_pairing_candidates",
    )

    async def create() -> None:
        async with engine.begin() as conn:
            for stmt in ddl_statements:
                await conn.execute(text(stmt))
            for table in all_tables:
                await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}"))
                await conn.execute(
                    text(
                        f"CREATE POLICY tenant_isolation_policy ON {table} "
                        "USING (tenant_id = (SELECT current_app_tenant_id())) "
                        "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                    )
                )
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ulp_app_rw"))
            for table in ingestion_rw_tables:
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO ulp_worker_ingestion"))

    async def drop() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DROP TABLE IF EXISTS generation_requests, file_pairing_candidates, "
                    "source_sync_cursors, source_connections, crawl_fetches, crawl_urls CASCADE"
                )
            )

    run(create())
    yield
    run(drop())


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Create two isolated tenants with staff users and memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        ua, ub = uuid.uuid4(), uuid.uuid4()
        ma, mb = uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t32-a-{ta.hex[:8]}", name="School 32A"),
                    Tenant(id=tb, slug=f"t32-b-{tb.hex[:8]}", name="School 32B"),
                    User(id=ua, email_blind_index=f"{ua.hex}{ua.hex}", pseudonym_handle="Staff32A"),
                    User(id=ub, email_blind_index=f"{ub.hex}{ub.hex}", pseudonym_handle="Staff32B"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=ma, tenant_id=ta, user_id=ua, roles=["ADMIN", "TEACHER", "REVIEWER"]),
                    Membership(id=mb, tenant_id=tb, user_id=ub, roles=["ADMIN", "TEACHER", "REVIEWER"]),
                ]
            )
        return {"ta": ta, "tb": tb, "ua": ua, "ub": ub, "ma": ma, "mb": mb}

    return run(create())


def test_pep562_lazy_public_exports_keep_core_imports_db_free() -> None:
    """Pure Core imports from crawler/drive_sync/generation public.py must not load ORM models."""
    code = (
        "import sys\n"
        "from app.modules.crawler.public import is_public_address, ParserDriftMonitor\n"
        "from app.modules.drive_sync.public import inspect_zip, SafeArchivePolicy\n"
        "from app.modules.generation.public import create_gap_fill_request, MatrixGapCell\n"
        "loaded = set(sys.modules)\n"
        "assert 'app.modules.crawler.internal.models' not in loaded\n"
        "assert 'app.modules.drive_sync.internal.models' not in loaded\n"
        "assert 'app.modules.generation.internal.models' not in loaded\n"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr


def test_crawler_ssrf_robots_drift_and_rls_end_to_end(world: dict[str, uuid.UUID]) -> None:
    """A02 crawler validates SSRF/robots, schedules URLs, pauses on >15% drift on 30 pages, and isolates tenants."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_a = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=world["mb"], user_id=world["ub"])

    created = client.post(
        "/api/v1/crawler/sources",
        headers=headers_a,
        json={
            "name": "Math Exam Portal",
            "base_uri": "https://exams.example.edu.vn/de-thi/",
            "allowed_domains": ["exams.example.edu.vn"],
            "allowed_path_prefixes": ["/de-thi"],
            "rights_status": "LICENSED",
        },
    )
    assert created.status_code == 201, created.text
    source_id = uuid.UUID(created.json()["id"])

    # Block private IPv4/loopback/metadata/IPv4-mapped IPv6 and robots disallowed paths
    for bad_ip in ("127.0.0.1", "169.254.169.254", "::ffff:127.0.0.1", "10.20.30.40"):
        resp = client.post(
            f"/api/v1/crawler/sources/{source_id}/validate",
            headers=headers_a,
            json={
                "urls": ["https://exams.example.edu.vn/de-thi/toan-12"],
                "dns_observations": {"exams.example.edu.vn": [bad_ip]},
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["policy_code"] == "SSRF_BLOCKED"

    # Block DNS rebinding on redirect hop
    rebinding = client.post(
        f"/api/v1/crawler/sources/{source_id}/validate",
        headers=headers_a,
        json={
            "urls": [
                "https://exams.example.edu.vn/de-thi/start",
                "https://exams.example.edu.vn/de-thi/internal-hop",
            ],
            "dns_observations": {"exams.example.edu.vn": ["192.168.1.50"]},
        },
    )
    assert rebinding.status_code == 403

    # Block robots.txt disallow
    robots_blocked = client.post(
        f"/api/v1/crawler/sources/{source_id}/validate",
        headers=headers_a,
        json={
            "urls": ["https://exams.example.edu.vn/de-thi/private/exam.pdf"],
            "dns_observations": {"exams.example.edu.vn": ["93.184.216.34"]},
            "robots_txt": {"exams.example.edu.vn": "User-agent: *\nDisallow: /de-thi/private/\n"},
        },
    )
    assert robots_blocked.status_code == 403
    assert robots_blocked.json()["policy_code"] == "ROBOTS_TXT_DISALLOWED"

    # Valid target passes and can be queued into crawl_urls
    ok_validate = client.post(
        f"/api/v1/crawler/sources/{source_id}/validate",
        headers=headers_a,
        json={
            "urls": ["https://exams.example.edu.vn/de-thi/toan-2026"],
            "dns_observations": {"exams.example.edu.vn": ["93.184.216.34"]},
            "robots_txt": {"exams.example.edu.vn": "User-agent: *\nAllow: /de-thi/\n"},
        },
    )
    assert ok_validate.status_code == 200
    assert ok_validate.json()["hops"][0]["pinned_ip"] == "93.184.216.34"

    job_resp = client.post(
        f"/api/v1/crawler/sources/{source_id}/crawl-jobs",
        headers=headers_a,
        json={
            "urls": ["https://exams.example.edu.vn/de-thi/toan-2026"],
            "dns_observations": {"exams.example.edu.vn": ["93.184.216.34"]},
        },
    )
    assert job_resp.status_code == 201, job_resp.text
    assert job_resp.json()["enqueued_count"] == 1

    # Cross-tenant validate must return 404 under Tenant B RLS
    cross_tenant = client.post(
        f"/api/v1/crawler/sources/{source_id}/validate",
        headers=headers_b,
        json={
            "urls": ["https://exams.example.edu.vn/de-thi/toan-2026"],
            "dns_observations": {"exams.example.edu.vn": ["93.184.216.34"]},
        },
    )
    assert cross_tenant.status_code == 404

    # Simulate 30 fetches under ulp_worker_ingestion: 25 non-empty + 5 empty (>15%) -> auto PAUSED_DRIFT
    async def simulate_fetches_and_verify_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.WORKER_INGESTION) as session:
            last_outcome = None
            for idx in range(30):
                yield_count = 0 if idx >= 25 else 3
                last_outcome = await record_crawl_fetch_and_evaluate_drift(
                    session,
                    tenant_id=ta,
                    source_id=source_id,
                    url=f"https://exams.example.edu.vn/de-thi/page-{idx}",
                    http_status=200,
                    resolved_ip="93.184.216.34",
                    parse_yield_count=yield_count,
                    dns_resolver=lambda _h: ("93.184.216.34",),
                )
            assert last_outcome is not None
            assert last_outcome.drift_status.is_drifted is True
            assert last_outcome.source_policy_status == "PAUSED_DRIFT"

        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            drift_events = (
                await session_a.execute(
                    text(
                        "SELECT event_type FROM outbox_events "
                        "WHERE tenant_id = :tid AND aggregate_id = :sid"
                    ),
                    {"tid": ta, "sid": source_id},
                )
            ).scalars().all()
            assert "crawl.parser.drift_detected" in drift_events

        async with tenant_transaction(tb, role=DbRole.APP_RW) as session_b:
            visible_urls = (await session_b.execute(select(CrawlUrl))).scalars().all()
            visible_fetches = (await session_b.execute(select(CrawlFetch))).scalars().all()
            assert visible_urls == []
            assert visible_fetches == []

    run(simulate_fetches_and_verify_rls())

    # Manual pause/status toggle still works cleanly
    paused = client.post(
        f"/api/v1/crawler/sources/{source_id}/pause",
        headers=headers_a,
        json={"policy_status": "PAUSED_MANUAL"},
    )
    assert paused.status_code == 200
    assert paused.json()["policy_status"] == "PAUSED_MANUAL"


def test_drive_sync_scopes_delta_safe_archive_pairing_revoke_and_rls(
    world: dict[str, uuid.UUID],
    tmp_path: Path,
) -> None:
    """A03 Drive sync enforces minimal scopes, skips rename re-OCR, blocks unsafe zips, pairs & keeps docs on revoke."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_a = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=world["mb"], user_id=world["ub"])

    # Reject broad full-drive read scopes
    broad = client.post(
        "/api/v1/integrations/google-drive/connect",
        headers=headers_a,
        json={
            "name": "Broad Drive",
            "secret_vault_ref": "vault://drive/t32a/broad",
            "granted_scopes": ["https://www.googleapis.com/auth/drive.readonly"],
            "rights_status": "OWNED",
        },
    )
    assert broad.status_code == 403
    assert broad.json()["policy_code"] == "DRIVE_SCOPE_TOO_BROAD"

    # Accept minimal drive.file scope
    connected = client.post(
        "/api/v1/integrations/google-drive/connect",
        headers=headers_a,
        json={
            "name": "Department Exam Folder",
            "secret_vault_ref": "vault://drive/t32a/picker-conn",
            "granted_scopes": ["https://www.googleapis.com/auth/drive.file"],
            "rights_status": "OWNED",
            "initial_cursor_token": "tok-0",
        },
    )
    assert connected.status_code == 201, connected.text
    conn_id = uuid.UUID(connected.json()["id"])

    exam_sha = hashlib.sha256(b"exam-content-v1").hexdigest()
    sol_sha = hashlib.sha256(b"solution-content-v1").hexdigest()

    # Initial sync discovers exam + solution files, emits outbox events, creates pairing candidate
    sync1 = client.post(
        f"/api/v1/drive/connections/{conn_id}/sync",
        headers=headers_a,
        json={
            "next_cursor_token": "tok-1",
            "changes": [
                {
                    "drive_file_id": "gd-exam-01",
                    "title": "De thi thu Toan THPT 2026 So Ha Noi.pdf",
                    "mime_type": "application/pdf",
                    "sha256": exam_sha,
                    "size_bytes": 1024,
                    "object_key": "bronze/t32a/de-toan-2026.pdf",
                    "folder_id": "folder-toan-12",
                    "folder_path": "/Toan12/ThiThu",
                    "subject_code": "MATH",
                    "question_count": 22,
                },
                {
                    "drive_file_id": "gd-sol-01",
                    "title": "HDC Toan THPT 2026 So Ha Noi.pdf",
                    "mime_type": "application/pdf",
                    "sha256": sol_sha,
                    "size_bytes": 512,
                    "object_key": "bronze/t32a/hdc-toan-2026.pdf",
                    "folder_id": "folder-toan-12",
                    "folder_path": "/Toan12/ThiThu",
                    "subject_code": "MATH",
                    "question_count": 22,
                },
            ],
        },
    )
    assert sync1.status_code == 201, sync1.text
    job1 = sync1.json()
    assert job1["ingested_count"] == 2
    assert job1["renamed_or_moved_count"] == 0
    assert len(job1["pairings_created"]) == 1
    pairing_id = job1["pairings_created"][0]["id"]

    # Job status reachable by Tenant A and hidden from Tenant B
    assert client.get(f"/api/v1/drive/sync-jobs/{job1['job_id']}", headers=headers_a).status_code == 200
    assert client.get(f"/api/v1/drive/sync-jobs/{job1['job_id']}", headers=headers_b).status_code == 404

    # Explicit confirmation updates FilePairingCandidate + source_documents.paired_solution_doc_id
    confirmed = client.post(
        f"/api/v1/drive/pairings/{pairing_id}/confirm",
        headers=headers_a,
        json={"decision": "CONFIRMED"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    # Second sync: rename & move Exam file WITHOUT changing sha256 -> ZERO re-ingest / re-OCR
    sync2 = client.post(
        f"/api/v1/drive/connections/{conn_id}/sync",
        headers=headers_a,
        json={
            "next_cursor_token": "tok-2",
            "changes": [
                {
                    "drive_file_id": "gd-exam-01",
                    "title": "De thi thu Toan THPT 2026 So Ha Noi (Renamed).pdf",
                    "mime_type": "application/pdf",
                    "sha256": exam_sha,
                    "size_bytes": 1024,
                    "object_key": "bronze/t32a/de-toan-2026.pdf",
                    "folder_id": "folder-archive",
                    "folder_path": "/Toan12/Archive",
                }
            ],
        },
    )
    assert sync2.status_code == 201
    assert sync2.json()["ingested_count"] == 0
    assert sync2.json()["renamed_or_moved_count"] == 1

    # Verify DB state + unsafe zip rejection (path traversal & zip bomb) + RLS
    traversal_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal_zip, "w") as zf:
        zf.writestr("../escape.txt", b"boom")

    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.txt", b"\x00" * (2 * 1024 * 1024))

    async def verify_rename_zip_and_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            ver_count = (
                await session_a.execute(
                    text(
                        "SELECT count(*) FROM document_versions dv "
                        "JOIN source_documents sd ON sd.id = dv.source_document_id "
                        "WHERE sd.external_id = 'gd-exam-01' AND sd.tenant_id = :tid"
                    ),
                    {"tid": ta},
                )
            ).scalar_one()
            assert ver_count == 1

            outbox_count = (
                await session_a.execute(
                    text(
                        "SELECT count(*) FROM outbox_events "
                        "WHERE tenant_id = :tid AND event_type = 'drive.file.discovered'"
                    ),
                    {"tid": ta},
                )
            ).scalar_one()
            assert outbox_count == 2

        for bad_zip in (traversal_zip, bomb_zip):
            zip_sha = hashlib.sha256(bad_zip.read_bytes()).hexdigest()
            with pytest.raises(DriveSyncPolicyError) as exc_info:
                async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
                    await sync_drive_connection(
                        session_a,
                        tenant_id=ta,
                        connection_id=conn_id,
                        next_cursor_token="tok-bad-zip",
                        changes=[
                            DriveFileChange(
                                drive_file_id=f"gd-zip-{bad_zip.stem}",
                                title=bad_zip.name,
                                mime_type="application/zip",
                                sha256=zip_sha,
                                size_bytes=bad_zip.stat().st_size,
                                object_key=f"bronze/t32a/{bad_zip.name}",
                                local_archive_path=bad_zip,
                            )
                        ],
                    )
            assert exc_info.value.code == "ARCHIVE_SECURITY_VIOLATION"

        async with tenant_transaction(tb, role=DbRole.APP_RW) as session_b:
            assert (await session_b.execute(select(SourceConnection))).scalars().all() == []
            assert (await session_b.execute(select(SourceSyncCursor))).scalars().all() == []
            assert (await session_b.execute(select(FilePairingCandidate))).scalars().all() == []

    run(verify_rename_zip_and_rls())

    # Revoke stops new syncs but preserves already ingested source_documents
    revoked = client.post(f"/api/v1/drive/connections/{conn_id}/revoke", headers=headers_a)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    blocked_after_revoke = client.post(
        f"/api/v1/drive/connections/{conn_id}/sync",
        headers=headers_a,
        json={"next_cursor_token": "tok-3", "changes": []},
    )
    assert blocked_after_revoke.status_code == 403

    async def verify_docs_preserved() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            preserved = (
                await session_a.execute(
                    text("SELECT count(*) FROM source_documents WHERE tenant_id = :tid"),
                    {"tid": ta},
                )
            ).scalar_one()
            assert preserved >= 2

    run(verify_docs_preserved())


def test_generation_grounded_anchors_c02_to_a09_zero_direct_publish_and_rls(
    world: dict[str, uuid.UUID],
) -> None:
    """E03 requires grounded tenant/system anchors, routes C02->A09 before PUBLISH, and enforces tenant RLS."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    ua, ub = world["ua"], world["ub"]
    headers_a = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=ua)
    headers_b = _auth_headers(tenant_id=tb, membership_id=world["mb"], user_id=ub)

    async def seed_anchors_and_taxonomy() -> dict[str, uuid.UUID]:
        tax_a = uuid.uuid4()
        src_a, src_b = uuid.uuid4(), uuid.uuid4()
        doc_a_owned, doc_a_restricted, doc_b_owned = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        asset_a1, asset_a2, asset_b1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        dv_a_owned, dv_a_restricted, dv_b_owned = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        sha = hashlib.sha256(b"anchor-doc").hexdigest()

        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=tax_a,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="TOPIC",
                    code="ALG_EQ",
                    code_path="MATH.ALGEBRA.EQ",
                    is_active=True,
                )
            )
            await session.flush()
            for tid, sid in ((ta, src_a), (tb, src_b)):
                await session.execute(
                    text(
                        "INSERT INTO sources "
                        "(id, tenant_id, source_type, name, config_json, rights_status, policy_status) "
                        "VALUES (:sid, :tid, 'MANUAL_UPLOAD', 'Seed Source', '{}'::jsonb, 'OWNED', 'ACTIVE')"
                    ),
                    {"sid": sid, "tid": tid},
                )
            for tid, aid in ((ta, asset_a1), (ta, asset_a2), (tb, asset_b1)):
                await session.execute(
                    text(
                        "INSERT INTO assets (id, tenant_id, asset_role, object_key, mime_type, "
                        "sha256, size_bytes, metadata_json) "
                        "VALUES (:aid, :tid, 'RAW_SOURCE', :obj, 'application/pdf', :sha, 100, '{}'::jsonb)"
                    ),
                    {"aid": aid, "tid": tid, "obj": f"bronze/{aid}.pdf", "sha": sha},
                )
            for did, tid, sid, rights in (
                (doc_a_owned, ta, src_a, "OWNED"),
                (doc_a_restricted, ta, src_a, "RESTRICTED"),
                (doc_b_owned, tb, src_b, "OWNED"),
            ):
                await session.execute(
                    text(
                        "INSERT INTO source_documents (id, tenant_id, source_id, external_id, title, mime_type, "
                        "acquisition_status, rights_status, inferred_metadata_json) "
                        "VALUES (:did, :tid, :sid, :ext, 'Anchor PDF', "
                        "'application/pdf', 'INGESTED', :rights, '{}'::jsonb)"
                    ),
                    {"did": did, "tid": tid, "sid": sid, "ext": f"ext-{did}", "rights": rights},
                )
            for dvid, tid, did, aid in (
                (dv_a_owned, ta, doc_a_owned, asset_a1),
                (dv_a_restricted, ta, doc_a_restricted, asset_a2),
                (dv_b_owned, tb, doc_b_owned, asset_b1),
            ):
                await session.execute(
                    text(
                        "INSERT INTO document_versions "
                        "(id, tenant_id, source_document_id, version_no, raw_asset_id, checksum_sha256) "
                        "VALUES (:dvid, :tid, :did, 1, :aid, :sha)"
                    ),
                    {"dvid": dvid, "tid": tid, "did": did, "aid": aid, "sha": sha},
                )
        return {
            "tax_a": tax_a,
            "dv_a_owned": dv_a_owned,
            "dv_a_restricted": dv_a_restricted,
            "dv_b_owned": dv_b_owned,
        }

    seeded = run(seed_anchors_and_taxonomy())

    # 1) Block ungrounded (empty anchors), DRAFT anchor, RESTRICTED rights, foreign anchor, and direct publish bypass
    bad_payloads = [
        {
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "anchors": [],
            "draft_stem": "Giai phuong trinh 2x + 4 = 10",
        },
        {
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "anchors": [
                {
                    "anchor_id": "anc-draft",
                    "document_version_id": str(seeded["dv_a_owned"]),
                    "excerpt": "2x + 4 = 10",
                    "publication_status": "DRAFT",
                    "rights_status": "OWNED",
                    "owner_tenant_id": str(ta),
                }
            ],
            "draft_stem": "Giai phuong trinh 2x + 4 = 10",
        },
        {
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "anchors": [
                {
                    "anchor_id": "anc-restricted",
                    "document_version_id": str(seeded["dv_a_restricted"]),
                    "excerpt": "Copyrighted excerpt",
                    "publication_status": "PUBLISHED",
                    "rights_status": "OWNED",
                    "owner_tenant_id": str(ta),
                }
            ],
            "draft_stem": "Giai phuong trinh 2x + 4 = 10",
        },
        {
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "anchors": [
                {
                    "anchor_id": "anc-foreign",
                    "document_version_id": str(seeded["dv_b_owned"]),
                    "excerpt": "Foreign tenant doc",
                    "publication_status": "PUBLISHED",
                    "rights_status": "OWNED",
                }
            ],
            "draft_stem": "Giai phuong trinh 2x + 4 = 10",
        },
        {
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "anchors": [
                {
                    "anchor_id": "anc-ok",
                    "document_version_id": str(seeded["dv_a_owned"]),
                    "excerpt": "Phuong trinh bac nhat ax + b = 0",
                    "publication_status": "PUBLISHED",
                    "rights_status": "OWNED",
                    "owner_tenant_id": str(ta),
                }
            ],
            "draft_stem": "Giai phuong trinh 2x + 4 = 10",
            "requested_publication_status": "PUBLISHED",
        },
    ]
    for bad in bad_payloads:
        resp = client.post("/api/v1/ai/generations", headers=headers_a, json=bad)
        assert resp.status_code == 403, f"expected 403 for {bad}, got {resp.status_code}: {resp.text}"

    # 2) Without verifiable math solution -> C02 marks DISPUTED, queues A09 OPEN, blocks direct approval!
    disputed = client.post(
        "/api/v1/ai/generations",
        headers=headers_a,
        json={
            "taxonomy_node_id": str(seeded["tax_a"]),
            "target_difficulty": "VD",
            "question_type": "SHORT_NUMERIC",
            "anchors": [
                {
                    "anchor_id": "anc-1",
                    "document_version_id": str(seeded["dv_a_owned"]),
                    "excerpt": "Phuong trinh bac nhat ax + b = 0",
                    "publication_status": "PUBLISHED",
                    "rights_status": "OWNED",
                    "owner_tenant_id": str(ta),
                }
            ],
            "draft_stem": "Tim x biet 2x + 4 = 10",
        },
    )
    assert disputed.status_code == 201, disputed.text
    disputed_body = disputed.json()
    assert disputed_body["acquisition_method"] == "AI_GENERATED"
    assert disputed_body["verification_status"] == "DISPUTED"
    assert disputed_body["publication_status"] == "DRAFT"
    assert disputed_body["status"] == "QUEUED_FOR_REVIEW"
    assert disputed_body["review_task_id"] is not None

    # Disputed candidate cannot be promoted to PUBLISHED
    blocked_approve = client.post(
        f"/api/v1/ai/generations/{disputed_body['id']}/approve",
        headers=headers_a,
        json={"reason": "Attempt override"},
    )
    assert blocked_approve.status_code == 403

    # 3) Verified generation via C02 -> A09 queue -> human reviewer approval publishes question
    async def run_verified_flow_and_rls() -> uuid.UUID:
        def fake_sympy_ok(expr: str, source_ans: str, _tol: Decimal) -> AnswerCheck:
            return AnswerCheck(
                matches=(expr.strip() == source_ans.strip()),
                computed=expr,
                source=source_ans,
                abs_diff="0",
            )

        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            created_view = await create_ai_generation(
                session_a,
                tenant_id=ta,
                requested_by=ua,
                taxonomy_node_id=seeded["tax_a"],
                target_difficulty="VD",
                question_type=QuestionItemType.SHORT_NUMERIC,
                missing_count=2,
                prompt_version="e03_math_v1",
                anchors=[
                    AnchorInput(
                        anchor_id="anc-ok",
                        document_version_id=seeded["dv_a_owned"],
                        excerpt="Cho phuong trinh 3x - 9 = 0",
                        publication_status=PublicationStatus.PUBLISHED,
                        rights_status=RightsStatus.OWNED,
                        owner_tenant_id=ta,
                    )
                ],
                draft_stem="Giai phuong trinh 3x - 9 = 0",
                computed_expression="3",
                source_answer="3",
                verifier=fake_sympy_ok,
            )
            assert created_view.acquisition_method == "AI_GENERATED"
            assert created_view.verification_status == "AUTO_VERIFIED"
            assert created_view.publication_status == "DRAFT"
            assert created_view.status == "QUEUED_FOR_REVIEW"
            assert created_view.review_task_id is not None

            # Verify 0 direct-to-PUBLISHED rows exist before human approval
            q_cnt = (
                await session_a.execute(
                    text("SELECT count(*) FROM questions WHERE tenant_id = :tid"),
                    {"tid": ta},
                )
            ).scalar_one()
            assert q_cnt == 0

            with pytest.raises(GenerationPolicyViolationError):
                await create_ai_generation(
                    session_a,
                    tenant_id=ta,
                    requested_by=ua,
                    taxonomy_node_id=seeded["tax_a"],
                    target_difficulty="VD",
                    question_type=QuestionItemType.SHORT_NUMERIC,
                    missing_count=1,
                    prompt_version="e03_math_v1",
                    anchors=[
                        AnchorInput(
                            anchor_id="anc-ok",
                            document_version_id=seeded["dv_a_owned"],
                            excerpt="test",
                            owner_tenant_id=ta,
                        )
                    ],
                    draft_stem="Direct bypass attempt",
                    skip_review=True,
                )

        # Tenant B sees zero generation_requests under RLS
        async with tenant_transaction(tb, role=DbRole.APP_RW) as session_b:
            assert (await session_b.execute(select(GenerationRequestRecord))).scalars().all() == []

        return created_view.id

    verified_req_id = run(run_verified_flow_and_rls())

    # Cross-tenant GET returns 404
    assert client.get(f"/api/v1/ai/generations/{verified_req_id}", headers=headers_b).status_code == 404

    # Human reviewer approves verified AI candidate -> becomes PUBLISHED with A09 decision trail
    published = client.post(
        f"/api/v1/ai/generations/{verified_req_id}/approve",
        headers=headers_a,
        json={"reason": "Verified math solution by reviewer"},
    )
    assert published.status_code == 200, published.text
    pub_body = published.json()
    assert pub_body["acquisition_method"] == "AI_GENERATED"
    assert pub_body["verification_status"] == "VERIFIED"
    assert pub_body["publication_status"] == "PUBLISHED"
    assert pub_body["status"] == "APPROVED"
