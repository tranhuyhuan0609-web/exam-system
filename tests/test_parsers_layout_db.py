"""Integration tests for A05 parsers & A06 layout DB models, services, RLS, and routers."""

from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.infrastructure.storage import get_bronze_storage
from app.modules.layout.internal.models import ExtractedPage, LayoutRegion  # noqa: F401
from app.modules.layout.router import router as layout_router
from app.modules.outbox.internal.models import OutboxEvent
from app.modules.parsers.internal.models import ExtractionRun, ParserDefinition  # noqa: F401
from app.modules.parsers.router import router as parsers_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction
from tests.pdf_fixtures import A4, PdfSketch, scanned_pdf


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(parsers_router)
    test_app.include_router(layout_router)
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


def _make_docx_bytes(xml_body: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types></Types>')
        zf.writestr("word/document.xml", xml_body)
    return buf.getvalue()


def _make_multipage_exam_pdf() -> bytes:
    sketch = PdfSketch()
    for idx in range(3):
        page = sketch.new_page(*A4)
        # Repeating header near top (within 0.065 * 842 ~= 54.7 pt)
        page.text(50, 25, f"De Thi Thu THPTQG - Trang {idx + 1}", size=10)
        # Repeating footer near bottom (within 0.935 * 842 ~= 787 pt)
        page.text(50, 822, f"H2Edu - Ma de 101 - Trang {idx + 1}", size=10)
        # Distinct body question text
        question_text = (
            f"Cau {idx + 1}. Cho ham so y = x^3 - 3x^2 + 2 co bao nhieu diem cuc tri?\n"
            "A. 1   B. 2   C. 3   D. 0"
        )
        page.lines(60, 120, question_text, size=11)
        # Two overlapping/close vector rectangles representing ONE clustered diagram (>= 35x30 pt)
        page.rect(100, 300, 160, 355)
        page.rect(155, 315, 220, 370)
    return sketch.tobytes()


@pytest.fixture(scope="session", autouse=True)
def _ensure_parsers_layout_schema() -> None:
    """Create parent B03/A01 + A05/A06 tables, RLS policies, and grants idempotently."""
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
        CREATE TABLE IF NOT EXISTS parser_definitions (
            code VARCHAR(60) PRIMARY KEY,
            version VARCHAR(40) NOT NULL,
            supported_mimes VARCHAR(120)[] NOT NULL,
            config_schema_json JSONB NOT NULL DEFAULT '{}',
            is_active BOOLEAN NOT NULL DEFAULT TRUE
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
        """
        CREATE TABLE IF NOT EXISTS extraction_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            document_version_id UUID NOT NULL,
            acquisition_job_id UUID,
            profile_code VARCHAR(60) NOT NULL,
            parser_version VARCHAR(40) NOT NULL,
            ocr_engine_version VARCHAR(40),
            confidence_score NUMERIC(4,3),
            silver_artifact_key VARCHAR(512),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (document_version_id, tenant_id)
                REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
            FOREIGN KEY (acquisition_job_id, tenant_id)
                REFERENCES acquisition_jobs(id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS extracted_pages (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extraction_run_id UUID NOT NULL,
            page_no INT NOT NULL,
            render_asset_id UUID,
            width_pt NUMERIC(8,2) NOT NULL,
            height_pt NUMERIC(8,2) NOT NULL,
            detected_header_y0 NUMERIC(8,2),
            detected_footer_y1 NUMERIC(8,2),
            raw_text TEXT,
            UNIQUE (extraction_run_id, page_no),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS layout_regions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extracted_page_id UUID NOT NULL,
            region_type VARCHAR(40) NOT NULL
                CHECK (region_type IN ('HEADER', 'FOOTER', 'QUESTION_BLOCK', 'FIGURE', 'TABLE', 'FORMULA_BLOCK')),
            bbox_json JSONB NOT NULL,
            cropped_asset_id UUID,
            extracted_text TEXT,
            FOREIGN KEY (extracted_page_id, tenant_id)
                REFERENCES extracted_pages(id, tenant_id) ON DELETE CASCADE
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
        "extraction_runs",
        "extracted_pages",
        "layout_regions",
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
            await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON parser_definitions TO ulp_app_rw"))
            await conn.execute(text("GRANT SELECT ON parser_definitions TO ulp_worker_ingestion"))

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
                    "GRANT SELECT, INSERT, UPDATE ON sources, source_documents, document_versions, "
                    "acquisition_jobs, pipeline_stage_runs, extraction_runs, extracted_pages, layout_regions "
                    "TO ulp_worker_ingestion"
                )
            )
            await conn.execute(text("GRANT SELECT, INSERT ON outbox_events TO ulp_app_rw"))
            await conn.execute(text("GRANT INSERT ON outbox_events TO ulp_worker_ingestion"))

    run(create())


def _create_tenant(slug_prefix: str = "parser-tenant") -> uuid.UUID:
    async def _go() -> uuid.UUID:
        async with SessionLocal() as session:
            async with session.begin():
                t = Tenant(
                    id=uuid.uuid4(),
                    slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
                    name="Parser Layout Test Tenant",
                    status="ACTIVE",
                )
                session.add(t)
                await session.flush()
                return t.id

    return run(_go())


def test_docx_25mb_xml_limit_returns_413() -> None:
    tenant_id = _create_tenant("xmlcap")
    client = _api_client()

    # Valid DOCX archive whose word/document.xml exceeds 25 MB (25 * 1024 * 1024)
    huge_payload = b"<w:document>" + (b"a" * (25 * 1024 * 1024 + 128)) + b"</w:document>"
    docx_bytes = _make_docx_bytes(huge_payload)

    resp = client.post(
        "/api/v1/documents",
        headers=_auth_headers(tenant_id=tenant_id),
        files={
            "file": (
                "huge.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 413


def test_docx_clean_vs_mathtype_routing_and_versions_list() -> None:
    tenant_id = _create_tenant("docxroute")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id)

    clean_docx = _make_docx_bytes(
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>Cau 1</w:t></w:r></w:p></w:body></w:document>"
    )
    resp1 = client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "filename": "de_toan.docx",
            "content_base64": base64.b64encode(clean_docx).decode("ascii"),
            "external_id": "shared-docx-item",
            "title": "De Toan v1",
        },
    )
    assert resp1.status_code == 201, resp1.text
    d1 = resp1.json()
    assert d1["routing_strategy"] == "NATIVE_DOCX_XML"
    assert d1["version_no"] == 1

    # Version 2 on the same logical document with MathType <w:object> tag -> LIBREOFFICE_HEADLESS
    mathtype_docx = _make_docx_bytes(
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:object/></w:p></w:body></w:document>"
    )
    resp2 = client.post(
        "/api/v1/documents",
        headers=headers,
        json={
            "filename": "de_toan_v2.docx",
            "content_base64": base64.b64encode(mathtype_docx).decode("ascii"),
            "source_id": d1["source_id"],
            "external_id": "shared-docx-item",
            "title": "De Toan v2 MathType",
        },
    )
    assert resp2.status_code == 201, resp2.text
    d2 = resp2.json()
    assert d2["source_document_id"] == d1["source_document_id"]
    assert d2["version_no"] == 2
    assert d2["routing_strategy"] == "LIBREOFFICE_HEADLESS"
    assert "<w:object" in d2["detected_markers"]

    # GET /api/v1/documents/{id}/versions returns both immutable versions in order
    ver_resp = client.get(f"/api/v1/documents/{d1['source_document_id']}/versions", headers=headers)
    assert ver_resp.status_code == 200
    versions = ver_resp.json()["versions"]
    assert [v["version_no"] for v in versions] == [1, 2]

    # POST /api/v1/documents/{id}/extract defaults to latest version (v2 -> LIBREOFFICE_HEADLESS)
    ext_resp = client.post(f"/api/v1/documents/{d1['source_document_id']}/extract", headers=headers)
    assert ext_resp.status_code == 201, ext_resp.text
    run_body = ext_resp.json()
    assert run_body["document_version_id"] == d2["document_version_id"]
    assert run_body["profile_code"] == "LIBREOFFICE_HEADLESS"

    # Explicit v1 extract routes to NATIVE_DOCX_XML
    ext_v1 = client.post(
        f"/api/v1/documents/{d1['source_document_id']}/extract",
        headers=headers,
        json={"document_version_id": d1["document_version_id"]},
    )
    assert ext_v1.status_code == 201
    assert ext_v1.json()["profile_code"] == "NATIVE_DOCX_XML"


def test_scanned_pdf_routes_to_ocr_and_multipage_layout_segments_figures_margins() -> None:
    tenant_id = _create_tenant("layout")
    client = _api_client()
    headers = _auth_headers(tenant_id=tenant_id)

    # 1. Scanned PDF (via tests.pdf_fixtures.scanned_pdf) -> OCR_PIPELINE
    scan_bytes = scanned_pdf(pages=[["De scan trang 1"], ["De scan trang 2"]])
    scan_up = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("scan.pdf", scan_bytes, "application/pdf")},
    )
    assert scan_up.status_code == 201
    assert scan_up.json()["routing_strategy"] == "OCR_PIPELINE"

    # 2. Text-rich 3-page PDF with repeating margins & vector figure
    exam_pdf = _make_multipage_exam_pdf()
    exam_up = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("exam_3p.pdf", exam_pdf, "application/pdf")},
    )
    assert exam_up.status_code == 201, exam_up.text
    exam_doc = exam_up.json()
    assert exam_doc["routing_strategy"] == "PDF_ENGINE"
    assert exam_doc["page_count"] == 3

    ext_resp = client.post(
        f"/api/v1/documents/{exam_doc['source_document_id']}/extract",
        headers=headers,
    )
    assert ext_resp.status_code == 201
    run_id = ext_resp.json()["id"]

    # Run A06 layout segmentation job
    ocr_resp = client.post(
        "/api/v1/ocr/jobs",
        headers=headers,
        json={"extraction_run_id": run_id, "crop_figures": True},
    )
    assert ocr_resp.status_code == 201, ocr_resp.text
    job = ocr_resp.json()
    assert job["job_id"] == run_id
    assert len(job["pages"]) == 3

    first_page = job["pages"][0]
    assert first_page["page_no"] == 1
    assert first_page["detected_header_y0"] is not None
    assert first_page["detected_footer_y1"] is not None
    region_types = {r["region_type"] for r in first_page["regions"]}
    assert {"HEADER", "FOOTER", "FIGURE", "QUESTION_BLOCK"}.issubset(region_types)

    # Two overlapping vector rects on each page merged into ONE clustered FIGURE region + cropped PNG asset
    figure_regions = [r for r in first_page["regions"] if r["region_type"] == "FIGURE"]
    assert len(figure_regions) == 1
    assert figure_regions[0]["cropped_asset_id"] is not None
    bbox = figure_regions[0]["bbox_json"]
    assert bbox["x0"] <= 100.5 and bbox["x1"] >= 219.5

    # Verify cropped figure PNG asset exists in Bronze/LocalStorage
    async def _verify_crop_exists() -> tuple[str, int]:
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            row = (
                await session.execute(
                    text("SELECT object_key FROM assets WHERE id = :aid"),
                    {"aid": uuid.UUID(figure_regions[0]["cropped_asset_id"])},
                )
            ).scalar_one()
            events = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == uuid.UUID(run_id),
                        OutboxEvent.event_type == "layout.ocr_job_completed",
                    )
                )
            ).scalars().all()
            return str(row), len(events)

    crop_key, outbox_count = run(_verify_crop_exists())
    assert run(get_bronze_storage().exists(crop_key)) is True
    assert outbox_count == 1

    # GET /api/v1/ocr/jobs/{id} retrieves persisted pages & regions
    get_job = client.get(f"/api/v1/ocr/jobs/{run_id}", headers=headers)
    assert get_job.status_code == 200
    assert len(get_job.json()["pages"]) == 3
    p1_orig_ids = {r["id"] for r in get_job.json()["pages"][0]["regions"]}
    p2_orig_ids = {r["id"] for r in get_job.json()["pages"][1]["regions"]}

    # Single-page OCR reprocess updates ONLY page 2 region IDs, leaving page 1 untouched
    re_page = client.post(
        f"/api/v1/ocr/jobs/{run_id}/pages/2/reprocess",
        headers=headers,
    )
    assert re_page.status_code == 200, re_page.text
    assert re_page.json()["page_no"] == 2
    after_get = client.get(f"/api/v1/ocr/jobs/{run_id}", headers=headers).json()
    p1_after_ids = {r["id"] for r in after_get["pages"][0]["regions"]}
    p2_after_ids = {r["id"] for r in after_get["pages"][1]["regions"]}
    assert p1_after_ids == p1_orig_ids
    assert p2_after_ids != p2_orig_ids

    # Invalid page number rejected with 422
    bad_page = client.post(f"/api/v1/ocr/jobs/{run_id}/pages/99/reprocess", headers=headers)
    assert bad_page.status_code == 422

    # Page-scoped ExtractionRun reprocess creates new versioned run referencing parent_run_id
    re_run = client.post(
        f"/api/v1/extraction-runs/{run_id}/reprocess",
        headers=headers,
        json={"pages": [3, 2, 2]},
    )
    assert re_run.status_code == 201, re_run.text
    re_body = re_run.json()
    assert re_body["id"] != run_id
    assert re_body["parent_run_id"] == run_id
    assert re_body["target_pages"] == [2, 3]


def test_auth_anti_spoofing_and_cross_tenant_rls_isolation() -> None:
    tenant_a = _create_tenant("rls-a")
    tenant_b = _create_tenant("rls-b")
    client = _api_client()
    headers_a = _auth_headers(tenant_id=tenant_a)
    headers_b = _auth_headers(tenant_id=tenant_b)

    # Missing Bearer token -> 401
    unauth = client.post("/api/v1/ocr/jobs", json={"extraction_run_id": str(uuid.uuid4())})
    assert unauth.status_code == 401

    # X-Tenant-Id spoofing -> 403
    spoof = client.get(
        f"/api/v1/documents/{uuid.uuid4()}/versions",
        headers={**headers_a, "X-Tenant-Id": str(tenant_b)},
    )
    assert spoof.status_code == 403

    # Tenant A uploads PDF, extracts, and segments layout
    up = client.post(
        "/api/v1/documents",
        headers=headers_a,
        files={"file": ("rls_exam.pdf", _make_multipage_exam_pdf(), "application/pdf")},
    )
    assert up.status_code == 201
    doc_id = up.json()["source_document_id"]
    run_id = client.post(f"/api/v1/documents/{doc_id}/extract", headers=headers_a).json()["id"]
    assert (
        client.post(
            "/api/v1/ocr/jobs",
            headers=headers_a,
            json={"extraction_run_id": run_id, "crop_figures": False},
        ).status_code
        == 201
    )

    # Tenant B receives strict 404 across all resource endpoints via PostgreSQL RLS
    assert client.get(f"/api/v1/documents/{doc_id}/versions", headers=headers_b).status_code == 404
    assert client.post(f"/api/v1/documents/{doc_id}/extract", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/api/v1/extraction-runs/{run_id}/reprocess",
            headers=headers_b,
            json={"pages": [1]},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/ocr/jobs",
            headers=headers_b,
            json={"extraction_run_id": run_id, "crop_figures": False},
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/ocr/jobs/{run_id}", headers=headers_b).status_code == 404
    assert (
        client.post(f"/api/v1/ocr/jobs/{run_id}/pages/1/reprocess", headers=headers_b).status_code
        == 404
    )
