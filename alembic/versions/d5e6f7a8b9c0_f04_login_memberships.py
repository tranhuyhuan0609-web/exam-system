"""F04: f04_login_memberships, one query for the membership a login signs in to

`memberships` is strict tenant RLS, so the login used to look at one tenant after another (up to 200). The function
reads it across tenants in one statement, as Chapter 4 section 2d: SECURITY DEFINER owned by `ulp_migrator` (its
`f04_owner_policy` lets FORCE ROW LEVEL SECURITY through), a pinned search_path, EXECUTE for `ulp_app_rw` only
(Decision #7). It answers with at most one row: the first ACTIVE membership, narrowed by tenant slug when given.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-11-15
"""

from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None

SIGNATURE = "f04_login_memberships(uuid, varchar)"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f04_login_memberships(p_user_id uuid, p_tenant_slug varchar DEFAULT NULL)
        RETURNS TABLE (
            r_tenant_id uuid, r_tenant_slug varchar, r_membership_id uuid, r_roles varchar[],
            r_entitlement_tier varchar
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT t.id, t.slug, m.id, m.roles, m.entitlement_tier
            FROM memberships m JOIN tenants t ON t.id = m.tenant_id
            WHERE m.user_id = p_user_id AND m.status = 'ACTIVE' AND m.left_at IS NULL AND t.status = 'ACTIVE'
              AND (p_tenant_slug IS NULL OR t.slug = p_tenant_slug)
            ORDER BY t.created_at, t.id
            LIMIT 1;
        $$
        """
    )
    op.execute("GRANT SELECT ON tenants TO ulp_migrator")  # the definer reads the tenant's slug and status
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO ulp_migrator")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO ulp_app_rw")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
    op.execute("REVOKE SELECT ON tenants FROM ulp_migrator")
