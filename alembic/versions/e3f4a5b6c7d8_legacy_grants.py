"""the API role may use the legacy tables

The application stops connecting as the schema owner and connects as a login that inherits
ulp_app_rw. Until its module replaces it, each legacy table is readable and writable by that role.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-12
"""
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

LEGACY_TABLES = (
    "ai_cache, ai_usage, answer_keys, categories, crawl_category_map, crawl_runs, crawled_questions, "
    "crawler_sites, exam_attempts, exam_enrollments, exam_questions, exam_targets, exams, import_jobs, "
    "knowledge_articles, knowledge_units, library_documents, library_views, question_categories, "
    "question_target_tags, questions, target_curriculum_mappings, users"
)


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {LEGACY_TABLES} TO ulp_app_rw")
    # Serial primary keys: the role needs the sequences behind them. Every sequence at this
    # revision belongs to a legacy table (V11.3 tables use UUIDv7 keys).
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ulp_app_rw")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON {LEGACY_TABLES} FROM ulp_app_rw")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ulp_app_rw")
