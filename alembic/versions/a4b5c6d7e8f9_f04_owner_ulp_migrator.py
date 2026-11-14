"""F04: tables and SECURITY DEFINER functions are owned by ulp_migrator, not by whichever login migrated

The F04 definer functions run with their owner's privileges, and FORCE ROW LEVEL SECURITY applies to the
owner too (hence f04_owner_policy). Left to the migrating login, the owner differs between environments
and the policy names it. Here the owner becomes the fixed role ulp_migrator (NOLOGIN, NOBYPASSRLS), and the
owner policies are re-created for it. The migrating login must be a member of ulp_migrator (a superuser is).

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-11-14
"""

from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None

OWNER_POLICY_TABLES = ("users", "user_pii_encryption_keys", "memberships")
F04_TABLES = (
    "users",
    "user_pii_encryption_keys",
    "erasure_certificates",
    "memberships",
    "parental_consents",
)
F04_FUNCTIONS = (
    "f04_find_user_id(char)",
    "f04_register_user(char, varchar, bytea, varchar, integer, varchar)",
    "f04_store_user_key(uuid, varchar, bytea)",
    "f04_load_user_key(uuid)",
    "f04_shred_user_key(uuid, interval)",
)


def _set_owner_policies(owner_sql: str) -> None:
    for table in OWNER_POLICY_TABLES:
        op.execute(f"DROP POLICY IF EXISTS f04_owner_policy ON {table}")
        op.execute(f"CREATE POLICY f04_owner_policy ON {table} FOR ALL TO {owner_sql} USING (true) WITH CHECK (true)")


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO ulp_migrator")  # definer functions run as the owner
    for table in F04_TABLES:
        op.execute(f"ALTER TABLE {table} OWNER TO ulp_migrator")
    op.execute("ALTER VIEW v_users_status OWNER TO ulp_migrator")
    for function in F04_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO ulp_migrator")
    _set_owner_policies("ulp_migrator")


def downgrade() -> None:
    _set_owner_policies("CURRENT_USER")
    for function in F04_FUNCTIONS:
        op.execute(f"ALTER FUNCTION {function} OWNER TO CURRENT_USER")
    op.execute("ALTER VIEW v_users_status OWNER TO CURRENT_USER")
    for table in F04_TABLES:
        op.execute(f"ALTER TABLE {table} OWNER TO CURRENT_USER")
