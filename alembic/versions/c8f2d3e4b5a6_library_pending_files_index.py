"""library: index of works whose open PDF is still to fetch

Revision ID: c8f2d3e4b5a6
Revises: b7e1c2d3a4f5
Create Date: 2026-11-06 20:00:00

"""

from typing import Sequence, Union

from alembic import op

revision: str = "c8f2d3e4b5a6"
down_revision: Union[str, Sequence[str], None] = "b7e1c2d3a4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_library_documents_pending_file ON library_documents (id) "
        "WHERE file_url IS NOT NULL AND storage_key = ''"
    )
    op.execute("CREATE INDEX ix_library_documents_kind ON library_documents (kind)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_library_documents_kind")
    op.execute("DROP INDEX IF EXISTS ix_library_documents_pending_file")
