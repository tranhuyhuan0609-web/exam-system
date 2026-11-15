"""D02 learning path, D03 practice, D05 error notebook: learning_plans, plan_tasks, practice_sessions,
practice_responses, error_notes, remediation_tasks

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables (section 2).
Column types and lengths follow the DDL (status columns are VARCHAR(30), `practice_sessions.mode` VARCHAR(40) with
default TOPIC_DRILL) and `error_notes.primary_taxonomy_node_id` is nullable, as there: a question without a topic
can still be noted (the service asks for a topic, the database does not). Foreign keys to taxonomy_nodes and
question_versions are plain (NO ACTION), as there.

Privileges, section 5 and grants_manifest.csv: the grading worker creates and updates error notes from graded
attempts (5c). The API role (5a) runs the learner loop: plans and tasks (a regenerated plan replaces the PENDING and
IN_PROGRESS tasks, so it may delete tasks), practice sessions, appended practice answers, error notes and their
remediation tasks. Practice answers and nothing else are append-only for it.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-11-15
"""

from alembic import op
from app.platform.rls import strict_tenant_rls

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None

TABLES = (
    "learning_plans",
    "plan_tasks",
    "practice_sessions",
    "practice_responses",
    "error_notes",
    "remediation_tasks",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE learning_plans (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            target_framework_code VARCHAR(60) NOT NULL DEFAULT 'THPTQG_2025',
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_learning_plans_status CHECK (status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')),
            CONSTRAINT uq_learning_plans_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_learning_plans_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE plan_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            learning_plan_id UUID NOT NULL,
            taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
            task_type VARCHAR(40) NOT NULL,
            rationale_text TEXT NOT NULL,
            sequence_order INT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
            CONSTRAINT ck_plan_tasks_task_type CHECK (
                task_type IN ('PREREQUISITE_REVIEW', 'READ_CONCEPT', 'PRACTICE_SET', 'ERROR_REMEDIATION')
            ),
            CONSTRAINT ck_plan_tasks_status CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')),
            CONSTRAINT fk_plan_tasks_plan_tenant
                FOREIGN KEY (learning_plan_id, tenant_id)
                REFERENCES learning_plans(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE practice_sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            mode VARCHAR(40) NOT NULL DEFAULT 'TOPIC_DRILL',
            status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT ck_practice_sessions_mode CHECK (mode IN ('LESSON_EMBED', 'TOPIC_DRILL', 'REMEDIATION_DRILL')),
            CONSTRAINT ck_practice_sessions_status CHECK (status IN ('IN_PROGRESS', 'COMPLETED')),
            CONSTRAINT uq_practice_sessions_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_practice_sessions_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE practice_responses (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            practice_session_id UUID NOT NULL,
            question_version_id UUID NOT NULL REFERENCES question_versions(id),
            response_payload JSONB NOT NULL,
            is_correct BOOLEAN NOT NULL,
            hints_used INT NOT NULL DEFAULT 0,
            answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_practice_responses_session_tenant
                FOREIGN KEY (practice_session_id, tenant_id)
                REFERENCES practice_sessions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE error_notes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            question_version_id UUID NOT NULL REFERENCES question_versions(id),
            primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
            wrong_count INT NOT NULL DEFAULT 1,
            consecutive_correct_count INT NOT NULL DEFAULT 0,
            status VARCHAR(30) NOT NULL DEFAULT 'OPEN',
            last_wrong_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            CONSTRAINT ck_error_notes_status CHECK (status IN ('OPEN', 'IN_REMEDIATION', 'RESOLVED')),
            CONSTRAINT uq_error_notes_membership_qv UNIQUE (membership_id, question_version_id),
            CONSTRAINT uq_error_notes_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_error_notes_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE remediation_tasks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            error_note_id UUID NOT NULL,
            recommended_question_version_ids UUID[] NOT NULL,
            completed_count INT NOT NULL DEFAULT 0,
            status VARCHAR(30) NOT NULL DEFAULT 'ASSIGNED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_remediation_tasks_status CHECK (status IN ('ASSIGNED', 'COMPLETED')),
            CONSTRAINT fk_remediation_tasks_error_note_tenant
                FOREIGN KEY (error_note_id, tenant_id)
                REFERENCES error_notes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(  # 5a
        "GRANT SELECT, INSERT, UPDATE ON learning_plans, practice_sessions, error_notes, remediation_tasks "
        "TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON plan_tasks TO ulp_app_rw")  # a replan replaces open tasks
    op.execute("GRANT SELECT, INSERT ON practice_responses TO ulp_app_rw")  # an answer is never edited
    op.execute("GRANT SELECT, INSERT, UPDATE ON error_notes TO ulp_worker_grading")  # 5c


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
