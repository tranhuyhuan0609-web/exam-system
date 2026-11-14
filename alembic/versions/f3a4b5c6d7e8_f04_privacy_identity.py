"""F04 privacy: users -> legacy_users, V11.3 identity tables, consent, crypto-shredding, RLS and grants

1. Decision #2: the pre-V11.3 `users` table becomes `legacy_users` (Expand step). Its key, unique
   constraint and sequence are renamed too so the new `users` can use the plain names; foreign keys of
   the legacy tables follow the table (they point at its oid).
2. Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (identity group): users,
   user_pii_encryption_keys, erasure_certificates, memberships, parental_consents, the STABLE function
   is_user_minor and the security_invoker view v_users_status.
3. Decision #7:
   * memberships, parental_consents, erasure_certificates: strict tenant RLS (spec list).
   * users: RLS; a tenant sees its members and the signed-in user (app.current_user_id, bound per
     transaction like app.current_tenant_id). Registration and login look-ups go through narrow
     SECURITY DEFINER functions (f04_register_user, f04_find_user_id).
   * user_pii_encryption_keys: no runtime role has any privilege; only the F04 SECURITY DEFINER
     functions (f04_store_user_key, f04_load_user_key, f04_shred_user_key) read or write it.
   The definer functions run as the table owner. FORCE ROW LEVEL SECURITY applies to the owner too, so
   each of these tables gets an owner-only policy (f04_owner_policy); runtime roles never own tables.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import invoker_view, strict_tenant_rls

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None

STRICT_TABLES = ("memberships", "parental_consents", "erasure_certificates")
DEFINER_FUNCTIONS = (
    "f04_find_user_id(char)",
    "f04_register_user(char, varchar, bytea, varchar, integer, varchar)",
    "f04_store_user_key(uuid, varchar, bytea)",
    "f04_load_user_key(uuid)",
    "f04_shred_user_key(uuid, interval)",
)

# A tenant may act on a user it has a membership with, or on the signed-in user. Inside a SECURITY
# DEFINER function the owner's policy lets everything through, so the tenant is filtered explicitly.
# COALESCE: with no user bound the comparison is NULL, and `IF NOT NULL` would not raise (fail open).
_CALLER_SEES_USER = """(
    COALESCE(p_user_id = current_app_user_id(), false)
    OR EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = p_user_id AND m.tenant_id = current_app_tenant_id())
)"""


def _owner_policy(table: str) -> str:
    return f"""
        DO $$
        BEGIN
            EXECUTE format(
                'CREATE POLICY f04_owner_policy ON {table} FOR ALL TO %I USING (true) WITH CHECK (true)',
                (SELECT tableowner FROM pg_tables WHERE schemaname = 'public' AND tablename = '{table}')
            );
        END $$;
    """


def upgrade() -> None:
    # --- 1. legacy table out of the way ----------------------------------------------------------
    op.execute("ALTER TABLE users RENAME TO legacy_users")
    op.execute("ALTER TABLE legacy_users RENAME CONSTRAINT users_pkey TO legacy_users_pkey")
    op.execute("ALTER TABLE legacy_users RENAME CONSTRAINT users_email_key TO legacy_users_email_key")
    op.execute("ALTER SEQUENCE users_id_seq RENAME TO legacy_users_id_seq")

    # --- 2. identity tables ----------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_app_user_id() RETURNS uuid AS $$
            SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid;
        $$ LANGUAGE sql STABLE;
        """
    )
    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            email_blind_index CHAR(64) NOT NULL,
            password_hash VARCHAR(255),
            pii_ciphertext_bytea BYTEA,
            pseudonym_handle VARCHAR(80) NOT NULL DEFAULT 'Learner',
            birth_year INT,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_users_email_blind_index UNIQUE (email_blind_index),
            CONSTRAINT ck_users_birth_year
                CHECK (birth_year IS NULL OR (birth_year >= 1920 AND birth_year <= 2100)),
            CONSTRAINT ck_users_status
                CHECK (status IN ('ACTIVE', 'LOCKED', 'PENDING_PARENTAL_CONSENT', 'ANONYMIZED'))
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION is_user_minor(p_birth_year INT, p_ref_date DATE DEFAULT CURRENT_DATE)
        RETURNS BOOLEAN AS $$
            SELECT p_birth_year IS NOT NULL AND (EXTRACT(YEAR FROM p_ref_date)::INT - p_birth_year) < 16;
        $$ LANGUAGE sql STABLE PARALLEL SAFE;
        """
    )
    op.execute(
        invoker_view(
            "v_users_status",
            "SELECT id, email_blind_index, pseudonym_handle, birth_year, "
            "is_user_minor(birth_year, CURRENT_DATE) AS is_minor, status, created_at FROM users",
        )
    )
    op.execute(
        """
        CREATE TABLE user_pii_encryption_keys (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            kms_kek_key_id VARCHAR(255) NOT NULL,
            encrypted_dek_bytea BYTEA,
            key_algorithm VARCHAR(30) NOT NULL DEFAULT 'AES_256_GCM_ENVELOPE',
            key_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            shredded_at TIMESTAMPTZ,
            backup_retention_expires_at TIMESTAMPTZ,
            CONSTRAINT ck_user_pii_encryption_keys_key_status CHECK (key_status IN ('ACTIVE', 'SHREDDED'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE erasure_certificates (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            subject_user_id UUID NOT NULL,
            kms_kek_key_id VARCHAR(255) NOT NULL,
            primary_db_shredded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            kms_kek_revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            backup_expiry_guaranteed_at TIMESTAMPTZ NOT NULL,
            verification_hash CHAR(64) NOT NULL,
            CONSTRAINT uq_erasure_certificates_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE memberships (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            roles VARCHAR(50)[] NOT NULL DEFAULT '{LEARNER}',
            entitlement_tier VARCHAR(30) NOT NULL DEFAULT 'FREE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            left_at TIMESTAMPTZ,
            CONSTRAINT ck_memberships_status CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
            CONSTRAINT ck_memberships_entitlement_tier
                CHECK (entitlement_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO', 'SCHOOL_ENTERPRISE')),
            CONSTRAINT uq_memberships_tenant_id_user_id UNIQUE (tenant_id, user_id),
            CONSTRAINT uq_memberships_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE parental_consents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            learner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            guardian_email_blind_index CHAR(64) NOT NULL,
            guardian_email_ciphertext BYTEA NOT NULL,
            guardian_relationship VARCHAR(40) NOT NULL,
            consent_scope_json JSONB NOT NULL DEFAULT '{"learning_history": true, "ai_tutor": true}',
            verification_method VARCHAR(40) NOT NULL,
            consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ,
            CONSTRAINT ck_parental_consents_guardian_relationship
                CHECK (guardian_relationship IN ('PARENT', 'LEGAL_GUARDIAN', 'SCHOOL_CONTRACT')),
            CONSTRAINT uq_parental_consents_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )

    # --- 3. row-level security -------------------------------------------------------------------
    for table in STRICT_TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    visible_user = """(
        id = (SELECT current_app_user_id())
        OR EXISTS (
            SELECT 1 FROM memberships m
            WHERE m.user_id = users.id AND m.tenant_id = (SELECT current_app_tenant_id())
        )
    )"""
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY users_member_read_policy ON users FOR SELECT USING {visible_user}")
    op.execute(
        f"CREATE POLICY users_member_update_policy ON users FOR UPDATE USING {visible_user} WITH CHECK {visible_user}"
    )
    op.execute("ALTER TABLE user_pii_encryption_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_pii_encryption_keys FORCE ROW LEVEL SECURITY")
    for table in ("users", "user_pii_encryption_keys", "memberships"):
        op.execute(_owner_policy(table))

    # --- 4. the F04 doors: narrow SECURITY DEFINER functions ---------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f04_find_user_id(p_email_blind_index CHAR(64)) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
            SELECT id FROM users WHERE email_blind_index = p_email_blind_index;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f04_register_user(
            p_email_blind_index CHAR(64), p_password_hash VARCHAR, p_pii BYTEA,
            p_pseudonym VARCHAR, p_birth_year INTEGER, p_status VARCHAR
        ) RETURNS TABLE (user_id uuid, created boolean)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            v_id uuid;
        BEGIN
            IF current_app_tenant_id() IS NULL THEN
                RAISE EXCEPTION 'f04_register_user needs a tenant context' USING ERRCODE = '42501';
            END IF;
            INSERT INTO users (email_blind_index, password_hash, pii_ciphertext_bytea, pseudonym_handle, birth_year, status)
            VALUES (p_email_blind_index, p_password_hash, p_pii, COALESCE(p_pseudonym, 'Learner'), p_birth_year,
                    COALESCE(p_status, 'ACTIVE'))
            ON CONFLICT (email_blind_index) DO NOTHING
            RETURNING id INTO v_id;
            IF v_id IS NOT NULL THEN
                RETURN QUERY SELECT v_id, true;
            ELSE
                RETURN QUERY SELECT u.id, false FROM users u WHERE u.email_blind_index = p_email_blind_index;
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION f04_store_user_key(p_user_id uuid, p_kms_kek_key_id VARCHAR, p_encrypted_dek BYTEA)
        RETURNS void
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            IF NOT {_CALLER_SEES_USER} THEN
                RAISE EXCEPTION 'user is not visible to this tenant' USING ERRCODE = '42501';
            END IF;
            INSERT INTO user_pii_encryption_keys (user_id, kms_kek_key_id, encrypted_dek_bytea)
            VALUES (p_user_id, p_kms_kek_key_id, p_encrypted_dek);
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION f04_load_user_key(p_user_id uuid)
        RETURNS TABLE (kms_kek_key_id VARCHAR, encrypted_dek_bytea BYTEA, key_status VARCHAR)
        LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            IF NOT {_CALLER_SEES_USER} THEN
                RAISE EXCEPTION 'user is not visible to this tenant' USING ERRCODE = '42501';
            END IF;
            RETURN QUERY
                SELECT k.kms_kek_key_id, k.encrypted_dek_bytea, k.key_status
                FROM user_pii_encryption_keys k WHERE k.user_id = p_user_id;
        END;
        $$;
        """
    )
    # Erasure is global (a user spans tenants): a tenant may erase a user it shares with no other tenant;
    # the signed-in user may always erase themself. The blind index is replaced by random bytes so the
    # HMAC of the e-mail does not survive either.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION f04_shred_user_key(p_user_id uuid, p_backup_retention INTERVAL)
        RETURNS TABLE (kms_kek_key_id VARCHAR, shredded_at TIMESTAMPTZ, backup_retention_expires_at TIMESTAMPTZ)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        BEGIN
            IF NOT {_CALLER_SEES_USER} THEN
                RAISE EXCEPTION 'user is not visible to this tenant' USING ERRCODE = '42501';
            END IF;
            IF p_user_id IS DISTINCT FROM current_app_user_id() AND EXISTS (
                SELECT 1 FROM memberships m
                WHERE m.user_id = p_user_id AND m.tenant_id <> current_app_tenant_id()
            ) THEN
                RAISE EXCEPTION 'user also belongs to another tenant' USING ERRCODE = '42501';
            END IF;
            UPDATE user_pii_encryption_keys k
               SET encrypted_dek_bytea = NULL,
                   key_status = 'SHREDDED',
                   shredded_at = COALESCE(k.shredded_at, NOW()),
                   backup_retention_expires_at = COALESCE(k.backup_retention_expires_at, NOW() + p_backup_retention)
             WHERE k.user_id = p_user_id;
            UPDATE users u
               SET pii_ciphertext_bytea = NULL,
                   password_hash = NULL,
                   pseudonym_handle = 'Anonymized',
                   status = 'ANONYMIZED',
                   email_blind_index = encode(gen_random_bytes(32), 'hex')
             WHERE u.id = p_user_id AND u.status <> 'ANONYMIZED';
            RETURN QUERY
                SELECT k.kms_kek_key_id, k.shredded_at, k.backup_retention_expires_at
                FROM user_pii_encryption_keys k WHERE k.user_id = p_user_id;
        END;
        $$;
        """
    )

    # --- 5. privileges ---------------------------------------------------------------------------
    op.execute(
        "REVOKE ALL ON users, user_pii_encryption_keys, erasure_certificates, memberships, parental_consents, "
        "v_users_status FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, "
        "ulp_system_scheduler, ulp_analytics_ro"
    )
    op.execute("GRANT SELECT ON users, v_users_status TO ulp_app_rw")
    op.execute(
        "GRANT UPDATE (password_hash, pii_ciphertext_bytea, pseudonym_handle, birth_year, status) ON users TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON memberships, parental_consents TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT ON erasure_certificates TO ulp_app_rw")  # a certificate is never edited
    op.execute("GRANT SELECT ON memberships TO ulp_exam_session_rw, ulp_worker_grading")  # section 5b, 5c
    for function in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO ulp_app_rw")


def downgrade() -> None:
    for function in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute("DROP VIEW IF EXISTS v_users_status")
    # The users policies read memberships: drop them before the tables.
    op.execute("DROP POLICY IF EXISTS users_member_read_policy ON users")
    op.execute("DROP POLICY IF EXISTS users_member_update_policy ON users")
    for table in ("parental_consents", "memberships", "erasure_certificates", "user_pii_encryption_keys", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS is_user_minor(INT, DATE)")
    op.execute("DROP FUNCTION IF EXISTS current_app_user_id()")
    op.execute("ALTER SEQUENCE legacy_users_id_seq RENAME TO users_id_seq")
    op.execute("ALTER TABLE legacy_users RENAME CONSTRAINT legacy_users_email_key TO users_email_key")
    op.execute("ALTER TABLE legacy_users RENAME CONSTRAINT legacy_users_pkey TO users_pkey")
    op.execute("ALTER TABLE legacy_users RENAME TO users")
