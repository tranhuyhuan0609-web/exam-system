"""C01 questions: legacy `questions` -> `legacy_questions`, V11.3 question bank tables, view, triggers, grants

1. The pre-V11.3 `questions` table (integer ids) becomes `legacy_questions`, with its sequence, primary key,
   foreign keys and indexes renamed to match (`app/models.py::Question`); foreign keys of other legacy tables
   follow the table. The backfill (`app/ops/run_backfill.py`) already reads `legacy_questions`.
2. Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql: `questions`, `question_stimuli`,
   `question_versions`, `question_version_answers`, `question_options`, `question_assets`.
   * Shared material (hybrid RLS): `questions` is public only when SYSTEM_PUBLIC, PUBLISHED and rights cleared;
     a version only when SYSTEM_PUBLIC and frozen and its question is visible; options, assets and stimuli only
     through a visible version (the EXISTS subqueries read the parent under the caller's own RLS).
   * `question_version_answers` is a strict tenant table, plus `grading_read_question_answers` for the grading
     worker (answers of system papers).
   * `v_public_question_versions` is `security_invoker` (F-02): it reads with the caller's RLS and grants.
3. Triggers: `trg_lock_question_version_on_publish` freezes the current version and its answers when the
   question is PUBLISHED; `trg_immutable_question_versions` / `trg_immutable_question_version_answers` (the
   F-03 fixed `prevent_version_immutability_mutation`, created by B01) refuse any change to a frozen row.
4. `verification_runs.question_version_id -> question_versions(id, tenant_id)`, which TASK-16 left for C01.
5. Privileges, section 5: 5a ulp_app_rw, 5b ulp_exam_session_rw, 5c ulp_worker_grading, 5f + 5i (F-02)
   ulp_analytics_ro, and 5g: question_version_answers revoked from PUBLIC, ulp_exam_session_rw,
   ulp_worker_ingestion, ulp_system_scheduler and ulp_analytics_ro (GATE-03, zero answer leak).

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import (
    FROZEN_VERSION,
    grading_read,
    hybrid_read_child,
    hybrid_read_root,
    hybrid_write_rls,
    invoker_view,
    strict_tenant_rls,
)

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None

HYBRID_TABLES = ("questions", "question_stimuli", "question_versions", "question_options", "question_assets")
NEW_TABLES = (*HYBRID_TABLES, "question_version_answers")
PUBLIC_VIEW_COLUMNS = (
    "id, tenant_id, visibility_scope, question_id, version_no, stimulus_id, stem_json, options_json, "
    "numeric_parse_policy_json"
)
NO_ANSWER_ROLES = "PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion, ulp_system_scheduler, ulp_analytics_ro"


def _rename_legacy(old: str, new: str) -> None:
    """Rename the legacy table and every object whose name carries the table name."""
    op.execute(f"ALTER TABLE {old} RENAME TO {new}")
    op.execute(f"ALTER SEQUENCE IF EXISTS {old}_id_seq RENAME TO {new}_id_seq")
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT conname FROM pg_constraint
                     WHERE conrelid = '{new}'::regclass AND contype IN ('p', 'f') AND conname LIKE '{old}\\_%'
            LOOP
                EXECUTE format('ALTER TABLE {new} RENAME CONSTRAINT %I TO %I',
                               r.conname, '{new}' || substr(r.conname, length('{old}') + 1));
            END LOOP;
            FOR r IN SELECT indexname FROM pg_indexes
                     WHERE schemaname = 'public' AND tablename = '{new}' AND indexname LIKE 'ix\\_{old}\\_%'
            LOOP
                EXECUTE format('ALTER INDEX %I RENAME TO %I',
                               r.indexname, 'ix_{new}' || substr(r.indexname, length('ix_{old}') + 1));
            END LOOP;
        END $$
        """
    )


