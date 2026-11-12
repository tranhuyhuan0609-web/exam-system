"""question_categories many-to-many and question reliability flags

Revision ID: a1b2c3d4e5f6
Revises: f2c5a6b7e8d9
Create Date: 2026-11-07 16:30:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f2c5a6b7e8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add question reliability columns
    op.add_column(
        "questions",
        sa.Column("answer_source", sa.String(50), nullable=True, server_default="single_source"),
    )
    op.add_column(
        "questions",
        sa.Column("explanation_by_ai", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Create question_categories association table
    op.create_table(
        "question_categories",
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_question_categories_category_id", "question_categories", ["category_id"])

    # 3. Copy existing category mappings into question_categories
    op.execute("""
        INSERT INTO question_categories (question_id, category_id)
        SELECT id, category_id FROM questions
        WHERE category_id IS NOT NULL
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_index("ix_question_categories_category_id", table_name="question_categories")
    op.drop_table("question_categories")
    op.drop_column("questions", "explanation_by_ai")
    op.drop_column("questions", "answer_source")

