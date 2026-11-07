"""crawled questions: difficulty graded by AI

Revision ID: f2c5a6b7e8d9
Revises: e1b4f5a6d7c8
Create Date: 2026-11-07 08:30:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c5a6b7e8d9"
down_revision: Union[str, Sequence[str], None] = "e1b4f5a6d7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "crawled_questions", sa.Column("difficulty_checked", sa.Boolean(), nullable=False, server_default="false")
    )


def downgrade() -> None:
    op.drop_column("crawled_questions", "difficulty_checked")
