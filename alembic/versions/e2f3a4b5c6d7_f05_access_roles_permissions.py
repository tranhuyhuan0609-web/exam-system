"""F05 access: roles, permissions, role_permissions with strict tenant RLS and grants

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (identity group);
roles and role_permissions are in the spec's strict_tenant_tables list. permissions is the global
catalog of permission codes (app.platform.rls.GLOBAL_TABLES): the API role only reads it, codes
arrive through migrations. Seeded with the codes the module specification names.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None

# Permission codes named in chapter 3 (F05) and chapter 5 (A01 step 2). Further codes come with
# the modules that check them.
SEED_PERMISSIONS = (
    ("document:import", "Import exam or study documents into the tenant (A01)"),
    ("exam:publish", "Publish an assessment to learners (C04)"),
    ("question:approve", "Approve a reviewed question version for publication (A09)"),
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE roles (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            code VARCHAR(60) NOT NULL,
            name VARCHAR(120) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_roles_tenant_id_code UNIQUE (tenant_id, code),
            CONSTRAINT uq_roles_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE permissions (
            code VARCHAR(80) PRIMARY KEY,
            description VARCHAR(255) NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE role_permissions (
            role_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            permission_code VARCHAR(80) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
            PRIMARY KEY (role_id, permission_code),
            CONSTRAINT fk_role_permissions_role_tenant
                FOREIGN KEY (role_id, tenant_id) REFERENCES roles(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    for code, description in SEED_PERMISSIONS:
        op.execute(
            f"INSERT INTO permissions (code, description) VALUES ('{code}', '{description}') "
            "ON CONFLICT (code) DO NOTHING"
        )

    for table in ("roles", "role_permissions"):
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute("REVOKE ALL ON roles, permissions, role_permissions FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON roles, role_permissions TO ulp_app_rw")
    op.execute("GRANT SELECT ON permissions TO ulp_app_rw")


def downgrade() -> None:
    for table in ("role_permissions", "permissions", "roles"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
