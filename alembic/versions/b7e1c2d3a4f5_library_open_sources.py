"""library: open sources (papers, open books)

Revision ID: b7e1c2d3a4f5
Revises: 37258f0d17ed
Create Date: 2026-11-06 18:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e1c2d3a4f5"
down_revision: Union[str, Sequence[str], None] = "37258f0d17ed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KINDS = "'BOOK', 'TOPIC', 'EXAM', 'HSG', 'OTHER'"


def upgrade() -> None:
    op.drop_constraint("library_kind", "library_documents", type_="check")
    op.create_check_constraint("library_kind", "library_documents", f"kind IN ({KINDS}, 'PAPER')")
    op.alter_column("library_documents", "source_url", type_=sa.String(1000))
    op.add_column("library_documents", sa.Column("file_url", sa.String(1000)))
    op.add_column("library_documents", sa.Column("authors", sa.String(500)))
    op.add_column("library_documents", sa.Column("year", sa.Integer()))
    op.add_column("library_documents", sa.Column("language", sa.String(10)))
    # Hundreds of thousands of rows: the library lists one folder and searches titles in SQL.
    op.create_index("ix_library_documents_folder_path", "library_documents", ["folder_path"])
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_library_documents_title_trgm ON library_documents USING gin (title gin_trgm_ops)")
    # Links to files that we never stored (a first trial): the harvester takes these works again
    op.execute("DELETE FROM library_documents WHERE source IN ('openalex', 'openlibrary', 'gutendex', 'semanticscholar')")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_library_documents_title_trgm")
    op.drop_index("ix_library_documents_folder_path", "library_documents")
    for column in ("language", "year", "authors", "file_url"):
        op.drop_column("library_documents", column)
    op.execute("DELETE FROM library_documents WHERE kind = 'PAPER'")
    op.alter_column("library_documents", "source_url", type_=sa.String(500))
    op.drop_constraint("library_kind", "library_documents", type_="check")
    op.create_check_constraint("library_kind", "library_documents", f"kind IN ({KINDS})")
