"""A02 crawler, A03 drive sync, E03 generation: source_connections, source_sync_cursors, crawl_urls, crawl_fetches,
file_pairing_candidates, generation_requests

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables (section 2).
* source_connections: one OAuth / service-account connection of a source. It holds only `secret_vault_ref`, a pointer
  into the vault, never a token; the ingestion worker has no grant on it (section 5d), only the API role does.
* source_sync_cursors, crawl_urls, crawl_fetches, file_pairing_candidates: the ingestion worker reads, inserts and
  updates them (section 5d, grants_manifest.csv). A crawl fetch is a log row, and the API role only reads and appends.
* generation_requests: a teacher's request for an AI-drafted question; foreign keys to tenants, users and
  taxonomy_nodes as in the DDL (no composite tenant key there, the row carries its own tenant).

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None

TABLES = (
    "source_connections",
    "source_sync_cursors",
    "crawl_urls",
    "crawl_fetches",
    "file_pairing_candidates",
    "generation_requests",
)
WORKER_TABLES = ("source_sync_cursors", "crawl_urls", "crawl_fetches", "file_pairing_candidates")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_connections (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            provider VARCHAR(40) NOT NULL DEFAULT 'GOOGLE_DRIVE',
            secret_vault_ref VARCHAR(255) NOT NULL,
            granted_scopes VARCHAR(120)[] NOT NULL DEFAULT '{https://www.googleapis.com/auth/drive.file}',
            status VARCHAR(30) NOT NULL DEFAULT 'CONNECTED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_source_connections_status CHECK (status IN ('CONNECTED', 'REAUTH_REQUIRED', 'REVOKED')),
            CONSTRAINT uq_source_connections_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_source_connections_source_tenant
                FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_sync_cursors (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_connection_id UUID UNIQUE NOT NULL,
            cursor_token VARCHAR(512) NOT NULL,
            last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_source_sync_cursors_connection_tenant
                FOREIGN KEY (source_connection_id, tenant_id)
                REFERENCES source_connections(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE crawl_urls (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            url VARCHAR(1024) NOT NULL,
            url_hash CHAR(64) NOT NULL,
            etag VARCHAR(255),
            last_modified_header VARCHAR(120),
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            last_fetched_at TIMESTAMPTZ,
            CONSTRAINT ck_crawl_urls_status
                CHECK (status IN ('PENDING', 'FETCHED', 'SKIPPED_304', 'FAILED', 'BLOCKED_ROBOTS')),
            CONSTRAINT uq_crawl_urls_source_url_hash UNIQUE (source_id, url_hash),
            CONSTRAINT uq_crawl_urls_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_crawl_urls_source_tenant
                FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE crawl_fetches (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            crawl_url_id UUID NOT NULL,
            http_status INT NOT NULL,
            raw_asset_id UUID,
            resolved_ip VARCHAR(64) NOT NULL,
            parse_yield_count INT NOT NULL DEFAULT 0,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_crawl_fetches_crawl_url_tenant
                FOREIGN KEY (crawl_url_id, tenant_id) REFERENCES crawl_urls(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_crawl_fetches_raw_asset_tenant
                FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE file_pairing_candidates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            exam_doc_id UUID NOT NULL,
            solution_doc_id UUID NOT NULL,
            similarity_score NUMERIC(4,3) NOT NULL,
            signals_json JSONB NOT NULL DEFAULT '{}',
            status VARCHAR(30) NOT NULL DEFAULT 'PROPOSED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_file_pairing_candidates_status CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')),
            CONSTRAINT fk_file_pairing_candidates_exam_doc_tenant
                FOREIGN KEY (exam_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_file_pairing_candidates_solution_doc_tenant
                FOREIGN KEY (solution_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE generation_requests (
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
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(  # 5a
        "GRANT SELECT, INSERT, UPDATE ON source_connections, source_sync_cursors, crawl_urls, "
        "file_pairing_candidates, generation_requests TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT ON crawl_fetches TO ulp_app_rw")  # a fetch is a log row
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(WORKER_TABLES)} TO ulp_worker_ingestion")  # 5d


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
