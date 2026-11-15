"""C07 grading: rubric_versions, grades, grading_details, appeals

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables:
* grades: one row per grading of an attempt, revisioned: a regrade (appeal, errata, manual override) inserts
  `revision_no + 1` and turns the previous row `is_current = FALSE`; `uq_grades_current_per_attempt` allows exactly one
  current grade per attempt. A grade is RESTRICTed against deleting its attempt.
* grading_details: the per-question outcome of one grade (earned and maximum points, sub-items).
* rubric_versions: the tenant's essay rubrics, read by the grading worker.
* appeals: a student's request to review a released grade (OPEN -> APPROVED_REGRADED / REJECTED).

Privileges, section 5: the grading worker reads rubrics and writes grades and details (5c, grants_manifest.csv). The
API role reads grades and details and may only stamp `grades.released_at` (column grant): a teacher releases a score,
but a score is changed only by grading again, which leaves the old revision in place. It works rubrics and appeals.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-11-15
"""

from alembic import op
from app.platform.rls import strict_tenant_rls

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None

TABLES = ("rubric_versions", "grades", "grading_details", "appeals")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE rubric_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            title VARCHAR(255) NOT NULL,
            criteria_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_rubric_versions_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE grades (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            revision_no INT NOT NULL DEFAULT 1,
            is_current BOOLEAN NOT NULL DEFAULT TRUE,
            grade_reason VARCHAR(40) NOT NULL DEFAULT 'INITIAL',
            scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
            total_score NUMERIC(6,2) NOT NULL,
            section_breakdown_json JSONB NOT NULL,
            item_details_json JSONB NOT NULL,
            graded_by UUID REFERENCES users(id),
            graded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            released_at TIMESTAMPTZ,
            CONSTRAINT ck_grades_grade_reason
                CHECK (grade_reason IN ('INITIAL', 'APPEAL_REGRADE', 'ERRATA_REGRADE', 'MANUAL_OVERRIDE')),
            CONSTRAINT uq_grades_attempt_id_revision_no UNIQUE (attempt_id, revision_no),
            CONSTRAINT uq_grades_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_grades_attempt_tenant
                FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_grades_current_per_attempt ON grades (attempt_id) WHERE is_current = TRUE")
    op.execute(
        """
        CREATE TABLE grading_details (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            grade_id UUID NOT NULL,
            question_version_id UUID NOT NULL REFERENCES question_versions(id),
            earned_points NUMERIC(5,2) NOT NULL,
            max_points NUMERIC(5,2) NOT NULL,
            is_correct BOOLEAN NOT NULL,
            sub_item_breakdown_json JSONB NOT NULL DEFAULT '{}',
            CONSTRAINT fk_grading_details_grade_tenant
                FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE appeals (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            grade_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            question_version_id UUID REFERENCES question_versions(id),
            reason_text TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'OPEN',
            resolution_note TEXT,
            resolved_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            CONSTRAINT ck_appeals_status CHECK (status IN ('OPEN', 'APPROVED_REGRADED', 'REJECTED')),
            CONSTRAINT fk_appeals_grade_tenant
                FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE RESTRICT,
            CONSTRAINT fk_appeals_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT ON rubric_versions TO ulp_worker_grading")  # 5c
    op.execute("GRANT SELECT, INSERT, UPDATE ON grades, grading_details TO ulp_worker_grading")
    op.execute("GRANT SELECT, INSERT, UPDATE ON rubric_versions, appeals TO ulp_app_rw")  # 5a
    op.execute("GRANT SELECT ON grades, grading_details TO ulp_app_rw")
    op.execute("GRANT UPDATE (released_at) ON grades TO ulp_app_rw")  # releasing; scores change only by regrading


def downgrade() -> None:
    for table in ("appeals", "grading_details", "grades", "rubric_versions"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
