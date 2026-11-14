"""G01 config tables, plus the Sprint 1 tables of B03 assets and A01 ingestion, with RLS and grants

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (platform_settings,
feature_flags; assets; sources, source_documents, document_versions, acquisition_jobs,
pipeline_stage_runs, dead_letter_items), policies from section 2 and privileges from section 5.
Constraint and index names follow the ORM models so that `alembic check` stays clean.

platform_settings and feature_flags are global (no tenant_id, see app.platform.rls.GLOBAL_TABLES):
the API role reads and writes them, only platform administrators reach those routes.

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import hybrid_read_root, hybrid_write_rls, strict_tenant_rls

revision = "d1e2f3a4b5c6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

STRICT_TABLES = (
    "sources",
    "source_documents",
    "document_versions",
    "acquisition_jobs",
    "pipeline_stage_runs",
    "dead_letter_items",
)
# The worker writes the acquisition backbone, appends to assets and the DLQ, never touches answers.
INGESTION_WORKER_RW = ("sources", "source_documents", "document_versions", "acquisition_jobs", "pipeline_stage_runs")


def upgrade() -> None:
    # --- G01 -------------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE platform_settings (
            key VARCHAR(100) PRIMARY KEY,
            value_json JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE feature_flags (
            key VARCHAR(100) PRIMARY KEY,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            tenant_whitelist UUID[] NOT NULL DEFAULT '{}',
            config_json JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("REVOKE ALL ON platform_settings, feature_flags FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON platform_settings, feature_flags TO ulp_app_rw")

    # --- B03 assets ------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE assets (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            parent_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
            asset_role VARCHAR(40) NOT NULL,
            object_key VARCHAR(512) UNIQUE NOT NULL,
            mime_type VARCHAR(120) NOT NULL,
            sha256 CHAR(64) NOT NULL,
            size_bytes BIGINT NOT NULL,
            -- NOT NULL: never null since asset_role is NOT NULL; matches the ORM model (alembic check)
            is_immutable_bronze BOOLEAN NOT NULL GENERATED ALWAYS AS (asset_role = 'RAW_SOURCE') STORED,
            metadata_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_assets_asset_role CHECK (asset_role IN (
                'RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF', 'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL'
            )),
            CONSTRAINT ck_assets_size_bytes_non_negative CHECK (size_bytes >= 0),
            CONSTRAINT uq_assets_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_assets_tenant_sha256 ON assets (tenant_id, sha256)")

    # --- A01 ingestion ---------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sources (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_type VARCHAR(40) NOT NULL,
            name VARCHAR(255) NOT NULL,
            base_uri VARCHAR(512),
            adapter_key VARCHAR(80),
            config_json JSONB NOT NULL DEFAULT '{}',
            rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW',
            attribution_text VARCHAR(500),
            policy_status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_sources_source_type
                CHECK (source_type IN ('MANUAL_UPLOAD', 'GOOGLE_DRIVE', 'WEB_CRAWLER', 'PARTNER_API')),
            CONSTRAINT ck_sources_rights_status CHECK (rights_status IN (
                'PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN'
            )),
            CONSTRAINT ck_sources_policy_status
                CHECK (policy_status IN ('ACTIVE', 'PAUSED_DRIFT', 'PAUSED_MANUAL', 'REVOKED')),
            CONSTRAINT uq_sources_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_documents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            external_id VARCHAR(255) NOT NULL,
            title VARCHAR(500),
            mime_type VARCHAR(120) NOT NULL,
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'INGESTED',
            rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW',
            inferred_metadata_json JSONB NOT NULL DEFAULT '{}',
            paired_solution_doc_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_source_documents_acquisition_status
                CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
            CONSTRAINT ck_source_documents_rights_status CHECK (rights_status IN (
                'PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN'
            )),
            CONSTRAINT uq_source_documents_source_external UNIQUE (source_id, external_id),
            CONSTRAINT uq_source_documents_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_source_documents_source_tenant
                FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE document_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_document_id UUID NOT NULL,
            version_no INT NOT NULL,
            raw_asset_id UUID NOT NULL,
            checksum_sha256 CHAR(64) NOT NULL,
            page_count INT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_document_versions_doc_version UNIQUE (source_document_id, version_no),
            CONSTRAINT uq_document_versions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_document_versions_source_doc_tenant
                FOREIGN KEY (source_document_id, tenant_id)
                REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_document_versions_raw_asset_tenant
                FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE acquisition_jobs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_id UUID,
            document_version_id UUID,
            job_type VARCHAR(40) NOT NULL,
            job_status VARCHAR(30) NOT NULL DEFAULT 'QUEUED',
            current_stage VARCHAR(50),
            idempotency_key VARCHAR(128) UNIQUE NOT NULL,
            attempt_count INT NOT NULL DEFAULT 0,
            metrics_json JSONB NOT NULL DEFAULT '{}',
            last_error_code VARCHAR(80),
            last_error_detail TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_acquisition_jobs_job_type
                CHECK (job_type IN ('EXAM_IMPORT', 'DRIVE_SYNC', 'WEB_CRAWL', 'REPROCESS_EXTRACTION')),
            CONSTRAINT ck_acquisition_jobs_job_status CHECK (job_status IN (
                'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'DEAD_LETTER', 'CANCELLED'
            )),
            CONSTRAINT uq_acquisition_jobs_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_acquisition_jobs_source_tenant
                FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id),
            CONSTRAINT fk_acquisition_jobs_doc_version_tenant
                FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_acquisition_jobs_status_created ON acquisition_jobs (job_status, created_at)")
    op.execute(
        """
        CREATE TABLE pipeline_stage_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            acquisition_job_id UUID NOT NULL,
            stage_name VARCHAR(50) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'RUNNING',
            worker_version VARCHAR(40),
            input_ref VARCHAR(512),
            output_ref VARCHAR(512),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            CONSTRAINT ck_pipeline_stage_runs_status CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')),
            CONSTRAINT fk_pipeline_stage_runs_job_tenant
                FOREIGN KEY (acquisition_job_id, tenant_id)
                REFERENCES acquisition_jobs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE dead_letter_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            acquisition_job_id UUID,
            source_queue VARCHAR(80) NOT NULL,
            payload_json JSONB NOT NULL,
            error_trace TEXT NOT NULL,
            replayed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    # --- row-level security (section 2): assets is shared material, the rest strict ----------
    statements = [
        *hybrid_write_rls("assets"),
        # System assets are public except the bronze originals (spec section 2a).
        *hybrid_read_root("assets", "asset_role <> 'RAW_SOURCE'"),
    ]
    for table in STRICT_TABLES:
        statements += strict_tenant_rls(table)
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ------------------------------------------------------------
    all_tables = ("assets", *STRICT_TABLES)
    op.execute(f"REVOKE ALL ON {', '.join(all_tables)} FROM PUBLIC")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(all_tables)} TO ulp_app_rw")
    op.execute("GRANT SELECT ON assets TO ulp_exam_session_rw")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(INGESTION_WORKER_RW)} TO ulp_worker_ingestion")
    op.execute("GRANT SELECT, INSERT ON assets, dead_letter_items TO ulp_worker_ingestion")


def downgrade() -> None:
    for table in (
        "dead_letter_items",
        "pipeline_stage_runs",
        "acquisition_jobs",
        "document_versions",
        "source_documents",
        "sources",
        "assets",
        "feature_flags",
        "platform_settings",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
