"""A07 extraction, A09 review, C02 quality: candidates, review queue, rights records, verification runs

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (candidates and governance group).
All six are strict tenant tables.

Foreign keys that point at tables a later task creates are NOT created here, and come with that task:
* content_candidates.provenance_id, question_candidates.provenance_id -> provenance_records (A04, TASK-18)
* question_candidates.duplicate_cluster_id -> duplicate_clusters (A08, TASK-18)
* verification_runs.question_version_id -> question_versions (C01, TASK-17)
Until then the columns are plain UUIDs (nullable, as in the spec); the services never fill the ones whose parent
table does not exist yet, except provenance anchors, which A07 keeps in payload_json["anchors"].

Privileges follow section 5: the API role works the queue; verification runs and review decisions are append-only
records (no UPDATE or DELETE); the ingestion worker reads, inserts and updates candidates, verification runs and
review tasks, and nothing else of this group (section 5d).

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None

TABLES = (
    "source_rights",
    "content_candidates",
    "question_candidates",
    "verification_runs",
    "review_tasks",
    "review_decisions",
)

RIGHTS = "('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')"
ACQUISITION = "('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_rights (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL,
            license_type VARCHAR(60) NOT NULL,
            allow_rebranding BOOLEAN NOT NULL DEFAULT FALSE,
            allow_public_redistribution BOOLEAN NOT NULL DEFAULT FALSE,
            evidence_asset_id UUID,
            verified_by UUID REFERENCES users(id),
            verified_at TIMESTAMPTZ,
            CONSTRAINT fk_source_rights_source_tenant
                FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE content_candidates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extraction_run_id UUID NOT NULL,
            provenance_id UUID,
            title VARCHAR(500) NOT NULL,
            blocks_json JSONB NOT NULL DEFAULT '[]',
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED',
            verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            promoted_content_version_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_content_candidates_acquisition_status CHECK (acquisition_status IN {ACQUISITION}),
            CONSTRAINT ck_content_candidates_verification_status
                CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'REJECTED')),
            CONSTRAINT ck_content_candidates_rights_status CHECK (rights_status IN {RIGHTS}),
            CONSTRAINT uq_content_candidates_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_content_candidates_run_tenant
                FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE question_candidates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            extraction_run_id UUID,
            provenance_id UUID,
            parent_candidate_id UUID,
            acquisition_method VARCHAR(40) NOT NULL,
            item_type VARCHAR(40) NOT NULL,
            payload_json JSONB NOT NULL,
            content_hash CHAR(64) NOT NULL,
            structural_hash CHAR(64) NOT NULL,
            embedding_vec vector(768),
            duplicate_cluster_id UUID,
            extraction_confidence NUMERIC(4,3),
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED',
            verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            candidate_status VARCHAR(30) NOT NULL DEFAULT 'NEEDS_REVIEW',
            promoted_question_version_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_question_candidates_acquisition_method CHECK (
                acquisition_method IN ('DOCUMENT_IMPORT', 'WEB_CRAWL', 'DRIVE_SYNC', 'AI_GENERATED', 'MANUAL_AUTHOR')
            ),
            CONSTRAINT ck_question_candidates_item_type CHECK (
                item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY',
                              'GROUP_STEM')
            ),
            CONSTRAINT ck_question_candidates_acquisition_status CHECK (acquisition_status IN {ACQUISITION}),
            CONSTRAINT ck_question_candidates_verification_status CHECK (
                verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')
            ),
            CONSTRAINT ck_question_candidates_rights_status CHECK (rights_status IN {RIGHTS}),
            CONSTRAINT ck_question_candidates_candidate_status
                CHECK (candidate_status IN ('NEEDS_REVIEW', 'APPROVED', 'REJECTED')),
            CONSTRAINT uq_question_candidates_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_question_candidates_run_tenant
                FOREIGN KEY (extraction_run_id, tenant_id)
                REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_question_candidates_content_hash ON question_candidates (tenant_id, content_hash)")
    op.execute("CREATE INDEX ix_question_candidates_struct_hash ON question_candidates (tenant_id, structural_hash)")
    op.execute(
        """
        CREATE TABLE verification_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            question_version_id UUID,
            candidate_id UUID,
            verifier_type VARCHAR(40) NOT NULL,
            model_or_tool_version VARCHAR(80) NOT NULL,
            computed_answer VARCHAR(255),
            is_match_with_source BOOLEAN,
            findings_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_verification_runs_verifier_type CHECK (
                verifier_type IN ('DETERMINISTIC_RULE', 'SYMPY_SOLVER', 'LLM_BLIND_SOLVER', 'HUMAN_EXPERT')
            ),
            CONSTRAINT fk_verification_runs_candidate_tenant
                FOREIGN KEY (candidate_id, tenant_id)
                REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE review_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            entity_type VARCHAR(40) NOT NULL,
            entity_id UUID NOT NULL,
            queue_name VARCHAR(60) NOT NULL,
            severity VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
            status VARCHAR(30) NOT NULL DEFAULT 'OPEN',
            assigned_to UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_review_tasks_entity_type CHECK (
                entity_type IN ('QUESTION_CANDIDATE', 'QUESTION_VERSION', 'EXAM_ARCHIVE', 'CONTENT_VERSION')
            ),
            CONSTRAINT ck_review_tasks_severity CHECK (severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
            CONSTRAINT ck_review_tasks_status CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CANCELLED')),
            CONSTRAINT uq_review_tasks_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_review_tasks_queue_status ON review_tasks (tenant_id, queue_name, status, severity)")
    op.execute(
        """
        CREATE TABLE review_decisions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            review_task_id UUID NOT NULL,
            reviewer_id UUID NOT NULL REFERENCES users(id),
            decision VARCHAR(30) NOT NULL,
            reason TEXT NOT NULL,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_review_decisions_decision
                CHECK (decision IN ('APPROVE', 'REJECT', 'REQUEST_REPROCESS', 'SPLIT_MERGE')),
            CONSTRAINT fk_review_decisions_task_tenant
                FOREIGN KEY (review_task_id, tenant_id) REFERENCES review_tasks(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE ON source_rights, content_candidates, question_candidates TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE ON review_tasks TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT ON verification_runs, review_decisions TO ulp_app_rw")  # append-only records
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON content_candidates, question_candidates, verification_runs, review_tasks "
        "TO ulp_worker_ingestion"
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
