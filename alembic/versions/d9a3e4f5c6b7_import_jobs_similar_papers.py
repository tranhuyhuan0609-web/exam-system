"""import jobs: similar papers for teachers

Revision ID: d9a3e4f5c6b7
Revises: c8f2d3e4b5a6
Create Date: 2026-11-06 21:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9a3e4f5c6b7"
down_revision: Union[str, Sequence[str], None] = "c8f2d3e4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("purpose", sa.String(20), nullable=False, server_default="BANK"))
    op.add_column("import_jobs", sa.Column("versions", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "import_jobs", sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]")
    )


def downgrade() -> None:
    for column in ("variants", "versions", "purpose"):
        op.drop_column("import_jobs", column)