def upgrade() -> None:
    _rename_legacy("questions", "legacy_questions")

    op.execute(
        """
        CREATE TABLE questions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            stable_code VARCHAR(100) NOT NULL,
            item_type VARCHAR(40) NOT NULL,
            primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
            curriculum_node_id UUID REFERENCES curriculum_nodes(id),
            exam_framework_tags VARCHAR(50)[] NOT NULL DEFAULT '{SGK_KNTT}',
            difficulty_level VARCHAR(20) NOT NULL DEFAULT 'TH',
            current_version_id UUID,
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED',
            verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE',
            duplicate_cluster_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_questions_visibility_scope
                CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
            CONSTRAINT ck_questions_item_type CHECK (
                item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY',
                              'GROUP_STEM')
            ),
            CONSTRAINT ck_questions_difficulty_level CHECK (difficulty_level IN ('NB', 'TH', 'VD', 'VDC')),
            CONSTRAINT ck_questions_acquisition_status
                CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
            CONSTRAINT ck_questions_verification_status CHECK (
                verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')
            ),
            CONSTRAINT ck_questions_rights_status CHECK (
                rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')
            ),
            CONSTRAINT ck_questions_publication_status
                CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
            CONSTRAINT ck_questions_access_tier CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
            CONSTRAINT uq_questions_tenant_id_stable_code UNIQUE (tenant_id, stable_code),
            CONSTRAINT uq_questions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT chk_question_publish_gate CHECK (
                publication_status <> 'PUBLISHED'
                OR (verification_status = 'VERIFIED'
                    AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                    AND current_version_id IS NOT NULL)
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_questions_exam_tags_gin ON questions USING GIN (exam_framework_tags)")
    op.execute(
        "CREATE INDEX ix_questions_pool_lookup ON questions "
        "(tenant_id, publication_status, verification_status, primary_taxonomy_node_id, difficulty_level)"
    )
    op.execute(
        """
        CREATE TABLE question_stimuli (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            stimulus_type VARCHAR(40) NOT NULL,
            body_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_question_stimuli_stimulus_type CHECK (
                stimulus_type IN ('READING_PASSAGE', 'EXPERIMENT_CONTEXT', 'DATA_TABLE', 'SHARED_FIGURE')
            ),
            CONSTRAINT uq_question_stimuli_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE question_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            question_id UUID NOT NULL,
            version_no INT NOT NULL,
            stimulus_id UUID,
            stem_json JSONB NOT NULL,
            options_json JSONB NOT NULL DEFAULT '[]',
            numeric_parse_policy_json JSONB NOT NULL DEFAULT
                '{"decimal_sep": "COMMA_OR_DOT", "allow_thousand_sep": false, "max_chars": 4, "tolerance": 0.01}',
            content_hash CHAR(64) NOT NULL,
            structural_hash CHAR(64) NOT NULL,
            is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
            change_reason VARCHAR(500),
            created_by UUID REFERENCES users(id),
            approved_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_question_versions_visibility_scope
                CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
            CONSTRAINT uq_question_versions_question_id_version_no UNIQUE (question_id, version_no),
            CONSTRAINT uq_question_versions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_question_versions_question_tenant
                FOREIGN KEY (question_id, tenant_id) REFERENCES questions(id, tenant_id) ON DELETE RESTRICT,
            CONSTRAINT fk_question_versions_stimulus_tenant
                FOREIGN KEY (stimulus_id, tenant_id) REFERENCES question_stimuli(id, tenant_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_question_versions_hashes ON question_versions (tenant_id, content_hash, structural_hash)"
    )
    op.execute(
        """
        CREATE TABLE question_version_answers (
            question_version_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            answer_schema JSONB NOT NULL,
            rationale_json JSONB NOT NULL DEFAULT '{}',
            is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_question_version_answers_version_tenant
                FOREIGN KEY (question_version_id, tenant_id)
                REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(invoker_view("v_public_question_versions", f"SELECT {PUBLIC_VIEW_COLUMNS} FROM question_versions"))
    op.execute(
        """
        ALTER TABLE questions
            ADD CONSTRAINT fk_questions_current_version
            FOREIGN KEY (current_version_id, tenant_id)
            REFERENCES question_versions(id, tenant_id)
            DEFERRABLE INITIALLY DEFERRED
        """
    )
    op.execute(
        """
        CREATE TABLE question_options (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            question_version_id UUID NOT NULL,
            option_key VARCHAR(10) NOT NULL,
            content_json JSONB NOT NULL,
            sequence_order INT NOT NULL,
            CONSTRAINT uq_question_options_question_version_id_option_key UNIQUE (question_version_id, option_key),
            CONSTRAINT fk_question_options_version_tenant
                FOREIGN KEY (question_version_id, tenant_id)
                REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE question_assets (
            question_version_id UUID NOT NULL,
            asset_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            PRIMARY KEY (question_version_id, asset_id),
            CONSTRAINT fk_question_assets_version_tenant
                FOREIGN KEY (question_version_id, tenant_id)
                REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_question_assets_asset_tenant
                FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    # TASK-16 left this foreign key for C01 (the column already exists, nullable, as in the spec).
    op.execute(
        """
        ALTER TABLE verification_runs
            ADD CONSTRAINT fk_verification_runs_question_version_tenant
            FOREIGN KEY (question_version_id, tenant_id)
            REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
        """
    )

    # --- immutability (spec section 4; prevent_version_immutability_mutation exists since B01, F-03 fixed) ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lock_question_version_when_published() RETURNS trigger AS $$
        BEGIN
            IF NEW.publication_status = 'PUBLISHED' AND NEW.current_version_id IS NOT NULL THEN
                UPDATE question_versions SET is_immutable = TRUE
                    WHERE id = NEW.current_version_id AND is_immutable = FALSE;
                UPDATE question_version_answers SET is_immutable = TRUE
                    WHERE question_version_id = NEW.current_version_id AND is_immutable = FALSE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lock_question_version_on_publish
            AFTER INSERT OR UPDATE OF publication_status, current_version_id ON questions
            FOR EACH ROW EXECUTE FUNCTION lock_question_version_when_published()
        """
    )
    for table in ("question_versions", "question_version_answers"):
        op.execute(
            f"""
            CREATE TRIGGER trg_immutable_{table}
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation()
            """
        )

    # --- row-level security ---------------------------------------------------------------------
    statements = [
        *hybrid_write_rls("questions"),
        *hybrid_read_root("questions"),
        *hybrid_write_rls("question_versions"),
        *hybrid_read_child(
            "question_versions",
            "questions",
            "question_id",
            own_condition=f"visibility_scope = 'SYSTEM_PUBLIC' AND {FROZEN_VERSION}",
        ),
        *hybrid_write_rls("question_options"),
        *hybrid_read_child("question_options", "question_versions", "question_version_id"),
        *hybrid_write_rls("question_assets"),
        *hybrid_read_child("question_assets", "question_versions", "question_version_id"),
        *hybrid_write_rls("question_stimuli"),
        # A stimulus is the parent of its versions, so the visible thing to look for is a version using it.
        "DROP POLICY IF EXISTS hybrid_read_policy ON question_stimuli",
        """CREATE POLICY hybrid_read_policy ON question_stimuli FOR SELECT USING (
            tenant_id = (SELECT current_app_tenant_id())
            OR (tenant_id = system_public_tenant_id() AND EXISTS (
                SELECT 1 FROM question_versions qv
                WHERE qv.stimulus_id = question_stimuli.id AND qv.tenant_id = question_stimuli.tenant_id
            ))
        )""",
        *strict_tenant_rls("question_version_answers"),
        *grading_read("question_version_answers", "grading_read_question_answers"),
    ]
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(NEW_TABLES)}, v_public_question_versions FROM PUBLIC")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(NEW_TABLES)} TO ulp_app_rw")  # 5a
    op.execute("GRANT SELECT ON v_public_question_versions TO ulp_app_rw")  # 5a: ON ALL TABLES covers views
    op.execute(f"GRANT SELECT ON v_public_question_versions, {', '.join(HYBRID_TABLES)} TO ulp_exam_session_rw")  # 5b
    op.execute("GRANT SELECT ON question_versions, question_version_answers TO ulp_worker_grading")  # 5c
    op.execute("GRANT SELECT ON v_public_question_versions TO ulp_analytics_ro")  # 5f
    # 5i (F-02): the invoker view reads with the caller's grants: exactly its columns, and the two key
    # columns of questions that the EXISTS of question_versions' hybrid_read_policy reads.
    op.execute(f"GRANT SELECT ({PUBLIC_VIEW_COLUMNS}) ON question_versions TO ulp_analytics_ro")
    op.execute("GRANT SELECT (id, tenant_id) ON questions TO ulp_analytics_ro")
    op.execute(f"REVOKE ALL ON question_version_answers FROM {NO_ANSWER_ROLES}")  # 5g


def downgrade() -> None:
    op.execute("ALTER TABLE verification_runs DROP CONSTRAINT IF EXISTS fk_verification_runs_question_version_tenant")
    op.execute("ALTER TABLE questions DROP CONSTRAINT IF EXISTS fk_questions_current_version")
    op.execute("DROP VIEW IF EXISTS v_public_question_versions")
    op.execute("DROP POLICY IF EXISTS hybrid_read_policy ON question_stimuli")  # it reads question_versions
    for table in ("question_assets", "question_options", "question_version_answers", "question_versions"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP TABLE IF EXISTS question_stimuli")
    op.execute("DROP TABLE IF EXISTS questions")
    op.execute("DROP FUNCTION IF EXISTS lock_question_version_when_published()")
    _rename_legacy("legacy_questions", "questions")
