"""A05 parsers / A06 layout: parser_definitions, extraction_runs, extracted_pages, layout_regions

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (acquisition group).
* parser_definitions is a global registry (no tenant, see app.platform.rls.GLOBAL_TABLES): the API role and the
  ingestion worker only read it. The four strategies the A05 router knows are seeded here (the service used to
  insert them lazily, which would have needed a write privilege on a global table for every tenant request).
* extraction_runs, extracted_pages, layout_regions are strict tenant tables. The API role writes them and, because
  a reprocess replaces the pages and regions of a run, also deletes extracted_pages and layout_regions; the
  ingestion worker reads, inserts and updates (section 5d), never deletes.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-11-15
"""

import json

from alembic import op
from app.platform.rls import strict_tenant_rls

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SEED = (
    ("NATIVE_DOCX_XML", "docx-ooxml-v1", [DOCX], {"max_document_xml_bytes": 25 * 1024 * 1024}),
    ("LIBREOFFICE_HEADLESS", "libreoffice-24.2", ["application/msword", DOCX], {"isolated_user_installation": True, "timeout_seconds": 60}),
    ("PDF_ENGINE", "pdfium-pdfplumber-v1", ["application/pdf"], {"min_chars_per_page": 40}),
    ("OCR_PIPELINE", "ocr-layout-v1", ["application/pdf", "image/png", "image/jpeg", "image/webp"], {"dpi": 300}),
)  # fmt: skip
STRICT_TABLES = ("extraction_runs", "extracted_pages", "layout_regions")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE parser_definitions (
            code VARCHAR(60) PRIMARY KEY,
            version VARCHAR(40) NOT NULL,
            supported_mimes VARCHAR(120)[] NOT NULL,
            config_schema_json JSONB NOT NULL DEFAULT '{}',
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    for code, version, mimes, config in SEED:
        array = "ARRAY[" + ", ".join(f"'{m}'" for m in mimes) + "]::varchar[]"
        op.execute(
            f"INSERT INTO parser_definitions (code, version, supported_mimes, config_schema_json) "
            f"VALUES ('{code}', '{version}', {array}, '{json.dumps(config)}'::jsonb) ON CONFLICT (code) DO NOTHING"
        )
    op.execute(
        """
        CREATE TABLE extraction_runs (
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
            CONSTRAINT uq_extraction_runs_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_extraction_runs_doc_version_tenant
                FOREIGN KEY (document_version_id, tenant_id)
                REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_extraction_runs_job_tenant
                FOREIGN KEY (acquisition_job_id, tenant_id) REFERENCES acquisition_jobs(id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE extracted_pages (
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
            CONSTRAINT uq_extracted_pages_run_page_no UNIQUE (extraction_run_id, page_no),
            CONSTRAINT uq_extracted_pages_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_extracted_pages_extraction_run_tenant
                FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE layout_regions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extracted_page_id UUID NOT NULL,
            region_type VARCHAR(40) NOT NULL,
            bbox_json JSONB NOT NULL,
            cropped_asset_id UUID,
            extracted_text TEXT,
            CONSTRAINT ck_layout_regions_region_type CHECK (
                region_type IN ('HEADER', 'FOOTER', 'QUESTION_BLOCK', 'FIGURE', 'TABLE', 'FORMULA_BLOCK')
            ),
            CONSTRAINT fk_layout_regions_extracted_page_tenant
                FOREIGN KEY (extracted_page_id, tenant_id)
                REFERENCES extracted_pages(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    for table in STRICT_TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute("REVOKE ALL ON parser_definitions, extraction_runs, extracted_pages, layout_regions FROM PUBLIC")
    op.execute("GRANT SELECT ON parser_definitions TO ulp_app_rw, ulp_worker_ingestion")
    op.execute("GRANT SELECT, INSERT, UPDATE ON extraction_runs TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON extracted_pages, layout_regions TO ulp_app_rw")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON extraction_runs, extracted_pages, layout_regions TO ulp_worker_ingestion"
    )


def downgrade() -> None:
    for table in ("layout_regions", "extracted_pages", "extraction_runs", "parser_definitions"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
