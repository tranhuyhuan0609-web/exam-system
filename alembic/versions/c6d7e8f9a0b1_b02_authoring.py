"""B02 authoring: content_drafts, content_blocks, comment_threads

As in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql: content_drafts and comment_threads are in
the spec's strict tenant list; content_blocks is in the hybrid list (a tenant reads the system tenant's blocks
only while the parent version is visible, which requires it to be PUBLISHED and frozen).

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import hybrid_read_child, hybrid_write_rls, strict_tenant_rls

revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE content_drafts (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            content_item_id UUID NOT NULL,
            author_id UUID NOT NULL REFERENCES users(id),
            working_blocks_json JSONB NOT NULL DEFAULT '[]',
            validation_errors_json JSONB NOT NULL DEFAULT '[]',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_content_drafts_item_tenant
                FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE content_blocks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            content_version_id UUID NOT NULL,
            block_order INT NOT NULL,
            block_type VARCHAR(40) NOT NULL,
            payload_json JSONB NOT NULL,
            CONSTRAINT ck_content_blocks_block_type CHECK (block_type IN (
                'THEORY_SUMMARY', 'FORMULA_BOX', 'METHOD_STEPS', 'TIP_WARNING',
                'WORKED_EXAMPLE', 'INTERACTIVE_QUESTION_EMBED', 'HTML_TABLE'
            )),
            CONSTRAINT uq_content_blocks_content_version_id_block_order UNIQUE (content_version_id, block_order),
            CONSTRAINT fk_content_blocks_version_tenant
                FOREIGN KEY (content_version_id, tenant_id)
                REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE comment_threads (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            target_type VARCHAR(40) NOT NULL,
            target_id UUID NOT NULL,
            author_id UUID NOT NULL REFERENCES users(id),
            body_text TEXT NOT NULL,
            is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    statements = [
        *strict_tenant_rls("content_drafts"),
        *strict_tenant_rls("comment_threads"),
        *hybrid_write_rls("content_blocks"),
        *hybrid_read_child("content_blocks", "content_versions", "content_version_id"),
    ]
    for stmt in statements:
        op.execute(stmt)
    op.execute("REVOKE ALL ON content_drafts, content_blocks, comment_threads FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON content_drafts, content_blocks, comment_threads TO ulp_app_rw")


def downgrade() -> None:
    for table in ("comment_threads", "content_blocks", "content_drafts"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
