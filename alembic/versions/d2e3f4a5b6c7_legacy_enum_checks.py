"""legacy enum CHECK constraints match the models

Alembic does not compare CHECK constraints, so values added to the legacy enums (HSG, WARN)
never reached the database, and columns added by hand had no constraint at all.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-12
"""
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

# (table, constraint, column, allowed values) as the models declared them on 2026-11-12.
ENUM_CHECKS = [
    ("categories", "access_tier", "default_tier", ("FREE", "PRO")),
    ("crawl_runs", "crawl_run_status", "status", ("QUEUED", "RUNNING", "DONE", "WARN", "FAILED")),
    ("crawled_questions", "difficulty", "difficulty", ("RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION", "HSG")),
    ("exams", "exam_access_tier", "access_tier", ("FREE", "PRO")),
    ("exams", "exam_origin_type", "origin_type", ("PUBLIC_CRAWLED", "ORIGINAL")),
    ("import_jobs", "difficulty", "default_difficulty", ("RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION", "HSG")),
    ("import_jobs", "job_access_tier", "access_tier", ("FREE", "PRO")),
    ("import_jobs", "job_origin_type", "origin_type", ("PUBLIC_CRAWLED", "ORIGINAL")),
    ("library_documents", "library_access_tier", "access_tier", ("FREE", "PRO")),
    ("questions", "difficulty", "difficulty", ("RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION", "HSG")),
    ("questions", "question_access_tier", "access_tier", ("FREE", "PRO")),
    ("questions", "question_origin_type", "origin_type", ("PUBLIC", "AUTHORED")),
]


def upgrade() -> None:
    for table, constraint, column, values in ENUM_CHECKS:
        allowed = ", ".join(f"'{value}'" for value in values)
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{constraint}"')
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT "{constraint}" CHECK ({column} IN ({allowed}))')


def downgrade() -> None:
    # Narrowing the constraints again could reject rows written since; leave them as they are.
    pass
