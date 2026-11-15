"""C04 assembly, C05 delivery: assessments, assessment_versions, assessment_items, assessment_answer_keys, attempts

Tables, policies, triggers and privileges as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* assessments / assessment_versions / assessment_items are shared material (hybrid RLS): a tenant reads its own rows
  plus the system tenant's PUBLISHED, cleared papers and their frozen versions and items; writes stay in the tenant.
* assessment_answer_keys and attempts are strict tenant tables. The grading worker reads the keys of its tenant and of
  the system tenant (`grading_read_answer_keys`); the expiry job reads IN_PROGRESS attempts of every tenant through
  `sys_attempt_expiry_scan` (`app.system_job = 'attempt_expiry'`), no BYPASSRLS.
* `trg_check_assessment_item_question_ref`: a paper may use the tenant's own question versions, or frozen SYSTEM_PUBLIC
  ones of the system tenant; any other tenant's version is refused.
* `trg_immutable_assessment_versions`: a frozen version can no longer change (a draft version, `is_immutable = FALSE`,
  is what C04 assembles; publishing freezes it).
* `chk_assessment_publish_gate` and the deferred `fk_assessments_current_version`, as for questions and content.
`attempts.assessment_version_id` and `assessment_items.question_version_id` are single-column foreign keys: a student of
one tenant sits a paper of the system tenant. `attempts.assignment_id` stays a plain UUID until F02 creates
`assessment_assignments` (TASK-30), which needs `classes` (F01).

Privileges, section 5: ulp_app_rw authors papers (5a); ulp_exam_session_rw reads papers and runs attempts (5b); the
grading worker reads versions, items, keys and attempts and updates `attempts.status` and `submitted_at` (5c); the
scheduler reads four attempt columns (5e). `assessment_answer_keys` is revoked from PUBLIC and from the exam-room,
ingestion, scheduler and analytics roles (5g, GATE-03: zero answer leak).

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import (
    FROZEN_VERSION,
    grading_read,
    hybrid_read_child,
    hybrid_read_root,
    hybrid_write_rls,
    strict_tenant_rls,
    system_job_policy,
)

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

TABLES = ("assessments", "assessment_versions", "assessment_items", "assessment_answer_keys", "attempts")
NO_KEY_ROLES = "PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion, ulp_system_scheduler, ulp_analytics_ro"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE assessments (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            title VARCHAR(500) NOT NULL,
            subject_code VARCHAR(50) NOT NULL,
            grade_band VARCHAR(30) NOT NULL,
            exam_type_code VARCHAR(50) NOT NULL,
            source_archive_doc_id UUID,
            branded_pdf_asset_id UUID,
            preview_pdf_asset_id UUID,
            access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE',
            current_version_id UUID,
            verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_assessments_visibility_scope CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
            CONSTRAINT ck_assessments_access_tier CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
            CONSTRAINT ck_assessments_verification_status CHECK (
                verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')
            ),
            CONSTRAINT ck_assessments_rights_status CHECK (
                rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')
            ),
            CONSTRAINT ck_assessments_publication_status
                CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
            CONSTRAINT uq_assessments_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT chk_assessment_publish_gate CHECK (
                publication_status <> 'PUBLISHED'
                OR (verification_status = 'VERIFIED'
                    AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                    AND current_version_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE assessment_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            assessment_id UUID NOT NULL,
            version_no INT NOT NULL,
            scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
            duration_seconds INT NOT NULL,
            total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
            base_seed BIGINT NOT NULL DEFAULT 20261111,
            structure_snapshot_json JSONB NOT NULL,
            is_immutable BOOLEAN NOT NULL DEFAULT TRUE,
            published_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_assessment_versions_assessment_id_version_no UNIQUE (assessment_id, version_no),
            CONSTRAINT uq_assessment_versions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_assessment_versions_assessment_tenant
                FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        ALTER TABLE assessments
            ADD CONSTRAINT fk_assessments_current_version
            FOREIGN KEY (current_version_id, tenant_id)
            REFERENCES assessment_versions(id, tenant_id)
            DEFERRABLE INITIALLY DEFERRED
        """
    )
    op.execute(
        """
        CREATE TABLE assessment_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            assessment_version_id UUID NOT NULL,
            question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
            section_code VARCHAR(30) NOT NULL,
            sequence_order INT NOT NULL,
            points NUMERIC(5,2) NOT NULL,
            CONSTRAINT uq_assessment_items_version_id_sequence_order UNIQUE (assessment_version_id, sequence_order),
            CONSTRAINT fk_assessment_items_version_tenant
                FOREIGN KEY (assessment_version_id, tenant_id)
                REFERENCES assessment_versions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE assessment_answer_keys (
            assessment_version_id UUID NOT NULL,
            key_revision_no INT NOT NULL DEFAULT 1,
            tenant_id UUID NOT NULL,
            is_current BOOLEAN NOT NULL DEFAULT TRUE,
            answer_key_payload_json JSONB NOT NULL,
            key_checksum_sha256 CHAR(64) NOT NULL,
            change_reason TEXT NOT NULL DEFAULT 'INITIAL_PUBLISH',
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (assessment_version_id, key_revision_no),
            CONSTRAINT fk_assessment_answer_keys_version_tenant
                FOREIGN KEY (assessment_version_id, tenant_id)
                REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_assessment_answer_keys_current "
        "ON assessment_answer_keys (assessment_version_id) WHERE is_current = TRUE"
    )
    op.execute(
        """
        CREATE TABLE attempts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            assessment_version_id UUID NOT NULL REFERENCES assessment_versions(id) ON DELETE RESTRICT,
            assignment_id UUID,
            membership_id UUID NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS',
            attempt_seed BIGINT NOT NULL,
            question_order_ids UUID[] NOT NULL,
            option_permutations_json JSONB NOT NULL DEFAULT '{}',
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deadline_at TIMESTAMPTZ NOT NULL,
            submitted_at TIMESTAMPTZ,
            submit_idempotency_key VARCHAR(128) UNIQUE,
            CONSTRAINT ck_attempts_status
                CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'VOIDED')),
            CONSTRAINT uq_attempts_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_attempts_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE INDEX ix_attempts_membership_status ON attempts (tenant_id, membership_id, status)")
    op.execute(
        "CREATE INDEX ix_attempts_auto_submit_scan ON attempts (status, deadline_at) WHERE status = 'IN_PROGRESS'"
    )

    # --- triggers (spec section 3 and 4) -----------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_tenant_or_system_public_question_ref() RETURNS trigger AS $$
        DECLARE
            v_target_tenant UUID;
            v_target_scope VARCHAR(20);
            v_target_immutable BOOLEAN;
        BEGIN
            SELECT tenant_id, visibility_scope, is_immutable INTO v_target_tenant, v_target_scope, v_target_immutable
            FROM question_versions
            WHERE id = NEW.question_version_id;

            IF v_target_tenant IS NULL THEN
                RAISE EXCEPTION 'FK_NOT_FOUND: Không tìm thấy question_version_id %', NEW.question_version_id;
            END IF;

            IF v_target_tenant <> NEW.tenant_id AND NOT (
                v_target_tenant = system_public_tenant_id()
                AND v_target_scope = 'SYSTEM_PUBLIC'
                AND v_target_immutable IS TRUE
            ) THEN
                RAISE EXCEPTION 'CROSS_TENANT_REFERENCE_FORBIDDEN: Tenant % không được phép sử dụng question_version_id % chưa công khai của Tenant khác.',
                    NEW.tenant_id, NEW.question_version_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_check_assessment_item_question_ref
            BEFORE INSERT OR UPDATE ON assessment_items
            FOR EACH ROW EXECUTE FUNCTION enforce_tenant_or_system_public_question_ref()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_immutable_assessment_versions
            BEFORE UPDATE OR DELETE ON assessment_versions
            FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation()
        """
    )

    # --- row-level security ---------------------------------------------------------------------
    statements = [
        *hybrid_write_rls("assessments"),
        *hybrid_read_root("assessments"),
        *hybrid_write_rls("assessment_versions"),
        *hybrid_read_child("assessment_versions", "assessments", "assessment_id", own_condition=FROZEN_VERSION),
        *hybrid_write_rls("assessment_items"),
        *hybrid_read_child("assessment_items", "assessment_versions", "assessment_version_id"),
        *strict_tenant_rls("assessment_answer_keys"),
        *grading_read("assessment_answer_keys", "grading_read_answer_keys"),
        *strict_tenant_rls("attempts"),
        *system_job_policy(
            "attempts", "sys_attempt_expiry_scan", "attempt_expiry", "SELECT", row_condition="status = 'IN_PROGRESS'"
        ),
    ]
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(  # 5a: the API role authors papers; a draft version is re-assembled, so items are replaced
        "GRANT SELECT, INSERT, UPDATE ON assessments, assessment_versions, assessment_answer_keys TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON assessment_items TO ulp_app_rw")
    op.execute("GRANT SELECT, UPDATE ON attempts TO ulp_app_rw")  # a teacher voids an attempt; attempts start in 5b
    op.execute("GRANT SELECT ON assessments, assessment_versions, assessment_items TO ulp_exam_session_rw")  # 5b
    op.execute("GRANT SELECT, INSERT, UPDATE ON attempts TO ulp_exam_session_rw")
    op.execute(  # 5c
        "GRANT SELECT ON assessment_versions, assessment_items, assessment_answer_keys, attempts TO ulp_worker_grading"
    )
    op.execute("GRANT UPDATE (status, submitted_at) ON attempts TO ulp_worker_grading")
    op.execute("GRANT SELECT (id, tenant_id, status, deadline_at) ON attempts TO ulp_system_scheduler")  # 5e
    op.execute(f"REVOKE ALL ON assessment_answer_keys FROM {NO_KEY_ROLES}")  # 5g


def downgrade() -> None:
    op.execute("ALTER TABLE assessments DROP CONSTRAINT IF EXISTS fk_assessments_current_version")
    for table in ("attempts", "assessment_answer_keys", "assessment_items", "assessment_versions", "assessments"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS enforce_tenant_or_system_public_question_ref()")
