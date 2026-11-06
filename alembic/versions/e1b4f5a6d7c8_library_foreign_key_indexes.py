"""library: indexes on the columns that reference library_documents (deleting many documents)

Revision ID: e1b4f5a6d7c8
Revises: d9a3e4f5c6b7
Create Date: 2026-11-06 21:45:00

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e1b4f5a6d7c8"
down_revision: Union[str, Sequence[str], None] = "d9a3e4f5c6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = {
    "ix_library_documents_origin_id": ("library_documents", "origin_id"),
    "ix_library_views_document_id": ("library_views", "document_id"),
    "ix_answer_keys_source_document_id": ("answer_keys", "source_document_id"),
}


def upgrade() -> None:
    # Without them every deleted document makes PostgreSQL scan these tables for references
    for name, (table, column) in INDEXES.items():
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, (table, _) in INDEXES.items():
        op.drop_index(name, table)
