"""study_guides_and_consensus_audit

Revision ID: f9a1b2c3d4e5
Revises: a1b2c3d4e5f6
Create Date: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f9a1b2c3d4e5"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Bảng lưu Lý thuyết & Phương pháp giải theo từng Dạng bài (Kiểu VietJack)
    if "knowledge_units" not in existing_tables:
        pass  # fresh database: created by the catch-up revision (b0c1d2e3f4a5), in the shape the models declare
    elif "knowledge_articles" not in existing_tables:
        op.create_table(
            "knowledge_articles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("knowledge_unit_id", sa.Integer(), sa.ForeignKey("knowledge_units.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("method_guide_md", sa.Text(), nullable=True),      # Hộp Phương pháp giải / Cách làm
            sa.Column("summary_formulas_md", sa.Text(), nullable=True),  # Hộp Các công thức cần nhớ
            sa.Column("notes_and_tips_md", sa.Text(), nullable=True),    # Hộp Chú ý / Dấu hiệu nhận biết
            sa.Column("worked_examples_json", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
            sa.Column("branded_pdf_path", sa.String(512), nullable=True),
            sa.Column("access_tier", sa.String(20), server_default="FREE", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    else:
        # Add category_id / branded_pdf_path if missing
        cols = [c["name"] for c in inspector.get_columns("knowledge_articles")]
        if "category_id" not in cols:
            op.add_column("knowledge_articles", sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True))
            op.create_index("ix_knowledge_articles_category_id", "knowledge_articles", ["category_id"])
        if "branded_pdf_path" not in cols:
            op.add_column("knowledge_articles", sa.Column("branded_pdf_path", sa.String(512), nullable=True))

    # 2. Mở rộng bảng questions: Hỗ trợ Cộng dồn nhãn kỳ thi (THPTQG, ĐGTD, HSG) & Kiểm chứng AI 2 vòng
    q_cols = [c["name"] for c in inspector.get_columns("questions")]
    with op.batch_alter_table("questions") as batch_op:
        if "target_tags" not in q_cols:
            batch_op.add_column(
                sa.Column("target_tags", postgresql.ARRAY(sa.String(30)), server_default="{SGK_KNTT}", nullable=False)
            )
        if "audit_status" not in q_cols:
            batch_op.add_column(
                sa.Column("audit_status", sa.String(30), server_default="PENDING", nullable=False)
            )
        if "first_ai_answer" not in q_cols:
            batch_op.add_column(sa.Column("first_ai_answer", sa.String(255), nullable=True))
        if "second_ai_answer" not in q_cols:
            batch_op.add_column(sa.Column("second_ai_answer", sa.String(255), nullable=True))
        if "ai_attempt_count" not in q_cols:
            batch_op.add_column(sa.Column("ai_attempt_count", sa.Integer(), server_default="0", nullable=False))

    # Create GIN index on target_tags if not existing
    q_indexes = [ix["name"] for ix in inspector.get_indexes("questions")]
    if "ix_questions_target_tags_gin" not in q_indexes:
        op.create_index("ix_questions_target_tags_gin", "questions", ["target_tags"], postgresql_using="gin")
    if "ix_questions_audit_status" not in q_indexes:
        op.create_index("ix_questions_audit_status", "questions", ["audit_status"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    q_indexes = [ix["name"] for ix in inspector.get_indexes("questions")]
    if "ix_questions_target_tags_gin" in q_indexes:
        op.drop_index("ix_questions_target_tags_gin", table_name="questions")
    if "ix_questions_audit_status" in q_indexes:
        op.drop_index("ix_questions_audit_status", table_name="questions")
    with op.batch_alter_table("questions") as batch_op:
        batch_op.drop_column("ai_attempt_count")
        batch_op.drop_column("second_ai_answer")
        batch_op.drop_column("first_ai_answer")
        batch_op.drop_column("audit_status")
        batch_op.drop_column("target_tags")
    op.execute("DROP TABLE IF EXISTS knowledge_articles")

