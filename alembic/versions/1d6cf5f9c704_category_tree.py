"""category tree

Replaces the fixed subjects -> chapters -> lessons hierarchy with a self-referencing `categories` table of
any depth. Subjects become roots, chapters their children, lessons grandchildren. Questions, exams and AI
import jobs point at their most specific category (`category_id`) and their subject (`root_id`). Matrix
rule scopes ("s:/c:/l:<id>") are rewritten to the new category keys ("c:<id>").

Revision ID: 1d6cf5f9c704
Revises: df493bca9d64
Create Date: 2026-11-05 15:04:47.267455

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1d6cf5f9c704"
down_revision: str | Sequence[str] | None = "df493bca9d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("questions", "exams", "import_jobs")


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("slug", sa.String(200), nullable=True, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"])
    op.create_index("ix_categories_path", "categories", ["path"])

    bind = op.get_bind()

    def insert(parent_id, name, slug, description, position, parent_path, depth, created_at):
        new_id = bind.execute(
            sa.text(
                "INSERT INTO categories (parent_id, name, slug, description, position, path, depth, created_at) "
                "VALUES (:p, :n, :s, :d, :pos, '', :dep, :c) RETURNING id"
            ),
            {"p": parent_id, "n": name, "s": slug, "d": description, "pos": position, "dep": depth, "c": created_at},
        ).scalar()
        path = f"{parent_path}{new_id}/"
        bind.execute(sa.text("UPDATE categories SET path = :path WHERE id = :id"), {"path": path, "id": new_id})
        return new_id, path

    subject_map, chapter_map, lesson_map, root_of = {}, {}, {}, {}
    subjects = bind.execute(sa.text("SELECT id, name, slug, description, created_at FROM subjects ORDER BY name")).all()
    for position, (sid, name, slug, description, created_at) in enumerate(subjects, start=1):
        new_id, path = insert(None, name, slug, description, position, "/", 0, created_at)
        subject_map[sid], root_of[new_id] = (new_id, path), new_id
    for cid, sid, name, position, created_at in bind.execute(
        sa.text("SELECT id, subject_id, name, position, created_at FROM chapters ORDER BY subject_id, position, id")
    ).all():
        parent_id, parent_path = subject_map[sid]
        new_id, path = insert(parent_id, name, None, None, position, parent_path, 1, created_at)
        chapter_map[cid], root_of[new_id] = (new_id, path), parent_id
    for lid, cid, name, position, created_at in bind.execute(
        sa.text("SELECT id, chapter_id, name, position, created_at FROM lessons ORDER BY chapter_id, position, id")
    ).all():
        parent_id, parent_path = chapter_map[cid]
        new_id, path = insert(parent_id, name, None, None, position, parent_path, 2, created_at)
        lesson_map[lid], root_of[new_id] = (new_id, path), root_of[parent_id]

    def most_specific(subject_id, chapter_id, lesson_id):
        if lesson_id in lesson_map:
            return lesson_map[lesson_id][0]
        if chapter_id in chapter_map:
            return chapter_map[chapter_id][0]
        return subject_map[subject_id][0]

    for table in TABLES:
        op.add_column(table, sa.Column("root_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("category_id", sa.Integer(), nullable=True))
        rows = bind.execute(sa.text(f"SELECT id, subject_id, chapter_id, lesson_id FROM {table}")).all()
        for row_id, subject_id, chapter_id, lesson_id in rows:
            category_id = most_specific(subject_id, chapter_id, lesson_id)
            bind.execute(
                sa.text(f"UPDATE {table} SET root_id = :r, category_id = :c WHERE id = :id"),
                {"r": root_of[category_id], "c": category_id, "id": row_id},
            )

    # Matrix rules reference scopes by key; map them to the new category ids.
    def new_key(key):
        kind, _, raw = (key or "").partition(":")
        if not raw.isdigit():
            return ""
        old = int(raw)
        table = {"s": subject_map, "c": chapter_map, "l": lesson_map}.get(kind, {})
        return f"c:{table[old][0]}" if old in table else ""

    for exam_id, matrix in bind.execute(sa.text("SELECT id, matrix FROM exams WHERE matrix IS NOT NULL")).all():
        rules = [{**rule, "scope": new_key(rule.get("scope"))} for rule in matrix or []]
        bind.execute(
            sa.text("UPDATE exams SET matrix = CAST(:m AS JSONB) WHERE id = :id"),
            {"m": json.dumps(rules, ensure_ascii=False), "id": exam_id},
        )

    for table in TABLES:
        op.alter_column(table, "root_id", nullable=False)
        op.alter_column(table, "category_id", nullable=False)
        op.create_foreign_key(f"{table}_root_id_fkey", table, "categories", ["root_id"], ["id"], ondelete="RESTRICT")
        op.create_foreign_key(
            f"{table}_category_id_fkey", table, "categories", ["category_id"], ["id"], ondelete="RESTRICT"
        )
        if table != "import_jobs":
            op.create_index(f"ix_{table}_root_id", table, ["root_id"])
            op.create_index(f"ix_{table}_category_id", table, ["category_id"])
        for column in ("lesson_id", "chapter_id", "subject_id"):
            op.drop_column(table, column)

    op.drop_table("lessons")
    op.drop_table("chapters")
    op.drop_table("subjects")


def downgrade() -> None:
    raise NotImplementedError("The category tree cannot be folded back into three fixed levels automatically.")
