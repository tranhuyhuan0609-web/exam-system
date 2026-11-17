"""C05/F02: the exam room reads the class roster, to gate a start by assignment

Starting a sitting for an assignment (`POST /exam-sessions/start` with `assignment_id`) must check that the caller is
an ACTIVE member of the assigned class, which is `check_can_start_assignment` of F02. It runs in the same transaction
as the insert of the attempt, as `ulp_exam_session_rw`, and that role could read `assessment_assignments` but not the
roster. It now may SELECT `classes` and `class_members`.

Both tables hold who sits in which class and nothing of any answer; the tenant-isolation policy (FORCE ROW LEVEL
SECURITY) already applies to every role. `ulp_exam_session_rw` keeps its REVOKE on `question_version_answers` and
`assessment_answer_keys`. Chapter 4 section 5b and tests/security/grants_manifest.csv list the two new rows.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-11-15
"""

from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON classes, class_members TO ulp_exam_session_rw")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON classes, class_members FROM ulp_exam_session_rw")
