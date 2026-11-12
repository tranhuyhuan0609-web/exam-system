"""legacy schema catch-up

Tables, columns and indexes that the legacy models declare but no migration ever created
(they were added to the development database by hand). Every step is guarded, so the same
revision brings a fresh database and a hand-patched one to the same schema.

Revision ID: b0c1d2e3f4a5
Revises: f9a1b2c3d4e5
Create Date: 2026-11-12
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "f9a1b2c3d4e5"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _add_column(table: str, column: sa.Column) -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def _add_questions_knowledge_unit_fk() -> None:
    fks = sa.inspect(op.get_bind()).get_foreign_keys("questions")
    if not any(fk["constrained_columns"] == ["knowledge_unit_id"] for fk in fks):
        op.create_foreign_key(
            "questions_knowledge_unit_id_fkey", "questions", "knowledge_units", ["knowledge_unit_id"], ["id"],
            ondelete="SET NULL",
        )


def upgrade() -> None:
    if not _has_table('exam_targets'):
        op.create_table('exam_targets',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(length=30), nullable=False),
            sa.Column('group_category', sa.String(length=30), nullable=False),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('access_tier', sa.Enum('FREE', 'PRO', name='target_access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
    op.create_index(op.f('ix_exam_targets_code'), 'exam_targets', ['code'], unique=True, if_not_exists=True)
    op.create_index(op.f('ix_exam_targets_group_category'), 'exam_targets', ['group_category'], unique=False, if_not_exists=True)
    if not _has_table('knowledge_units'):
        op.create_table('knowledge_units',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('parent_id', sa.Integer(), nullable=True),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('subject', sa.String(length=50), nullable=False),
            sa.Column('grade', sa.Integer(), nullable=False),
            sa.Column('unit_type', sa.String(length=30), nullable=False),
            sa.Column('code', sa.String(length=60), nullable=True),
            sa.Column('name', sa.String(length=255), nullable=False),
            sa.Column('slug', sa.String(length=255), nullable=True),
            sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
            sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['parent_id'], ['knowledge_units.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
    op.create_index(op.f('ix_knowledge_units_category_id'), 'knowledge_units', ['category_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_units_code'), 'knowledge_units', ['code'], unique=True, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_units_grade'), 'knowledge_units', ['grade'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_units_parent_id'), 'knowledge_units', ['parent_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_units_slug'), 'knowledge_units', ['slug'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_units_subject'), 'knowledge_units', ['subject'], unique=False, if_not_exists=True)
    if not _has_table('knowledge_articles'):
        op.create_table('knowledge_articles',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('knowledge_unit_id', sa.Integer(), nullable=True),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('summary_formulas_md', sa.Text(), nullable=True),
            sa.Column('method_guide_md', sa.Text(), nullable=True),
            sa.Column('notes_and_tips_md', sa.Text(), nullable=True),
            sa.Column('worked_examples_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('branded_pdf_url', sa.Text(), nullable=True),
            sa.Column('branded_pdf_path', sa.String(length=512), nullable=True),
            sa.Column('access_tier', sa.String(length=10), server_default='FREE', nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['knowledge_unit_id'], ['knowledge_units.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id')
        )
    op.create_index(op.f('ix_knowledge_articles_category_id'), 'knowledge_articles', ['category_id'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_knowledge_articles_knowledge_unit_id'), 'knowledge_articles', ['knowledge_unit_id'], unique=False, if_not_exists=True)
    if not _has_table('target_curriculum_mappings'):
        op.create_table('target_curriculum_mappings',
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.Column('knowledge_unit_id', sa.Integer(), nullable=False),
            sa.Column('allowed_difficulties', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.ForeignKeyConstraint(['knowledge_unit_id'], ['knowledge_units.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['target_id'], ['exam_targets.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('target_id', 'knowledge_unit_id')
        )
    if not _has_table('question_target_tags'):
        op.create_table('question_target_tags',
            sa.Column('question_id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['target_id'], ['exam_targets.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('question_id', 'target_id')
        )
    op.create_index(op.f('ix_question_target_tags_target_id'), 'question_target_tags', ['target_id'], unique=False, if_not_exists=True)
    op.alter_column('ai_cache', 'kind',
        existing_type=sa.VARCHAR(length=16),
        type_=sa.String(length=32),
        existing_nullable=False)
    _add_column('categories', sa.Column('default_tier', sa.Enum('FREE', 'PRO', name='access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False))
    _add_column('exams', sa.Column('origin_type', sa.Enum('PUBLIC_CRAWLED', 'ORIGINAL', name='exam_origin_type', native_enum=False, create_constraint=True, length=20), server_default='PUBLIC_CRAWLED', nullable=False))
    _add_column('exams', sa.Column('access_tier', sa.Enum('FREE', 'PRO', name='exam_access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False))
    _add_column('exams', sa.Column('preview_question_count', sa.Integer(), server_default='5', nullable=False))
    _add_column('exams', sa.Column('is_solution_pro_only', sa.Boolean(), server_default='false', nullable=False))
    op.create_index(op.f('ix_exams_access_tier'), 'exams', ['access_tier'], unique=False, if_not_exists=True)
    _add_column('import_jobs', sa.Column('solution_stored_path', sa.String(length=300), nullable=True))
    _add_column('import_jobs', sa.Column('access_tier', sa.Enum('FREE', 'PRO', name='job_access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False))
    _add_column('import_jobs', sa.Column('origin_type', sa.Enum('PUBLIC_CRAWLED', 'ORIGINAL', name='job_origin_type', native_enum=False, create_constraint=True, length=20), server_default='PUBLIC_CRAWLED', nullable=False))
    _add_column('import_jobs', sa.Column('doc_type', sa.String(length=50), nullable=True))
    _add_column('import_jobs', sa.Column('knowledge_article_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    _add_column('library_documents', sa.Column('preview_storage_key', sa.String(length=300), nullable=True))
    _add_column('library_documents', sa.Column('access_tier', sa.Enum('FREE', 'PRO', name='library_access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False))
    _add_column('library_documents', sa.Column('content_markdown', sa.Text(), nullable=True))
    op.create_index(op.f('ix_library_documents_access_tier'), 'library_documents', ['access_tier'], unique=False, if_not_exists=True)
    _add_column('questions', sa.Column('knowledge_unit_id', sa.Integer(), nullable=True))
    _add_column('questions', sa.Column('is_thinking_skill', sa.Boolean(), server_default='false', nullable=False))
    _add_column('questions', sa.Column('access_tier', sa.Enum('FREE', 'PRO', name='question_access_tier', native_enum=False, create_constraint=True, length=20), server_default='FREE', nullable=False))
    _add_column('questions', sa.Column('origin_type', sa.Enum('PUBLIC', 'AUTHORED', name='question_origin_type', native_enum=False, create_constraint=True, length=20), server_default='PUBLIC', nullable=False))
    _add_column('questions', sa.Column('audit_status', sa.String(length=30), server_default='PENDING', nullable=False))
    _add_column('questions', sa.Column('ai_attempt_count', sa.Integer(), server_default='0', nullable=False))
    _add_column('questions', sa.Column('first_ai_answer', sa.String(length=255), nullable=True))
    _add_column('questions', sa.Column('second_ai_answer', sa.String(length=255), nullable=True))
    _add_column('questions', sa.Column('pending_exam_id', sa.Integer(), nullable=True))
    _add_column('questions', sa.Column('quality_flags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False))
    _add_column('questions', sa.Column('target_tags', postgresql.ARRAY(sa.String(length=30)), server_default='{SGK_KNTT}', nullable=False))
    op.create_index(op.f('ix_questions_access_tier'), 'questions', ['access_tier'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_questions_audit_status'), 'questions', ['audit_status'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_questions_is_thinking_skill'), 'questions', ['is_thinking_skill'], unique=False, if_not_exists=True)
    op.create_index(op.f('ix_questions_knowledge_unit_id'), 'questions', ['knowledge_unit_id'], unique=False, if_not_exists=True)
    op.create_index('ix_questions_target_tags_gin', 'questions', ['target_tags'], unique=False, postgresql_using='gin', if_not_exists=True)
    _add_questions_knowledge_unit_fk()

    # Hand-patched databases: bring names, widths and nullability in line with the models.
    op.execute("DROP INDEX IF EXISTS idx_knowledge_articles_ku_id")  # replaced by ix_knowledge_articles_knowledge_unit_id
    op.execute("ALTER TABLE knowledge_units ALTER COLUMN unit_type TYPE varchar(30)")
    op.execute("ALTER TABLE knowledge_units ALTER COLUMN code TYPE varchar(60)")
    op.execute("ALTER TABLE knowledge_articles ALTER COLUMN knowledge_unit_id DROP NOT NULL")
    op.execute("UPDATE knowledge_articles SET access_tier = 'FREE' WHERE access_tier IS NULL")
    op.execute("UPDATE knowledge_articles SET created_at = now() WHERE created_at IS NULL")
    op.execute("ALTER TABLE knowledge_articles ALTER COLUMN access_tier SET NOT NULL")
    op.execute("ALTER TABLE knowledge_articles ALTER COLUMN created_at SET NOT NULL")
    op.execute("UPDATE questions SET audit_status = 'PENDING' WHERE audit_status IS NULL")
    op.execute("UPDATE questions SET ai_attempt_count = 0 WHERE ai_attempt_count IS NULL")
    op.execute("UPDATE questions SET quality_flags = '[]' WHERE quality_flags IS NULL")
    op.execute("ALTER TABLE questions ALTER COLUMN audit_status SET NOT NULL")
    op.execute("ALTER TABLE questions ALTER COLUMN ai_attempt_count SET NOT NULL")
    op.execute("ALTER TABLE questions ALTER COLUMN quality_flags SET NOT NULL")


def downgrade() -> None:
    # The catch-up only records what already existed outside the migration history; nothing to undo.
    pass
