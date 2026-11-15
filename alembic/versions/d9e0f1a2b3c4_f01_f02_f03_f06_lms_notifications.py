"""F01 classes, F02 assignments, F03 gradebook, F06 notifications: groups, classes, class_members,
assessment_assignments, gradebook_projections, notifications, notification_deliveries

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql, all strict tenant tables (section 2):
* classes and class_members: a student who leaves a class is soft-deleted (`status = INACTIVE`, `left_at`), so the
  history of their attempts and grades stays; the membership foreign key is RESTRICT (as in the DDL), a roster row
  never disappears with its membership.
* assessment_assignments: a paper of one tenant version given to a class (composite foreign keys to
  assessment_versions, RESTRICT, and to classes, CASCADE); there is no foreign key on group_id, as in the DDL.
  `attempts.assignment_id` stays a plain UUID, as in the DDL: chapter 4 gives it no foreign key.
* gradebook_projections: per class and assignment, rebuilt by the grading worker; keyed (class_id, assignment_id)
  with a composite foreign key to classes only, as in the DDL.
* notifications: `dedup_key` is UNIQUE, so replaying an event creates one notification. It also gets
  UNIQUE (id, tenant_id), which the spec lacks, to anchor the composite foreign key below.
* notification_deliveries: Decision #7 (IMPLEMENTATION_PLAN_V11_3.md) gives it a `tenant_id` and the standard strict
  policy, where chapter 4 has none. The foreign key to the notification is composite (same tenant by construction),
  and a channel is delivered once per notification (UNIQUE (notification_id, channel)).

Privileges, section 5 and grants_manifest.csv: the exam room reads assignments (5b), the grading worker upserts the
gradebook projection (5c). The API role (5a) runs classes, assignments, notifications and the gradebook; nothing
deletes a roster row, an assignment or a notification (leaving a class is an UPDATE), delivery rows are append-only.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-11-15
"""

from alembic import op
from app.platform.rls import strict_tenant_rls

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None

TABLES = (
    "groups",
    "classes",
    "class_members",
    "assessment_assignments",
    "gradebook_projections",
    "notifications",
    "notification_deliveries",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE groups (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_groups_id_tenant UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE classes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            group_id UUID,
            teacher_membership_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL,
            academic_year VARCHAR(30),
            invite_code VARCHAR(30) UNIQUE,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_classes_status CHECK (status IN ('ACTIVE', 'ARCHIVED')),
            CONSTRAINT uq_classes_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_classes_group_tenant
                FOREIGN KEY (group_id, tenant_id) REFERENCES groups(id, tenant_id) ON DELETE SET NULL,
            CONSTRAINT fk_classes_teacher_membership_tenant
                FOREIGN KEY (teacher_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE class_members (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            class_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            role_in_class VARCHAR(30) NOT NULL DEFAULT 'STUDENT',
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            left_at TIMESTAMPTZ,
            CONSTRAINT ck_class_members_role_in_class CHECK (role_in_class IN ('STUDENT', 'TEACHING_ASSISTANT')),
            CONSTRAINT ck_class_members_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
            CONSTRAINT uq_class_members_class_membership UNIQUE (class_id, membership_id),
            CONSTRAINT uq_class_members_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_class_members_class_tenant
                FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_class_members_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE assessment_assignments (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            assessment_version_id UUID NOT NULL,
            class_id UUID,
            group_id UUID,
            assigned_by UUID NOT NULL REFERENCES users(id),
            available_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            due_at TIMESTAMPTZ,
            max_attempts INT NOT NULL DEFAULT 1,
            reveal_policy VARCHAR(40) NOT NULL DEFAULT 'AFTER_DUE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_assessment_assignments_reveal_policy
                CHECK (reveal_policy IN ('IMMEDIATE', 'AFTER_DUE', 'MANUAL_RELEASE', 'NEVER')),
            CONSTRAINT uq_assessment_assignments_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_assessment_assignments_version_tenant
                FOREIGN KEY (assessment_version_id, tenant_id)
                REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT,
            CONSTRAINT fk_assessment_assignments_class_tenant
                FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE gradebook_projections (
            class_id UUID NOT NULL,
            assignment_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            stats_json JSONB NOT NULL DEFAULT '{}',
            top_wrong_questions_json JSONB NOT NULL DEFAULT '[]',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (class_id, assignment_id),
            CONSTRAINT fk_gradebook_projections_class_tenant
                FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notifications (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            dedup_key VARCHAR(160) UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            body_text TEXT NOT NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_notifications_id_tenant UNIQUE (id, tenant_id),
            CONSTRAINT fk_notifications_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE notification_deliveries (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            notification_id UUID NOT NULL,
            channel VARCHAR(30) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'SENT',
            delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_notification_deliveries_channel CHECK (channel IN ('IN_APP', 'EMAIL')),
            CONSTRAINT uq_notification_deliveries_notif_channel UNIQUE (notification_id, channel),
            CONSTRAINT fk_notification_deliveries_notification_tenant
                FOREIGN KEY (notification_id, tenant_id)
                REFERENCES notifications(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    for table in TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute(  # 5a
        "GRANT SELECT, INSERT, UPDATE ON groups, classes, class_members, assessment_assignments, "
        "gradebook_projections, notifications TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT ON notification_deliveries TO ulp_app_rw")
    op.execute("GRANT SELECT ON assessment_assignments TO ulp_exam_session_rw")  # 5b
    op.execute("GRANT SELECT, INSERT, UPDATE ON gradebook_projections TO ulp_worker_grading")  # 5c


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
