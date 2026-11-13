"""B04 + B06: taxonomy tree and curriculum mapping tables, hybrid RLS and grants

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-13
"""

from alembic import op

from app.platform.rls import hybrid_read_child, hybrid_read_root, hybrid_write_rls

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

HYBRID_TABLES = (
    "taxonomy_nodes",
    "taxonomy_node_versions",
    "taxonomy_aliases",
    "curriculum_versions",
    "curriculum_nodes",
    "taxonomy_mappings",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE taxonomy_nodes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'
                REFERENCES tenants(id) ON DELETE RESTRICT,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC',
            parent_id UUID REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
            node_type VARCHAR(40) NOT NULL,
            code VARCHAR(120) NOT NULL,
            code_path ltree NOT NULL,
            current_version_no INT NOT NULL DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            retired_into_node_id UUID REFERENCES taxonomy_nodes(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_taxonomy_nodes_visibility_scope
                CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
            CONSTRAINT ck_taxonomy_nodes_node_type
                CHECK (node_type IN ('DOMAIN', 'SUBJECT', 'TOPIC', 'CONCEPT', 'SKILL')),
            CONSTRAINT uq_taxonomy_nodes_tenant_id_code UNIQUE (tenant_id, code),
            CONSTRAINT uq_taxonomy_nodes_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_taxonomy_nodes_ltree_gist ON taxonomy_nodes USING GIST (code_path)")

    # Spec declares `created_by UUID REFERENCES users(id)`, omitted while `users.id` is legacy integer
    # (pre-F04 identity migration). Documented for owner sign-off.
    op.execute(
        """
        CREATE TABLE taxonomy_node_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            taxonomy_node_id UUID NOT NULL,
            version_no INT NOT NULL,
            name VARCHAR(255) NOT NULL,
            description_md TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_taxonomy_node_versions_node_version UNIQUE (taxonomy_node_id, version_no),
            CONSTRAINT fk_taxonomy_node_versions_node_tenant
                FOREIGN KEY (taxonomy_node_id, tenant_id)
                REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE taxonomy_aliases (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            taxonomy_node_id UUID NOT NULL,
            alias_name VARCHAR(255) NOT NULL,
            normalized_alias VARCHAR(255) NOT NULL,
            CONSTRAINT uq_taxonomy_aliases_tenant_norm_alias UNIQUE (tenant_id, normalized_alias),
            CONSTRAINT fk_taxonomy_aliases_node_tenant
                FOREIGN KEY (taxonomy_node_id, tenant_id)
                REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE curriculum_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'
                REFERENCES tenants(id) ON DELETE CASCADE,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC',
            code VARCHAR(80) NOT NULL,
            name VARCHAR(255) NOT NULL,
            subject_code VARCHAR(50) NOT NULL,
            grade_band VARCHAR(30) NOT NULL,
            version_label VARCHAR(40) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'PUBLISHED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_curriculum_versions_visibility_scope
                CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
            CONSTRAINT ck_curriculum_versions_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT uq_curriculum_versions_tenant_code UNIQUE (tenant_id, code),
            CONSTRAINT uq_curriculum_versions_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE curriculum_nodes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            curriculum_version_id UUID NOT NULL,
            parent_id UUID REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
            node_level VARCHAR(40) NOT NULL,
            code VARCHAR(120) NOT NULL,
            title VARCHAR(255) NOT NULL,
            sequence_order INT NOT NULL DEFAULT 0,
            CONSTRAINT ck_curriculum_nodes_node_level
                CHECK (node_level IN ('CHAPTER', 'LESSON', 'SECTION', 'PROBLEM_TYPE')),
            CONSTRAINT uq_curriculum_nodes_version_code UNIQUE (curriculum_version_id, code),
            CONSTRAINT uq_curriculum_nodes_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_curriculum_nodes_version_tenant
                FOREIGN KEY (curriculum_version_id, tenant_id)
                REFERENCES curriculum_versions(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE taxonomy_mappings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            curriculum_node_id UUID NOT NULL,
            taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
            mapping_weight NUMERIC(4, 2) NOT NULL DEFAULT 1.00,
            CONSTRAINT uq_taxonomy_mappings_curriculum_taxonomy
                UNIQUE (curriculum_node_id, taxonomy_node_id),
            CONSTRAINT fk_taxonomy_mappings_curriculum_tenant
                FOREIGN KEY (curriculum_node_id, tenant_id)
                REFERENCES curriculum_nodes(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    rls_statements = (
        *hybrid_write_rls("taxonomy_nodes"),
        *hybrid_read_root("taxonomy_nodes", "visibility_scope = 'SYSTEM_PUBLIC' AND is_active IS TRUE"),
        *hybrid_write_rls("taxonomy_node_versions"),
        *hybrid_read_child("taxonomy_node_versions", "taxonomy_nodes", "taxonomy_node_id"),
        *hybrid_write_rls("taxonomy_aliases"),
        *hybrid_read_child("taxonomy_aliases", "taxonomy_nodes", "taxonomy_node_id"),
        *hybrid_write_rls("curriculum_versions"),
        *hybrid_read_root("curriculum_versions", "visibility_scope = 'SYSTEM_PUBLIC' AND status = 'PUBLISHED'"),
        *hybrid_write_rls("curriculum_nodes"),
        *hybrid_read_child("curriculum_nodes", "curriculum_versions", "curriculum_version_id"),
        *hybrid_write_rls("taxonomy_mappings"),
        *hybrid_read_child("taxonomy_mappings", "curriculum_nodes", "curriculum_node_id"),
    )
    for stmt in rls_statements:
        op.execute(stmt)

    for table in HYBRID_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ulp_app_rw")


def downgrade() -> None:
    for table in reversed(HYBRID_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")

