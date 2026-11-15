"""A08 dedup, A04 exam import: provenance_records, exam_import_sessions, duplicate_clusters, duplicate_members

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (extraction and candidates groups); all
four are strict tenant tables (section 2).

Also the three foreign keys that TASK-16 left for these parents (the columns already exist, nullable, as in the spec):
* content_candidates.provenance_id    -> provenance_records(id, tenant_id)
* question_candidates.provenance_id   -> provenance_records(id, tenant_id)
* question_candidates.duplicate_cluster_id -> duplicate_clusters(id, tenant_id)
`questions.duplicate_cluster_id` stays a plain UUID, as in the spec: the canonical question of a cluster is a pointer,
not a constraint, so a bank question can outlive (or precede) its cluster.

Privileges follow section 5d (ulp_worker_ingestion, as in grants_manifest.csv): provenance records, import sessions and
clusters are read, inserted and updated; members are read and inserted only. The API role works the review of
clusters and import sessions (5a) but members are append-only for it too: a candidate leaves a cluster by the
cluster being resolved, not by deleting history.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

TABLES = ("provenance_records", "exam_import_sessions", "duplicate_clusters", "duplicate_members")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE provenance_records (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            document_version_id UUID NOT NULL,
            extraction_run_id UUID NOT NULL,
            page_no INT NOT NULL,
            bbox_json JSONB,
            section_label VARCHAR(120),
            original_question_label VARCHAR(60),
            raw_snippet_text TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_provenance_records_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_provenance_records_document_version_tenant
                FOREIGN KEY (document_version_id, tenant_id)
                REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_provenance_records_run_tenant
                FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE exam_import_sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extraction_run_id UUID NOT NULL,
            proposed_structure_json JSONB NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_exam_import_sessions_status CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')),
            CONSTRAINT fk_exam_import_sessions_run_tenant
                FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE duplicate_clusters (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            cluster_type VARCHAR(30) NOT NULL,
            canonical_question_id UUID,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_duplicate_clusters_type CHECK (cluster_type IN ('EXACT', 'STRUCTURAL_VARIANT', 'SEMANTIC_NEAR')),
            CONSTRAINT ck_duplicate_clusters_status
                CHECK (status IN ('PENDING_REVIEW', 'RESOLVED_MERGED', 'RESOLVED_KEEP_VARIANTS')),
            CONSTRAINT uq_duplicate_clusters_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE duplicate_members (
            cluster_id UUID NOT NULL,
            candidate_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            similarity_score NUMERIC(4,3) NOT NULL DEFAULT 1.000,
            PRIMARY KEY (cluster_id, candidate_id),
            CONSTRAINT fk_duplicate_members_cluster_tenant
                FOREIGN KEY (cluster_id, tenant_id)
                REFERENCES duplicate_clusters(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_duplicate_members_candidate_tenant
                FOREIGN KEY (candidate_id, tenant_id)
                REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    # The parents exist now: close the foreign keys TASK-16 could not create.
    op.execute(
        """
        ALTER TABLE content_candidates
            ADD CONSTRAINT fk_content_candidates_provenance_tenant
            FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id)
        """
    )
    op.execute(
        """
        ALTER TABLE question_candidates
            ADD CONSTRAINT fk_question_candidates_provenance_tenant
            FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id)
        """
    )
    op.execute(
        """
        ALTER TABLE question_candidates
            ADD CONSTRAINT fk_question_candidates_duplicate_cluster_tenant
            FOREIGN KEY (duplicate_cluster_id, tenant_id) REFERENCES duplicate_clusters(id, tenant_id)
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON provenance_records, exam_import_sessions, duplicate_clusters TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT ON duplicate_members TO ulp_app_rw")  # append-only for the API too
    op.execute(  # 5d
        "GRANT SELECT, INSERT, UPDATE ON provenance_records, exam_import_sessions, duplicate_clusters "
        "TO ulp_worker_ingestion"
    )
    op.execute("GRANT SELECT, INSERT ON duplicate_members TO ulp_worker_ingestion")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE question_candidates DROP CONSTRAINT IF EXISTS fk_question_candidates_duplicate_cluster_tenant"
    )
    op.execute("ALTER TABLE question_candidates DROP CONSTRAINT IF EXISTS fk_question_candidates_provenance_tenant")
    op.execute("ALTER TABLE content_candidates DROP CONSTRAINT IF EXISTS fk_content_candidates_provenance_tenant")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
