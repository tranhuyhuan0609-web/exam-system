PHẦN II (TẬP 2 / 3 — V11.3): THIẾT KẾ DATABASE VẬT LÝ TOÀN DIỆN (DDL + RLS + TRIGGERS), PYDANTIC/EVENT CONTRACTS & 9 THUẬT TOÁN LÕI (BAO GỒM WEB CRAWLER & GOOGLE DRIVE SYNC)
CHƯƠNG 4. PHYSICAL POSTGRESQL DDL TOÀN DIỆN (FULL SCHEMA, COMPOSITE FK TENANCY, RLS & IMMUTABILITY TRIGGERS)
Chỉ đạo triển khai Alembic (Khắc phục lỗi P1 trong Review): Không đặt file .sql thô đơn lẻ trong alembic/versions/. Đội Backend sử dụng các file migration Python chuẩn (001_foundation_and_rls.py đến 004_ai_lms_and_ops.py) gọi op.execute(...) chứa kịch bản SQL chuẩn hóa dưới đây. Toàn bộ bảng đều có:
1. Khóa chính UUIDv7 (uuid_generate_v7()) tự sinh theo thứ tự thời gian.
2. Cột tenant_id hiện diện trên mọi bảng nghiệp vụ (bao gồm cả 22 bảng con thông qua Composite Foreign Key (parent_id, tenant_id)) để áp dụng Row-Level Security (RLS) trực tiếp không cần JOIN.
3. Ràng buộc CHECK cho toàn bộ các cột trạng thái (5 trục trạng thái) và Trigger bảo vệ tính bất biến.
Khối mã thực thi (SQL):
-- ============================================================================
-- MIGRATION 001: EXTENSIONS, UUIDv7, IMMUTABLE UNACCENT, ROLES & IDENTITY/TENANCY
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "unaccent";
CREATE EXTENSION IF NOT EXISTS "ltree";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 0. Khởi tạo 7 DB Roles chuẩn Least-Privilege (Khắc phục AUD-004: XÓA BỎ HOÀN TOÀN BYPASSRLS trên mọi Runtime/Worker Role)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_migrator') THEN
        CREATE ROLE ulp_migrator NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_app_rw') THEN
        CREATE ROLE ulp_app_rw NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_exam_session_rw') THEN
        CREATE ROLE ulp_exam_session_rw NOLOGIN NOBYPASSRLS;
    END IF;
    -- Tách Worker theo nhiệm vụ, tất cả đều NOBYPASSRLS và bắt buộc SET LOCAL app.current_tenant_id theo từng Job/Event
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_worker_ingestion') THEN
        CREATE ROLE ulp_worker_ingestion NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_worker_grading') THEN
        CREATE ROLE ulp_worker_grading NOLOGIN NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_analytics_ro') THEN
        CREATE ROLE ulp_analytics_ro NOLOGIN NOBYPASSRLS;
    END IF;
    -- Role chuyên trách các tác vụ nền xuyên Tenant (outbox_relay, attempt_expiry, metric_rollup) dùng policy hẹp theo app.system_job (Khắc phục P0-2)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ulp_system_scheduler') THEN
        CREATE ROLE ulp_system_scheduler NOLOGIN NOBYPASSRLS;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION app_system_job() RETURNS text AS $$
    SELECT NULLIF(current_setting('app.system_job', true), '');
$$ LANGUAGE sql STABLE;

-- 1. Hàm sinh UUIDv7 (Tự động ưu tiên hàm C gốc uuidv7() nếu chạy trên PostgreSQL 18+, fallback sang PL/pgSQL trên PG 16/17)
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
DECLARE
    unix_ts_ms bytea;
    uuid_bytes bytea;
    v_native_uuid uuid;
BEGIN
    -- Nếu chạy trên PostgreSQL 18+, gọi trực tiếp hàm C gốc pg_catalog.uuidv7()
    IF to_regproc('pg_catalog.uuidv7') IS NOT NULL THEN
        EXECUTE 'SELECT pg_catalog.uuidv7()' INTO v_native_uuid;
        RETURN v_native_uuid;
    END IF;
    -- Fallback tương thích 100% trên PostgreSQL 16 & 17
    unix_ts_ms := substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3 FOR 6);
    uuid_bytes := unix_ts_ms || gen_random_bytes(10);
    uuid_bytes := set_byte(uuid_bytes, 6, (b'0111' || get_byte(uuid_bytes, 6)::bit(4))::bit(8)::int);
    uuid_bytes := set_byte(uuid_bytes, 8, (b'10' || get_byte(uuid_bytes, 8)::bit(6))::bit(8)::int);
    RETURN encode(uuid_bytes, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- 2. Quản lý Phiên bản Từ điển FTS & Hàm f_unaccent Ghim Phiên bản (Khắc phục AUD-005)
CREATE TABLE fts_dictionary_versions (
    dict_name VARCHAR(80) PRIMARY KEY,
    rules_sha256 CHAR(64) NOT NULL,
    func_version INT NOT NULL DEFAULT 1,
    last_reindexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO fts_dictionary_versions (dict_name, rules_sha256, func_version)
VALUES ('public.unaccent', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 1)
ON CONFLICT (dict_name) DO NOTHING;

-- Chỉ đánh dấu IMMUTABLE khi đi kèm kiểm soát phiên bản rules_sha256 trong fts_dictionary_versions;
-- Khi nâng cấp từ điển PostgreSQL, migration bắt buộc cập nhật func_version và chạy REINDEX INDEX CONCURRENTLY.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text AS $$
    SELECT public.unaccent('public.unaccent', $1);
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;

-- 3. Hàm đọc Context Đa thuê bao an toàn với PgBouncer Transaction Pooling
CREATE OR REPLACE FUNCTION current_app_tenant_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION system_public_tenant_id() RETURNS uuid AS $$
    SELECT '00000000-0000-0000-0000-000000000000'::uuid;
$$ LANGUAGE sql IMMUTABLE;

-- ============================================================================
-- NHÓM 1: IDENTITY, TENANCY, PARENTAL CONSENT (Luật BVDLCN 91/2025/QH15 & NĐ 13/2023) & RBAC/ENTITLEMENTS (F01, F04, F05)
-- ============================================================================
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    slug VARCHAR(80) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')),
    settings_json JSONB NOT NULL DEFAULT '{}',
    daily_ai_budget_usd NUMERIC(10,2) NOT NULL DEFAULT 10.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Khởi tạo Tenant Hệ thống (Chứa SGK Kết nối tri thức & Học liệu Công khai dùng chung)
INSERT INTO tenants (id, slug, name, status)
VALUES ('00000000-0000-0000-0000-000000000000', 'system-public', 'System Public Knowledge Commons', 'ACTIVE')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    -- Không lưu email/họ tên dạng rõ để đảm bảo Crypto-Shredding thực chất:
    -- email_blind_index = HMAC_SHA256(lower(email), cluster_pepper) phục vụ tra cứu đăng nhập O(1)
    email_blind_index CHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    -- pii_ciphertext_bytea chứa JSON {"email": ..., "display_name": ..., "phone": ...} mã hóa AES-256-GCM bằng DEK riêng của user_id
    pii_ciphertext_bytea BYTEA,
    pseudonym_handle VARCHAR(80) NOT NULL DEFAULT 'Learner',
    birth_year INT CHECK (birth_year IS NULL OR (birth_year >= 1920 AND birth_year <= 2100)),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'LOCKED', 'PENDING_PARENTAL_CONSENT', 'ANONYMIZED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hàm STABLE tính trạng thái vị thành niên (< 16 tuổi) tại thời điểm truy vấn
CREATE OR REPLACE FUNCTION is_user_minor(p_birth_year INT, p_ref_date DATE DEFAULT CURRENT_DATE) RETURNS BOOLEAN AS $$
    SELECT p_birth_year IS NOT NULL AND (EXTRACT(YEAR FROM p_ref_date)::INT - p_birth_year) < 16;
$$ LANGUAGE sql STABLE PARALLEL SAFE;

-- View v_users_status cung cấp trạng thái is_minor tính động tại thời điểm truy vấn
CREATE OR REPLACE VIEW v_users_status AS
SELECT
    id,
    email_blind_index,
    pseudonym_handle,
    birth_year,
    is_user_minor(birth_year, CURRENT_DATE) AS is_minor,
    status,
    created_at
FROM users;

-- Bảng quản lý khóa mã hóa PII theo mô hình KMS Envelope Encryption (Khắc phục AUD-006: Bảo vệ cả trên DB Backup)
CREATE TABLE user_pii_encryption_keys (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kms_kek_key_id VARCHAR(255) NOT NULL, -- Khóa chủ KEK nằm trên KMS/Vault NGOÀI PostgreSQL và ngoài bản Backup DB
    encrypted_dek_bytea BYTEA,            -- DEK đã được bọc bởi KEK; khi xóa dữ liệu: hủy KEK trên KMS + đặt NULL tại đây
    key_algorithm VARCHAR(30) NOT NULL DEFAULT 'AES_256_GCM_ENVELOPE',
    key_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (key_status IN ('ACTIVE', 'SHREDDED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shredded_at TIMESTAMPTZ,
    backup_retention_expires_at TIMESTAMPTZ
);

-- Bảng chứng nhận hoàn tất xóa dữ liệu cá nhân (Right to Erasure Completion Certificate)
CREATE TABLE erasure_certificates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    subject_user_id UUID NOT NULL,
    kms_kek_key_id VARCHAR(255) NOT NULL,
    primary_db_shredded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kms_kek_revoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    backup_expiry_guaranteed_at TIMESTAMPTZ NOT NULL,
    verification_hash CHAR(64) NOT NULL,
    UNIQUE (id, tenant_id)
);

CREATE TABLE memberships (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    roles VARCHAR(50)[] NOT NULL DEFAULT '{LEARNER}',
    entitlement_tier VARCHAR(30) NOT NULL DEFAULT 'FREE'
        CHECK (entitlement_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO', 'SCHOOL_ENTERPRISE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    UNIQUE (tenant_id, user_id),
    UNIQUE (id, tenant_id)
);

-- Bảng xác nhận đồng ý của phụ huynh/nhà trường theo Luật BVDLCN 91/2025/QH15 & NĐ 13/2023/NĐ-CP
CREATE TABLE parental_consents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    learner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Khắc phục P0-4: Không lưu guardian_email dạng rõ; lưu blind_index (HMAC-SHA256) và ciphertext (mã hóa bằng DEK của learner_user_id)
    guardian_email_blind_index CHAR(64) NOT NULL,
    guardian_email_ciphertext BYTEA NOT NULL,
    guardian_relationship VARCHAR(40) NOT NULL CHECK (guardian_relationship IN ('PARENT', 'LEGAL_GUARDIAN', 'SCHOOL_CONTRACT')),
    consent_scope_json JSONB NOT NULL DEFAULT '{"learning_history": true, "ai_tutor": true}',
    verification_method VARCHAR(40) NOT NULL, -- OTP_EMAIL, SIGNED_SCHOOL_AGREEMENT
    consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (id, tenant_id)
);

CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    code VARCHAR(60) NOT NULL,
    name VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);

CREATE TABLE permissions (
    code VARCHAR(80) PRIMARY KEY,
    description VARCHAR(255) NOT NULL
);

CREATE TABLE role_permissions (
    role_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    permission_code VARCHAR(80) NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_code),
    FOREIGN KEY (role_id, tenant_id) REFERENCES roles(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE groups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE classes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    group_id UUID,
    teacher_membership_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    academic_year VARCHAR(30),
    invite_code VARCHAR(30) UNIQUE,
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (group_id, tenant_id) REFERENCES groups(id, tenant_id) ON DELETE SET NULL,
    FOREIGN KEY (teacher_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE class_members (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    class_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    role_in_class VARCHAR(30) NOT NULL DEFAULT 'STUDENT'
        CHECK (role_in_class IN ('STUDENT', 'TEACHING_ASSISTANT')),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'INACTIVE')), -- Soft-delete bảo toàn điểm lịch sử
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,
    UNIQUE (class_id, membership_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

-- ============================================================================
-- NHÓM 2: TAXONOMY (TÁCH ĐỊNH DANH & PHIÊN BẢN + LTREE), KNOWLEDGE GRAPH & CURRICULUM (B04, B05, B06)
-- ============================================================================
CREATE TABLE taxonomy_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    parent_id UUID REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    node_type VARCHAR(40) NOT NULL
        CHECK (node_type IN ('DOMAIN', 'SUBJECT', 'TOPIC', 'CONCEPT', 'SKILL')),
    code VARCHAR(120) NOT NULL,
    code_path ltree NOT NULL, -- VD: 'MATH.PROB.BAYES'
    current_version_no INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    retired_into_node_id UUID REFERENCES taxonomy_nodes(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_taxonomy_nodes_ltree_gist ON taxonomy_nodes USING GIST (code_path);

CREATE TABLE taxonomy_node_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL,
    version_no INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    description_md TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (taxonomy_node_id, version_no),
    FOREIGN KEY (taxonomy_node_id, tenant_id) REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE taxonomy_aliases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL,
    alias_name VARCHAR(255) NOT NULL,
    normalized_alias VARCHAR(255) NOT NULL,
    UNIQUE (tenant_id, normalized_alias),
    FOREIGN KEY (taxonomy_node_id, tenant_id) REFERENCES taxonomy_nodes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE knowledge_edges (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    target_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
    edge_type VARCHAR(40) NOT NULL
        CHECK (edge_type IN ('PREREQUISITE', 'RELATED_TO', 'EXTENDS', 'APPLIED_IN', 'COMMON_MISCONCEPTION')),
    confidence NUMERIC(4,3) NOT NULL DEFAULT 1.000 CHECK (confidence BETWEEN 0 AND 1),
    graph_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, source_node_id, target_node_id, edge_type, graph_version),
    CHECK (source_node_id <> target_node_id)
);

CREATE TABLE graph_validation_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    graph_version INT NOT NULL,
    is_dag_valid BOOLEAN NOT NULL,
    cycle_path_json JSONB NOT NULL DEFAULT '[]',
    validated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE curriculum_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000' REFERENCES tenants(id) ON DELETE CASCADE,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'SYSTEM_PUBLIC'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
    code VARCHAR(80) NOT NULL,
    name VARCHAR(255) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    grade_band VARCHAR(30) NOT NULL,
    version_label VARCHAR(40) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PUBLISHED'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, code),
    UNIQUE (id, tenant_id)
);

CREATE TABLE curriculum_nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    curriculum_version_id UUID NOT NULL,
    parent_id UUID REFERENCES curriculum_nodes(id) ON DELETE CASCADE,
    node_level VARCHAR(40) NOT NULL
        CHECK (node_level IN ('CHAPTER', 'LESSON', 'SECTION', 'PROBLEM_TYPE')),
    code VARCHAR(120) NOT NULL,
    title VARCHAR(255) NOT NULL,
    sequence_order INT NOT NULL DEFAULT 0,
    UNIQUE (curriculum_version_id, code),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (curriculum_version_id, tenant_id) REFERENCES curriculum_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE taxonomy_mappings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    curriculum_node_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
    mapping_weight NUMERIC(4,2) NOT NULL DEFAULT 1.00,
    UNIQUE (curriculum_node_id, taxonomy_node_id),
    FOREIGN KEY (curriculum_node_id, tenant_id) REFERENCES curriculum_nodes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 3: ASSETS, CONTENT LIBRARY, STUDIO & SEARCH (B01, B02, B03, B07)
-- ============================================================================
CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    parent_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    asset_role VARCHAR(40) NOT NULL
        CHECK (asset_role IN ('RAW_SOURCE', 'BRANDED_PDF', 'PREVIEW_PDF', 'PAGE_RENDER', 'FIGURE_CROP', 'AUDIO', 'THUMBNAIL')),
    object_key VARCHAR(512) UNIQUE NOT NULL,
    mime_type VARCHAR(120) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    is_immutable_bronze BOOLEAN GENERATED ALWAYS AS (asset_role = 'RAW_SOURCE') STORED,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_assets_tenant_sha256 ON assets (tenant_id, sha256);

CREATE TABLE content_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    content_type VARCHAR(40) NOT NULL
        CHECK (content_type IN ('ARTICLE', 'LESSON', 'VIDEO', 'DOCUMENT', 'FLASHCARD_SET', 'VOCABULARY_SET')),
    slug VARCHAR(255) NOT NULL,
    curriculum_node_id UUID REFERENCES curriculum_nodes(id) ON DELETE SET NULL,
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id) ON DELETE SET NULL,
    current_version_id UUID,
    -- Đủ 4 trục trạng thái trên thực thể học liệu (Fail-Closed Default)
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, slug),
    UNIQUE (id, tenant_id),
    -- Cổng kiểm soát cứng: Không thể PUBLISHED nếu chưa VERIFIED hoặc bản quyền không hợp lệ
    CONSTRAINT chk_content_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);

CREATE TABLE content_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_item_id UUID NOT NULL,
    version_no INT NOT NULL,
    title VARCHAR(500) NOT NULL,
    summary_md TEXT,
    blocks_json JSONB NOT NULL DEFAULT '[]',
    branded_asset_id UUID,
    preview_asset_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED', 'TAKEN_DOWN')),
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (content_item_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (branded_asset_id, tenant_id) REFERENCES assets(id, tenant_id),
    FOREIGN KEY (preview_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
);

ALTER TABLE content_items
    ADD CONSTRAINT fk_content_items_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES content_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE content_drafts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_item_id UUID NOT NULL,
    author_id UUID NOT NULL REFERENCES users(id),
    working_blocks_json JSONB NOT NULL DEFAULT '[]',
    validation_errors_json JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE content_blocks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    content_version_id UUID NOT NULL,
    block_order INT NOT NULL,
    block_type VARCHAR(40) NOT NULL
        CHECK (block_type IN ('THEORY_SUMMARY', 'FORMULA_BOX', 'METHOD_STEPS', 'TIP_WARNING', 'WORKED_EXAMPLE', 'INTERACTIVE_QUESTION_EMBED', 'HTML_TABLE')),
    payload_json JSONB NOT NULL,
    UNIQUE (content_version_id, block_order),
    FOREIGN KEY (content_version_id, tenant_id) REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE comment_threads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    target_type VARCHAR(40) NOT NULL,
    target_id UUID NOT NULL,
    author_id UUID NOT NULL REFERENCES users(id),
    body_text TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE content_assets (
    content_version_id UUID NOT NULL,
    asset_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    PRIMARY KEY (content_version_id, asset_id),
    FOREIGN KEY (content_version_id, tenant_id) REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE publications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_type VARCHAR(40) NOT NULL CHECK (entity_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
    entity_id UUID NOT NULL,
    version_id UUID NOT NULL,
    published_by UUID NOT NULL REFERENCES users(id),
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE access_grants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    membership_id UUID NOT NULL,
    resource_type VARCHAR(40) NOT NULL,
    resource_id UUID NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE search_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
    doc_type VARCHAR(30) NOT NULL CHECK (doc_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
    entity_id UUID NOT NULL,
    title TEXT NOT NULL,
    body_plain TEXT NOT NULL,
    subject_code VARCHAR(50),
    grade_band VARCHAR(30),
    taxonomy_path ltree,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', f_unaccent(coalesce(title, ''))), 'A') ||
        setweight(to_tsvector('simple', f_unaccent(coalesce(body_plain, ''))), 'B')
    ) STORED,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, doc_type, entity_id)
);
CREATE INDEX ix_search_documents_fts_gin ON search_documents USING GIN (search_vector);
CREATE INDEX ix_search_documents_title_trgm ON search_documents USING GIN (f_unaccent(title) gin_trgm_ops);

CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    owner_membership_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (owner_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE collection_items (
    collection_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    item_type VARCHAR(30) NOT NULL,
    item_id UUID NOT NULL,
    sequence_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, item_type, item_id),
    FOREIGN KEY (collection_id, tenant_id) REFERENCES collections(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 4: ACQUISITION, CRAWLER, DRIVE SYNC, OCR & PROVENANCE (A01 – A09)
-- ============================================================================
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_type VARCHAR(40) NOT NULL
        CHECK (source_type IN ('MANUAL_UPLOAD', 'GOOGLE_DRIVE', 'WEB_CRAWLER', 'PARTNER_API')),
    name VARCHAR(255) NOT NULL,
    base_uri VARCHAR(512),
    adapter_key VARCHAR(80),
    config_json JSONB NOT NULL DEFAULT '{}',
    rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    attribution_text VARCHAR(500),
    policy_status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (policy_status IN ('ACTIVE', 'PAUSED_DRIFT', 'PAUSED_MANUAL', 'REVOKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE source_rights (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    license_type VARCHAR(60) NOT NULL,
    allow_rebranding BOOLEAN NOT NULL DEFAULT FALSE,
    allow_public_redistribution BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_asset_id UUID,
    verified_by UUID REFERENCES users(id),
    verified_at TIMESTAMPTZ,
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE source_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    provider VARCHAR(40) NOT NULL DEFAULT 'GOOGLE_DRIVE',
    secret_vault_ref VARCHAR(255) NOT NULL,
    granted_scopes VARCHAR(120)[] NOT NULL DEFAULT '{https://www.googleapis.com/auth/drive.file}',
    status VARCHAR(30) NOT NULL DEFAULT 'CONNECTED'
        CHECK (status IN ('CONNECTED', 'REAUTH_REQUIRED', 'REVOKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE source_sync_cursors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_connection_id UUID UNIQUE NOT NULL,
    cursor_token VARCHAR(512) NOT NULL,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (source_connection_id, tenant_id) REFERENCES source_connections(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE crawl_urls (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    url VARCHAR(1024) NOT NULL,
    url_hash CHAR(64) NOT NULL,
    etag VARCHAR(255),
    last_modified_header VARCHAR(120),
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'FETCHED', 'SKIPPED_304', 'FAILED', 'BLOCKED_ROBOTS')),
    last_fetched_at TIMESTAMPTZ,
    UNIQUE (source_id, url_hash),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE crawl_fetches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    crawl_url_id UUID NOT NULL,
    http_status INT NOT NULL,
    raw_asset_id UUID,
    resolved_ip VARCHAR(64) NOT NULL,
    parse_yield_count INT NOT NULL DEFAULT 0,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (crawl_url_id, tenant_id) REFERENCES crawl_urls(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
);

CREATE TABLE source_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_id UUID NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    title VARCHAR(500),
    mime_type VARCHAR(120) NOT NULL,
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'INGESTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    rights_status VARCHAR(40) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    inferred_metadata_json JSONB NOT NULL DEFAULT '{}',
    paired_solution_doc_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, external_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE file_pairing_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    exam_doc_id UUID NOT NULL,
    solution_doc_id UUID NOT NULL,
    similarity_score NUMERIC(4,3) NOT NULL,
    signals_json JSONB NOT NULL DEFAULT '{}', -- filename_stem, folder_proximity, header_match, item_count_match
    status VARCHAR(30) NOT NULL DEFAULT 'PROPOSED'
        CHECK (status IN ('PROPOSED', 'CONFIRMED', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (exam_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (solution_doc_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE document_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    source_document_id UUID NOT NULL,
    version_no INT NOT NULL,
    raw_asset_id UUID NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    page_count INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_document_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_document_id, tenant_id) REFERENCES source_documents(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (raw_asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE parser_definitions (
    code VARCHAR(60) PRIMARY KEY,
    version VARCHAR(40) NOT NULL,
    supported_mimes VARCHAR(120)[] NOT NULL,
    config_schema_json JSONB NOT NULL DEFAULT '{}',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE acquisition_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_id UUID,
    document_version_id UUID,
    job_type VARCHAR(40) NOT NULL
        CHECK (job_type IN ('EXAM_IMPORT', 'DRIVE_SYNC', 'WEB_CRAWL', 'REPROCESS_EXTRACTION')),
    job_status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (job_status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')),
    current_stage VARCHAR(50),
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    metrics_json JSONB NOT NULL DEFAULT '{}',
    last_error_code VARCHAR(80),
    last_error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (source_id, tenant_id) REFERENCES sources(id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
);
CREATE INDEX ix_acquisition_jobs_status_created ON acquisition_jobs (job_status, created_at);

CREATE TABLE pipeline_stage_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    acquisition_job_id UUID NOT NULL,
    stage_name VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')),
    worker_version VARCHAR(40),
    input_ref VARCHAR(512),
    output_ref VARCHAR(512),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    FOREIGN KEY (acquisition_job_id, tenant_id) REFERENCES acquisition_jobs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE dead_letter_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    acquisition_job_id UUID,
    source_queue VARCHAR(80) NOT NULL,
    payload_json JSONB NOT NULL,
    error_trace TEXT NOT NULL,
    replayed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE extraction_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    document_version_id UUID NOT NULL,
    acquisition_job_id UUID,
    profile_code VARCHAR(60) NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    ocr_engine_version VARCHAR(40),
    confidence_score NUMERIC(4,3),
    silver_artifact_key VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (acquisition_job_id, tenant_id) REFERENCES acquisition_jobs(id, tenant_id)
);

CREATE TABLE extracted_pages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    page_no INT NOT NULL,
    render_asset_id UUID,
    width_pt NUMERIC(8,2) NOT NULL,
    height_pt NUMERIC(8,2) NOT NULL,
    detected_header_y0 NUMERIC(8,2),
    detected_footer_y1 NUMERIC(8,2),
    raw_text TEXT,
    UNIQUE (extraction_run_id, page_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE layout_regions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extracted_page_id UUID NOT NULL,
    region_type VARCHAR(40) NOT NULL
        CHECK (region_type IN ('HEADER', 'FOOTER', 'QUESTION_BLOCK', 'FIGURE', 'TABLE', 'FORMULA_BLOCK')),
    bbox_json JSONB NOT NULL,
    cropped_asset_id UUID,
    extracted_text TEXT,
    FOREIGN KEY (extracted_page_id, tenant_id) REFERENCES extracted_pages(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE provenance_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    document_version_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    page_no INT NOT NULL,
    bbox_json JSONB,
    section_label VARCHAR(120),
    original_question_label VARCHAR(60),
    raw_snippet_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (document_version_id, tenant_id) REFERENCES document_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE exam_import_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    proposed_structure_json JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 5: CANDIDATES, DEDUPLICATION, QUESTION BANK & QUALITY GOVERNANCE (A07, A08, A09, C01, C02)
-- ============================================================================
CREATE TABLE duplicate_clusters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cluster_type VARCHAR(30) NOT NULL
        CHECK (cluster_type IN ('EXACT', 'STRUCTURAL_VARIANT', 'SEMANTIC_NEAR')),
    canonical_question_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (status IN ('PENDING_REVIEW', 'RESOLVED_MERGED', 'RESOLVED_KEEP_VARIANTS')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE content_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID NOT NULL,
    provenance_id UUID,
    title VARCHAR(500) NOT NULL,
    blocks_json JSONB NOT NULL DEFAULT '[]',
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    promoted_content_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id)
);

CREATE TABLE question_candidates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    extraction_run_id UUID,
    provenance_id UUID,
    parent_candidate_id UUID,
    acquisition_method VARCHAR(40) NOT NULL
        CHECK (acquisition_method IN ('DOCUMENT_IMPORT', 'WEB_CRAWL', 'DRIVE_SYNC', 'AI_GENERATED', 'MANUAL_AUTHOR')),
    item_type VARCHAR(40) NOT NULL
        CHECK (item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY', 'GROUP_STEM')),
    payload_json JSONB NOT NULL,
    content_hash CHAR(64) NOT NULL,
    structural_hash CHAR(64) NOT NULL,
    embedding_vec vector(768),
    duplicate_cluster_id UUID,
    extraction_confidence NUMERIC(4,3),
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'EXTRACTED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    candidate_status VARCHAR(30) NOT NULL DEFAULT 'NEEDS_REVIEW'
        CHECK (candidate_status IN ('NEEDS_REVIEW', 'APPROVED', 'REJECTED')),
    promoted_question_version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (extraction_run_id, tenant_id) REFERENCES extraction_runs(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (provenance_id, tenant_id) REFERENCES provenance_records(id, tenant_id),
    FOREIGN KEY (duplicate_cluster_id, tenant_id) REFERENCES duplicate_clusters(id, tenant_id)
);
CREATE INDEX ix_question_candidates_content_hash ON question_candidates (tenant_id, content_hash);
CREATE INDEX ix_question_candidates_struct_hash ON question_candidates (tenant_id, structural_hash);

CREATE TABLE duplicate_members (
    cluster_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    similarity_score NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    PRIMARY KEY (cluster_id, candidate_id),
    FOREIGN KEY (cluster_id, tenant_id) REFERENCES duplicate_clusters(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, tenant_id) REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE questions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    stable_code VARCHAR(100) NOT NULL,
    item_type VARCHAR(40) NOT NULL
        CHECK (item_type IN ('SINGLE_CHOICE', 'MULTIPLE_SELECT', 'CLUSTER_TRUE_FALSE', 'SHORT_NUMERIC', 'ESSAY', 'GROUP_STEM')),
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
    curriculum_node_id UUID REFERENCES curriculum_nodes(id),
    exam_framework_tags VARCHAR(50)[] NOT NULL DEFAULT '{SGK_KNTT}',
    difficulty_level VARCHAR(20) NOT NULL DEFAULT 'TH'
        CHECK (difficulty_level IN ('NB', 'TH', 'VD', 'VDC')),
    current_version_id UUID,
    -- Đủ các trục trạng thái với mặc định Fail-Closed ('PENDING_REVIEW')
    acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED'
        CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    duplicate_cluster_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, stable_code), -- Khắc phục lỗi UNIQUE toàn cục
    UNIQUE (id, tenant_id),
    CONSTRAINT chk_question_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);
CREATE INDEX ix_questions_exam_tags_gin ON questions USING GIN (exam_framework_tags);
CREATE INDEX ix_questions_pool_lookup ON questions (tenant_id, publication_status, verification_status, primary_taxonomy_node_id, difficulty_level);

CREATE TABLE question_stimuli (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stimulus_type VARCHAR(40) NOT NULL
        CHECK (stimulus_type IN ('READING_PASSAGE', 'EXPERIMENT_CONTEXT', 'DATA_TABLE', 'SHARED_FIGURE')),
    body_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE question_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
    question_id UUID NOT NULL,
    version_no INT NOT NULL,
    stimulus_id UUID,
    stem_json JSONB NOT NULL,
    options_json JSONB NOT NULL DEFAULT '[]',
    numeric_parse_policy_json JSONB NOT NULL DEFAULT '{"decimal_sep": "COMMA_OR_DOT", "allow_thousand_sep": false, "max_chars": 4, "tolerance": 0.01}',
    content_hash CHAR(64) NOT NULL,
    structural_hash CHAR(64) NOT NULL,
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    change_reason VARCHAR(500),
    created_by UUID REFERENCES users(id),
    approved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (question_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (question_id, tenant_id) REFERENCES questions(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (stimulus_id, tenant_id) REFERENCES question_stimuli(id, tenant_id)
);

-- Tách biệt hoàn toàn đáp án & lời giải sang bảng riêng (Role phát đề bị REVOKE SELECT — Khắc phục P0-8)
CREATE TABLE question_version_answers (
    question_version_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    answer_schema JSONB NOT NULL,
    rationale_json JSONB NOT NULL DEFAULT '{}',
    is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
);

-- View công khai an toàn tuyệt đối cho API Phát đề (C05)
CREATE OR REPLACE VIEW v_public_question_versions AS
SELECT id, tenant_id, visibility_scope, question_id, version_no, stimulus_id, stem_json, options_json, numeric_parse_policy_json
FROM question_versions;
CREATE INDEX ix_question_versions_hashes ON question_versions (tenant_id, content_hash, structural_hash);

ALTER TABLE questions
    ADD CONSTRAINT fk_questions_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES question_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE question_options (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    question_version_id UUID NOT NULL,
    option_key VARCHAR(10) NOT NULL, -- A, B, C, D hoặc a, b, c, d
    content_json JSONB NOT NULL,
    sequence_order INT NOT NULL,
    UNIQUE (question_version_id, option_key),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE question_assets (
    question_version_id UUID NOT NULL,
    asset_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    PRIMARY KEY (question_version_id, asset_id),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE verification_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    question_version_id UUID,
    candidate_id UUID,
    verifier_type VARCHAR(40) NOT NULL
        CHECK (verifier_type IN ('DETERMINISTIC_RULE', 'SYMPY_SOLVER', 'LLM_BLIND_SOLVER', 'HUMAN_EXPERT')),
    model_or_tool_version VARCHAR(80) NOT NULL,
    computed_answer VARCHAR(255),
    is_match_with_source BOOLEAN,
    findings_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (question_version_id, tenant_id) REFERENCES question_versions(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id, tenant_id) REFERENCES question_candidates(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE review_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    entity_type VARCHAR(40) NOT NULL
        CHECK (entity_type IN ('QUESTION_CANDIDATE', 'QUESTION_VERSION', 'EXAM_ARCHIVE', 'CONTENT_VERSION')),
    entity_id UUID NOT NULL,
    queue_name VARCHAR(60) NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'NORMAL'
        CHECK (severity IN ('LOW', 'NORMAL', 'HIGH', 'CRITICAL')),
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CANCELLED')),
    assigned_to UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);
CREATE INDEX ix_review_tasks_queue_status ON review_tasks (tenant_id, queue_name, status, severity);

CREATE TABLE review_decisions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    review_task_id UUID NOT NULL,
    reviewer_id UUID NOT NULL REFERENCES users(id),
    decision VARCHAR(30) NOT NULL
        CHECK (decision IN ('APPROVE', 'REJECT', 'REQUEST_REPROCESS', 'SPLIT_MERGE')),
    reason TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (review_task_id, tenant_id) REFERENCES review_tasks(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 6: ASSESSMENT, SNAPSHOT TÁCH ĐÁP ÁN, ATTEMPTS & GRADING (C03 – C07, F02, F03)
-- ============================================================================
CREATE TABLE assessment_frameworks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(60) UNIQUE NOT NULL, -- THPTQG_2025, DGTD_TSA, DGNL_HSA, HSG_CHUYEN
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE framework_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    framework_id UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
    version_code VARCHAR(40) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    total_questions INT NOT NULL, -- VD: Toán THPT 2025 = 22 câu; Lý/Hóa/Sinh = 28 câu
    duration_seconds INT NOT NULL,
    section_specs_json JSONB NOT NULL,
    UNIQUE (framework_id, version_code, subject_code)
);

CREATE TABLE scoring_scheme_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(80) UNIQUE NOT NULL,
    rules_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE rubric_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    criteria_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE blueprints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    framework_id UUID NOT NULL REFERENCES assessment_frameworks(id),
    name VARCHAR(255) NOT NULL,
    rules_json JSONB NOT NULL,
    duration_seconds INT NOT NULL,
    total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE assessments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE'
        CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE')),
    title VARCHAR(500) NOT NULL,
    subject_code VARCHAR(50) NOT NULL,
    grade_band VARCHAR(30) NOT NULL,
    exam_type_code VARCHAR(50) NOT NULL,
    source_archive_doc_id UUID,
    branded_pdf_asset_id UUID,
    preview_pdf_asset_id UUID,
    access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE'
        CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
    current_version_id UUID,
    verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED'
        CHECK (verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')),
    rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW'
        CHECK (rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')),
    publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
        CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    CONSTRAINT chk_assessment_publish_gate CHECK (
        publication_status <> 'PUBLISHED'
        OR (verification_status = 'VERIFIED' AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE') AND current_version_id IS NOT NULL)
    )
);

-- Tách hoàn toàn structure_snapshot_json (không đáp án) và bảng assessment_answer_keys (ADR-005 & Runbook 9)
CREATE TABLE assessment_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_id UUID NOT NULL,
    version_no INT NOT NULL,
    scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
    duration_seconds INT NOT NULL,
    total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
    base_seed BIGINT NOT NULL DEFAULT 20261111,
    -- Chỉ chứa danh sách question_version_id, section, điểm số và hash kiểm chứng (Tuyệt đối không chứa đáp án)
    structure_snapshot_json JSONB NOT NULL,
    is_immutable BOOLEAN NOT NULL DEFAULT TRUE,
    published_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (assessment_id, version_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments(id, tenant_id) ON DELETE RESTRICT
);

ALTER TABLE assessments
    ADD CONSTRAINT fk_assessments_current_version
    FOREIGN KEY (current_version_id, tenant_id)
    REFERENCES assessment_versions(id, tenant_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE assessment_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_version_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    section_code VARCHAR(30) NOT NULL, -- PART_I_MCQ, PART_II_TF4, PART_III_SHORT
    sequence_order INT NOT NULL,
    points NUMERIC(5,2) NOT NULL,
    UNIQUE (assessment_version_id, sequence_order),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE CASCADE
);

-- Bảng lưu khóa đáp án chấm điểm tách riêng có lịch sử phiên bản phục vụ Regrade/Errata (Khắc phục P0-8 & RB-07)
CREATE TABLE assessment_answer_keys (
    assessment_version_id UUID NOT NULL,
    key_revision_no INT NOT NULL DEFAULT 1,
    tenant_id UUID NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    answer_key_payload_json JSONB NOT NULL,
    key_checksum_sha256 CHAR(64) NOT NULL,
    change_reason TEXT NOT NULL DEFAULT 'INITIAL_PUBLISH',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (assessment_version_id, key_revision_no),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_assessment_answer_keys_current ON assessment_answer_keys (assessment_version_id) WHERE is_current = TRUE;

CREATE TABLE assessment_assignments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    assessment_version_id UUID NOT NULL,
    class_id UUID,
    group_id UUID,
    assigned_by UUID NOT NULL REFERENCES users(id),
    available_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at TIMESTAMPTZ,
    max_attempts INT NOT NULL DEFAULT 1,
    reveal_policy VARCHAR(40) NOT NULL DEFAULT 'AFTER_DUE'
        CHECK (reveal_policy IN ('IMMEDIATE', 'AFTER_DUE', 'MANUAL_RELEASE', 'NEVER')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- Bảng attempts: ON DELETE RESTRICT trên membership_id + Lưu hạt giống và thứ tự trộn đề
CREATE TABLE attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    assessment_version_id UUID NOT NULL REFERENCES assessment_versions(id) ON DELETE RESTRICT,
    assignment_id UUID,
    membership_id UUID NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'VOIDED')),
    attempt_seed BIGINT NOT NULL,
    question_order_ids UUID[] NOT NULL,
    option_permutations_json JSONB NOT NULL DEFAULT '{}', -- {"<question_version_id>": ["C", "A", "D", "B"]}
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    submit_idempotency_key VARCHAR(128) UNIQUE,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);
CREATE INDEX ix_attempts_membership_status ON attempts (tenant_id, membership_id, status);
CREATE INDEX ix_attempts_auto_submit_scan ON attempts (status, deadline_at) WHERE status = 'IN_PROGRESS';

CREATE TABLE responses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
    current_sequence BIGINT NOT NULL DEFAULT 0,
    current_revision_no INT NOT NULL DEFAULT 0,
    last_idempotency_key VARCHAR(128),
    response_payload JSONB NOT NULL,
    is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (attempt_id, question_version_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
);

-- Bảng đăng ký khóa Idempotency-Key chống Race Condition tuyệt đối (Không phụ thuộc recorded_at của Partition — Khắc phục P0-7)
CREATE TABLE autosave_idempotency_registry (
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    response_id UUID NOT NULL,
    revision_no INT NOT NULL,
    client_sequence BIGINT NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, attempt_id, idempotency_key),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX ix_autosave_idem_registry_recorded_at ON autosave_idempotency_registry (recorded_at);

-- Bảng response_revisions: Lưu lịch sử bất biến mọi lần lưu bài
CREATE TABLE response_revisions (
    id UUID DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    response_id UUID NOT NULL,
    revision_no INT NOT NULL,
    client_sequence BIGINT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    payload_json JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, recorded_at),
    FOREIGN KEY (response_id, tenant_id) REFERENCES responses(id, tenant_id) ON DELETE CASCADE
) PARTITION BY RANGE (recorded_at);

CREATE TABLE response_revisions_default PARTITION OF response_revisions DEFAULT;
CREATE INDEX ix_response_revisions_lookup ON response_revisions (tenant_id, response_id, revision_no);

-- Bảng grades: Cho phép nhiều revision khi Phúc khảo / Regrade (Khắc phục lỗi UNIQUE(attempt_id))
CREATE TABLE grades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    attempt_id UUID NOT NULL,
    revision_no INT NOT NULL DEFAULT 1,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    grade_reason VARCHAR(40) NOT NULL DEFAULT 'INITIAL'
        CHECK (grade_reason IN ('INITIAL', 'APPEAL_REGRADE', 'ERRATA_REGRADE', 'MANUAL_OVERRIDE')),
    scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
    total_score NUMERIC(6,2) NOT NULL,
    section_breakdown_json JSONB NOT NULL,
    item_details_json JSONB NOT NULL,
    graded_by UUID REFERENCES users(id),
    graded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    UNIQUE (attempt_id, revision_no),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX uq_grades_current_per_attempt ON grades (attempt_id) WHERE is_current = TRUE;

CREATE TABLE grading_details (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    grade_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    earned_points NUMERIC(5,2) NOT NULL,
    max_points NUMERIC(5,2) NOT NULL,
    is_correct BOOLEAN NOT NULL,
    sub_item_breakdown_json JSONB NOT NULL DEFAULT '{}',
    FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE appeals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    grade_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    question_version_id UUID REFERENCES question_versions(id),
    reason_text TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'APPROVED_REGRADED', 'REJECTED')),
    resolution_note TEXT,
    resolved_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE RESTRICT,
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE gradebook_projections (
    class_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    stats_json JSONB NOT NULL DEFAULT '{}',
    top_wrong_questions_json JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (class_id, assignment_id),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 7: LEARNER LOOP, PRACTICE, ERROR NOTEBOOK, FSRS & IRT (D01 – D06)
-- ============================================================================
CREATE TABLE activity_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    activity_type VARCHAR(50) NOT NULL,
    entity_id UUID NOT NULL,
    duration_seconds INT NOT NULL DEFAULT 0,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE learner_dashboard_projections (
    membership_id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    streak_days INT NOT NULL DEFAULT 0,
    summary_json JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE learning_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    target_framework_code VARCHAR(60) NOT NULL DEFAULT 'THPTQG_2025',
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE plan_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    learning_plan_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
    task_type VARCHAR(40) NOT NULL
        CHECK (task_type IN ('PREREQUISITE_REVIEW', 'READ_CONCEPT', 'PRACTICE_SET', 'ERROR_REMEDIATION')),
    rationale_text TEXT NOT NULL,
    sequence_order INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')),
    FOREIGN KEY (learning_plan_id, tenant_id) REFERENCES learning_plans(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE practice_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    mode VARCHAR(40) NOT NULL DEFAULT 'TOPIC_DRILL'
        CHECK (mode IN ('LESSON_EMBED', 'TOPIC_DRILL', 'REMEDIATION_DRILL')),
    status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS'
        CHECK (status IN ('IN_PROGRESS', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE practice_responses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    practice_session_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    response_payload JSONB NOT NULL,
    is_correct BOOLEAN NOT NULL,
    hints_used INT NOT NULL DEFAULT 0,
    answered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (practice_session_id, tenant_id) REFERENCES practice_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE error_notes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id),
    wrong_count INT NOT NULL DEFAULT 1,
    consecutive_correct_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'IN_REMEDIATION', 'RESOLVED')),
    last_wrong_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (membership_id, question_version_id),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE remediation_tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    error_note_id UUID NOT NULL,
    recommended_question_version_ids UUID[] NOT NULL,
    completed_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'ASSIGNED'
        CHECK (status IN ('ASSIGNED', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (error_note_id, tenant_id) REFERENCES error_notes(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE flashcard_decks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE flashcards (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    deck_id UUID NOT NULL,
    front_md TEXT NOT NULL,
    back_md TEXT NOT NULL,
    source_question_version_id UUID REFERENCES question_versions(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (deck_id, tenant_id) REFERENCES flashcard_decks(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE revision_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    flashcard_id UUID NOT NULL,
    scheduler_version VARCHAR(30) NOT NULL DEFAULT 'FSRS_V5',
    stability NUMERIC(8,4) NOT NULL DEFAULT 1.0000,
    difficulty NUMERIC(6,4) NOT NULL DEFAULT 5.0000,
    retrievability NUMERIC(5,4) NOT NULL DEFAULT 1.0000,
    reps INT NOT NULL DEFAULT 0,
    lapses INT NOT NULL DEFAULT 0,
    last_review_at TIMESTAMPTZ,
    due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (membership_id, flashcard_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE,
    FOREIGN KEY (flashcard_id, tenant_id) REFERENCES flashcards(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE question_irt_parameters (
    question_version_id UUID PRIMARY KEY REFERENCES question_versions(id) ON DELETE CASCADE,
    calibration_run_id UUID NOT NULL DEFAULT uuid_generate_v7(),
    model_type VARCHAR(30) NOT NULL
        CHECK (model_type IN ('RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')),
    sample_size INT NOT NULL CHECK (sample_size >= 150),
    learner_count INT NOT NULL CHECK (learner_count >= 100),
    ability_spread_sd NUMERIC(5,3) NOT NULL CHECK (ability_spread_sd >= 0.600),
    converged BOOLEAN NOT NULL DEFAULT TRUE CHECK (converged IS TRUE),
    infit_msq NUMERIC(5,3) NOT NULL CHECK (infit_msq BETWEEN 0.700 AND 1.300),
    outfit_msq NUMERIC(5,3) NOT NULL,
    max_abs_dif NUMERIC(5,3) NOT NULL DEFAULT 0.000 CHECK (max_abs_dif <= 0.500),
    discrimination_a NUMERIC(6,4) NOT NULL DEFAULT 1.0000,
    difficulty_b NUMERIC(6,4) NOT NULL,
    guessing_c NUMERIC(6,4) NOT NULL DEFAULT 0.0000,
    step_thresholds_json JSONB NOT NULL DEFAULT '[]',
    se_difficulty NUMERIC(6,4) NOT NULL CHECK (se_difficulty < 0.3000),
    diagnostics_json JSONB NOT NULL DEFAULT '{}',
    approved_by UUID REFERENCES users(id),
    calibrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE mastery_estimates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE CASCADE,
    model_code VARCHAR(40) NOT NULL DEFAULT 'RULE_WEIGHTED_L1'
        CHECK (model_code IN ('RULE_WEIGHTED_L1', 'RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')),
    theta_estimate NUMERIC(6,3) NOT NULL DEFAULT 0.000,
    mastery_probability NUMERIC(5,3) NOT NULL CHECK (mastery_probability BETWEEN 0 AND 1),
    standard_error NUMERIC(5,3) NOT NULL DEFAULT 0.500,
    evidence_count INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'INSUFFICIENT_DATA'
        CHECK (status IN ('INSUFFICIENT_DATA', 'CALIBRATED')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (membership_id, taxonomy_node_id, model_code),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 8: AI SYSTEM, TUTOR, GENERATION & COPILOT (E01 – E06)
-- ============================================================================
CREATE TABLE ai_providers (
    code VARCHAR(40) PRIMARY KEY,
    base_url VARCHAR(255) NOT NULL,
    circuit_state VARCHAR(20) NOT NULL DEFAULT 'CLOSED'
        CHECK (circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE ai_models (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    provider_code VARCHAR(40) NOT NULL REFERENCES ai_providers(code),
    model_name VARCHAR(100) NOT NULL,
    task_role VARCHAR(40) NOT NULL, -- OCR_VISION, HEALER_FAST, VERIFIER_REASONING, TUTOR_CHAT
    input_cost_per_1m NUMERIC(8,4) NOT NULL,
    output_cost_per_1m NUMERIC(8,4) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE prompt_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    code VARCHAR(80) NOT NULL,
    version_no INT NOT NULL,
    system_prompt TEXT NOT NULL,
    user_template TEXT NOT NULL,
    output_schema_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (code, version_no)
);

CREATE TABLE ai_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    task_type VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
    prompt_version_id UUID REFERENCES prompt_versions(id),
    input_refs_json JSONB NOT NULL DEFAULT '{}',
    output_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id)
);

CREATE TABLE ai_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ai_job_id UUID,
    model_id UUID NOT NULL REFERENCES ai_models(id),
    prompt_tokens INT NOT NULL,
    completion_tokens INT NOT NULL,
    cost_usd NUMERIC(10,6) NOT NULL,
    latency_ms INT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_ai_traces_tenant_date ON ai_traces (tenant_id, occurred_at);

CREATE TABLE tutor_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    context_question_version_id UUID REFERENCES question_versions(id),
    context_content_version_id UUID REFERENCES content_versions(id),
    tutor_mode VARCHAR(30) NOT NULL DEFAULT 'HINT_MODE'
        CHECK (tutor_mode IN ('HINT_MODE', 'EXPLAIN_MODE', 'CHALLENGE_MODE')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (id, tenant_id),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE tutor_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    tutor_session_id UUID NOT NULL,
    sender_role VARCHAR(20) NOT NULL CHECK (sender_role IN ('USER', 'ASSISTANT', 'SYSTEM_GUARD')),
    message_md TEXT NOT NULL,
    citations_json JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tutor_session_id, tenant_id) REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE retrieval_traces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    tutor_session_id UUID NOT NULL,
    retrieved_ids UUID[] NOT NULL,
    scores NUMERIC(5,4)[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tutor_session_id, tenant_id) REFERENCES tutor_sessions(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE generation_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    requested_by UUID NOT NULL REFERENCES users(id),
    taxonomy_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id),
    target_difficulty VARCHAR(20) NOT NULL,
    source_anchors_json JSONB NOT NULL,
    generated_candidate_id UUID,
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT_CREATED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE explanation_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    question_version_id UUID NOT NULL REFERENCES question_versions(id),
    draft_rationale_json JSONB NOT NULL,
    sympy_verified BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING_APPROVAL',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    recommended_action_type VARCHAR(40) NOT NULL,
    target_entity_id UUID NOT NULL,
    evidence_json JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'DISMISSED', 'COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE teacher_assist_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    class_id UUID NOT NULL,
    assignment_id UUID NOT NULL,
    summary_report_json JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'READY_FOR_TEACHER',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
);

-- ============================================================================
-- NHÓM 9: NOTIFICATIONS, PLATFORM OPS, OUTBOX/INBOX, AUDIT & COMMERCE (F06, G01 – G06)
-- ============================================================================
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    dedup_key VARCHAR(160) UNIQUE NOT NULL,
    title VARCHAR(255) NOT NULL,
    body_text TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
);

CREATE TABLE notification_deliveries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    channel VARCHAR(30) NOT NULL CHECK (channel IN ('IN_APP', 'EMAIL')),
    status VARCHAR(30) NOT NULL DEFAULT 'SENT',
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE platform_settings (
    key VARCHAR(100) PRIMARY KEY,
    value_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE feature_flags (
    key VARCHAR(100) PRIMARY KEY,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    tenant_whitelist UUID[] NOT NULL DEFAULT '{}',
    config_json JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE takedown_cases (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    complainant_name VARCHAR(255) NOT NULL,
    target_entity_type VARCHAR(40) NOT NULL CHECK (target_entity_type IN ('SOURCE_DOCUMENT', 'CONTENT_ITEM', 'QUESTION')),
    target_entity_id UUID NOT NULL,
    legal_reason TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'EXECUTED', 'REJECTED')),
    executed_by UUID REFERENCES users(id),
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE audit_events (
    id UUID DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    actor_id UUID,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(60) NOT NULL,
    target_id UUID NOT NULL,
    reason TEXT,
    before_json JSONB,
    after_json JSONB,
    correlation_id VARCHAR(100),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE audit_events_2026_11 PARTITION OF audit_events
    FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT;
CREATE INDEX ix_audit_events_tenant_target ON audit_events (tenant_id, target_type, target_id, occurred_at DESC);

CREATE TABLE outbox_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    schema_version INT NOT NULL DEFAULT 1,
    aggregate_type VARCHAR(60) NOT NULL,
    aggregate_id UUID NOT NULL,
    correlation_id VARCHAR(100),
    payload_json JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PUBLISHED', 'FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);
CREATE INDEX ix_outbox_events_pending ON outbox_events (status, created_at) WHERE status = 'PENDING';

-- Bảng Inbox cho Idempotent Event Consumers (Khắc phục lỗi thiếu Consumer Idempotency)
CREATE TABLE inbox_events (
    consumer_name VARCHAR(80) NOT NULL,
    event_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (consumer_name, event_id)
);

CREATE TABLE analytics_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    event_name VARCHAR(80) NOT NULL,
    dimensions_json JSONB NOT NULL DEFAULT '{}',
    metrics_json JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE daily_metric_rollups (
    metric_date DATE NOT NULL,
    tenant_id UUID NOT NULL,
    metric_code VARCHAR(80) NOT NULL,
    metric_value NUMERIC(14,4) NOT NULL,
    PRIMARY KEY (metric_date, tenant_id, metric_code)
);

CREATE TABLE subscription_plans (
    code VARCHAR(40) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    price_vnd BIGINT NOT NULL,
    quotas_json JSONB NOT NULL
);

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    membership_id UUID NOT NULL,
    plan_code VARCHAR(40) NOT NULL REFERENCES subscription_plans(code),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'PAST_DUE', 'CANCELLED', 'EXPIRED')),
    current_period_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE usage_ledger (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    tenant_id UUID NOT NULL,
    membership_id UUID NOT NULL,
    resource_code VARCHAR(60) NOT NULL, -- BRANDED_PDF_EXPORT, AI_TUTOR_MESSAGE
    delta INT NOT NULL,
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
);

CREATE TABLE payment_webhooks (
    webhook_id VARCHAR(128) PRIMARY KEY,
    provider VARCHAR(40) NOT NULL,
    raw_payload_json JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE vertical_packs (
    code VARCHAR(60) PRIMARY KEY, -- K12_VN_KNTT, IELTS_ACADEMIC, TOEIC_LR, HSK_3_0
    name VARCHAR(255) NOT NULL,
    manifest_json JSONB NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE locale_bundles (
    locale_code VARCHAR(20) NOT NULL,
    namespace VARCHAR(60) NOT NULL,
    messages_json JSONB NOT NULL,
    PRIMARY KEY (locale_code, namespace)
);

-- ============================================================================
-- KỊCH BẢN ROW-LEVEL SECURITY (RLS 100% BẢNG), AUTO-PARTITION, TRIGGERS & GRANT/REVOKE CHUẨN V11.3
-- ============================================================================

-- 1. Hàm tự động tạo Partition hàng tháng & Dọn dẹp khóa Idempotency quá hạn (Chạy định kỳ hàng ngày)
CREATE OR REPLACE FUNCTION ensure_monthly_partitions(p_table_name TEXT, p_start_date DATE, p_months INT) RETURNS void AS $$
DECLARE
    i INT;
    v_start TIMESTAMPTZ;
    v_end TIMESTAMPTZ;
    v_part_name TEXT;
BEGIN
    FOR i IN 0..(p_months - 1) LOOP
        v_start := (date_trunc('month', p_start_date) + (i || ' months')::interval) AT TIME ZONE 'UTC';
        v_end := (date_trunc('month', p_start_date) + ((i + 1) || ' months')::interval) AT TIME ZONE 'UTC';
        v_part_name := format('%s_%s', p_table_name, to_char(v_start AT TIME ZONE 'UTC', 'YYYY_MM'));
        IF to_regclass(v_part_name) IS NULL THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L);',
                v_part_name, p_table_name, v_start, v_end
            );
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION run_daily_partition_and_registry_maintenance() RETURNS void AS $$
BEGIN
    PERFORM ensure_monthly_partitions('response_revisions', CURRENT_DATE, 6);
    PERFORM ensure_monthly_partitions('audit_events', CURRENT_DATE, 6);
    -- Dọn các khóa Idempotency của các phiên thi đã ghi nhận quá 30 ngày
    DELETE FROM autosave_idempotency_registry WHERE recorded_at < NOW() - INTERVAL '30 days';
END;
$$ LANGUAGE plpgsql;

-- Khởi tạo ngay các phân vùng ban đầu và đăng ký lịch chạy hàng ngày nếu có pg_cron
SELECT run_daily_partition_and_registry_maintenance();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
        EXECUTE $cron$SELECT cron.schedule('ulp_daily_maintenance', '15 2 * * *', 'SELECT run_daily_partition_and_registry_maintenance()')$cron$;
    END IF;
END $$;

-- 2. Kích hoạt ENABLE & FORCE ROW LEVEL SECURITY trên 100% Bảng Nghiệp Vụ
--    Áp dụng InitPlan `(SELECT current_app_tenant_id())` để PostgreSQL tính 1 lần mỗi câu lệnh (tối ưu cho tải 5.000 CCU)
--    và siết chặt hybrid_read_policy chỉ cho phép đọc học liệu SYSTEM_PUBLIC đã PUBLISHED / đóng băng (Khắc phục P0-1, P0-2, P0-3, P0-8)
DO $$
DECLARE
    t text;
    strict_tenant_tables text[] := ARRAY[
        'memberships', 'parental_consents', 'roles', 'role_permissions', 'groups', 'classes', 'class_members',
        'content_drafts', 'comment_threads', 'publications', 'access_grants',
        'collections', 'collection_items', 'sources', 'source_rights', 'source_connections', 'source_sync_cursors',
        'crawl_urls', 'crawl_fetches', 'source_documents', 'file_pairing_candidates', 'document_versions',
        'acquisition_jobs', 'pipeline_stage_runs', 'dead_letter_items', 'extraction_runs', 'extracted_pages',
        'layout_regions', 'provenance_records', 'exam_import_sessions', 'duplicate_clusters', 'content_candidates',
        'question_candidates', 'duplicate_members', 'question_version_answers',
        'verification_runs', 'review_tasks', 'review_decisions', 'rubric_versions', 'blueprints',
        'assessment_answer_keys', 'assessment_assignments', 'attempts', 'responses',
        'autosave_idempotency_registry', 'response_revisions', 'grades', 'grading_details', 'appeals', 'gradebook_projections',
        'activity_events', 'learner_dashboard_projections', 'learning_plans', 'plan_tasks',
        'practice_sessions', 'practice_responses', 'error_notes', 'remediation_tasks',
        'flashcard_decks', 'flashcards', 'revision_schedules', 'mastery_estimates',
        'ai_jobs', 'ai_traces', 'tutor_sessions', 'tutor_messages', 'retrieval_traces',
        'generation_requests', 'explanation_runs', 'recommendations', 'teacher_assist_jobs',
        'notifications', 'takedown_cases', 'erasure_certificates', 'audit_events', 'outbox_events', 'inbox_events',
        'analytics_events', 'daily_metric_rollups', 'subscriptions', 'usage_ledger', 'graph_validation_runs'
    ];
    hybrid_public_tables text[] := ARRAY[
        'taxonomy_nodes', 'taxonomy_node_versions', 'taxonomy_aliases', 'knowledge_edges',
        'curriculum_versions', 'curriculum_nodes', 'taxonomy_mappings',
        'assets', 'content_items', 'content_versions', 'content_blocks', 'content_assets',
        'questions', 'question_stimuli', 'question_versions', 'question_options', 'question_assets',
        'assessments', 'assessment_versions', 'assessment_items', 'search_documents'
    ];
BEGIN
    FOREACH t IN ARRAY strict_tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON %I;', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation_policy ON %I
             USING (tenant_id = (SELECT current_app_tenant_id()))
             WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));',
            t
        );
    END LOOP;

    FOREACH t IN ARRAY hybrid_public_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format('DROP POLICY IF EXISTS hybrid_read_policy ON %I;', t);
        EXECUTE format('DROP POLICY IF EXISTS hybrid_write_policy ON %I;', t);
        EXECUTE format(
            'CREATE POLICY hybrid_write_policy ON %I FOR ALL
             USING (tenant_id = (SELECT current_app_tenant_id()))
             WITH CHECK (tenant_id = (SELECT current_app_tenant_id()));',
            t
        );
    END LOOP;
END $$;

-- 2a. Định nghĩa tường minh hybrid_read_policy cho từng nhóm bảng công khai (Khắc phục P0-3: Chặn lộ bản DRAFT / PENDING_REVIEW / TAKEN_DOWN của hệ thống)
CREATE POLICY hybrid_read_policy ON content_items FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON questions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON assessments FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND publication_status = 'PUBLISHED'
        AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE'))
);

CREATE POLICY hybrid_read_policy ON content_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND is_immutable IS TRUE
        AND status = 'PUBLISHED'
        AND EXISTS (
            SELECT 1 FROM content_items c
            WHERE c.id = content_versions.content_item_id AND c.tenant_id = content_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON content_blocks FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM content_versions cv
            WHERE cv.id = content_blocks.content_version_id AND cv.tenant_id = content_blocks.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON content_assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM content_versions cv
            WHERE cv.id = content_assets.content_version_id AND cv.tenant_id = content_assets.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND visibility_scope = 'SYSTEM_PUBLIC'
        AND is_immutable IS TRUE
        AND EXISTS (
            SELECT 1 FROM questions q
            WHERE q.id = question_versions.question_id AND q.tenant_id = question_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_options FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.id = question_options.question_version_id AND qv.tenant_id = question_options.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.id = question_assets.question_version_id AND qv.tenant_id = question_assets.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON question_stimuli FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM question_versions qv
            WHERE qv.stimulus_id = question_stimuli.id AND qv.tenant_id = question_stimuli.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assessment_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND is_immutable IS TRUE
        AND EXISTS (
            SELECT 1 FROM assessments a
            WHERE a.id = assessment_versions.assessment_id AND a.tenant_id = assessment_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assessment_items FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM assessment_versions av
            WHERE av.id = assessment_items.assessment_version_id AND av.tenant_id = assessment_items.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON assets FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND asset_role <> 'RAW_SOURCE')
);

CREATE POLICY hybrid_read_policy ON search_documents FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC')
);

CREATE POLICY hybrid_read_policy ON taxonomy_nodes FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC' AND is_active IS TRUE)
);

CREATE POLICY hybrid_read_policy ON taxonomy_node_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM taxonomy_nodes tn
            WHERE tn.id = taxonomy_node_versions.taxonomy_node_id AND tn.tenant_id = taxonomy_node_versions.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON taxonomy_aliases FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM taxonomy_nodes tn
            WHERE tn.id = taxonomy_aliases.taxonomy_node_id AND tn.tenant_id = taxonomy_aliases.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON knowledge_edges FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (SELECT 1 FROM taxonomy_nodes s WHERE s.id = knowledge_edges.source_node_id AND s.is_active IS TRUE)
        AND EXISTS (SELECT 1 FROM taxonomy_nodes t WHERE t.id = knowledge_edges.target_node_id AND t.is_active IS TRUE))
);

CREATE POLICY hybrid_read_policy ON curriculum_versions FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id() AND visibility_scope = 'SYSTEM_PUBLIC' AND status = 'PUBLISHED')
);

CREATE POLICY hybrid_read_policy ON curriculum_nodes FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM curriculum_versions cv
            WHERE cv.id = curriculum_nodes.curriculum_version_id AND cv.tenant_id = curriculum_nodes.tenant_id
        ))
);

CREATE POLICY hybrid_read_policy ON taxonomy_mappings FOR SELECT USING (
    tenant_id = (SELECT current_app_tenant_id())
    OR (tenant_id = system_public_tenant_id()
        AND EXISTS (
            SELECT 1 FROM curriculum_nodes cn
            WHERE cn.id = taxonomy_mappings.curriculum_node_id AND cn.tenant_id = taxonomy_mappings.tenant_id
        ))
);

-- 2b. Policy riêng cho ulp_worker_grading đọc được khóa đáp án của đề hệ thống SYSTEM_PUBLIC (Khắc phục P0-1)
CREATE POLICY grading_read_question_answers ON question_version_answers
    FOR SELECT TO ulp_worker_grading
    USING (tenant_id = (SELECT current_app_tenant_id()) OR tenant_id = system_public_tenant_id());

CREATE POLICY grading_read_answer_keys ON assessment_answer_keys
    FOR SELECT TO ulp_worker_grading
    USING (tenant_id = (SELECT current_app_tenant_id()) OR tenant_id = system_public_tenant_id());

-- 2c. Policy hẹp theo app.system_job cho ulp_system_scheduler (Khắc phục P0-2: Quét job nền xuyên Tenant không cần BYPASSRLS)
CREATE POLICY sys_outbox_relay ON outbox_events FOR ALL TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'outbox_relay')
    WITH CHECK ((SELECT app_system_job()) = 'outbox_relay');

CREATE POLICY sys_attempt_expiry_scan ON attempts FOR SELECT TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'attempt_expiry' AND status = 'IN_PROGRESS');

CREATE POLICY sys_rollup_read ON analytics_events FOR SELECT TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'metric_rollup');

CREATE POLICY sys_rollup_write ON daily_metric_rollups FOR ALL TO ulp_system_scheduler
    USING ((SELECT app_system_job()) = 'metric_rollup')
    WITH CHECK ((SELECT app_system_job()) = 'metric_rollup');

-- 3. Trigger kiểm tra tham chiếu học liệu chéo: Cho phép đề thi của Tenant tham chiếu câu hỏi của chính Tenant đó HOẶC SYSTEM_PUBLIC đã PUBLISHED
CREATE OR REPLACE FUNCTION enforce_tenant_or_system_public_question_ref() RETURNS trigger AS $$
DECLARE
    v_target_tenant UUID;
    v_target_scope VARCHAR(20);
    v_target_immutable BOOLEAN;
BEGIN
    SELECT tenant_id, visibility_scope, is_immutable INTO v_target_tenant, v_target_scope, v_target_immutable
    FROM question_versions
    WHERE id = NEW.question_version_id;

    IF v_target_tenant IS NULL THEN
        RAISE EXCEPTION 'FK_NOT_FOUND: Không tìm thấy question_version_id %', NEW.question_version_id;
    END IF;

    IF v_target_tenant <> NEW.tenant_id AND NOT (v_target_tenant = system_public_tenant_id() AND v_target_scope = 'SYSTEM_PUBLIC' AND v_target_immutable IS TRUE) THEN
        RAISE EXCEPTION 'CROSS_TENANT_REFERENCE_FORBIDDEN: Tenant % không được phép sử dụng question_version_id % chưa công khai của Tenant khác.',
            NEW.tenant_id, NEW.question_version_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_check_assessment_item_question_ref
    BEFORE INSERT OR UPDATE ON assessment_items
    FOR EACH ROW EXECUTE FUNCTION enforce_tenant_or_system_public_question_ref();

-- 4. Tách biệt Trigger Append-Only (trên bảng phân vùng) và Trigger Phiên bản Bất biến
CREATE OR REPLACE FUNCTION prevent_append_only_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'APPEND_ONLY_VIOLATION: Bảng % (thuộc %) là Append-Only, cấm tuyệt đối UPDATE/DELETE.',
        TG_TABLE_NAME, TG_ARGV[0];
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_audit_events
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('audit_events');

CREATE TRIGGER trg_immutable_response_revisions
    BEFORE UPDATE OR DELETE ON response_revisions
    FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation('response_revisions');

CREATE OR REPLACE FUNCTION enforce_audit_no_raw_pii() RETURNS trigger AS $$
DECLARE
    v_combined TEXT;
BEGIN
    v_combined := coalesce(NEW.before_json::text, '') || coalesce(NEW.after_json::text, '');
    IF v_combined ~* '"(raw_email|plaintext_email|guardian_email|password|phone_number|cccd)"\s*:' THEN
        RAISE EXCEPTION 'RAW_PII_IN_AUDIT_FORBIDDEN: Không được phép ghi PII dạng rõ vào audit_events; phải dùng blind_index hoặc pii_ciphertext.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_no_raw_pii
    BEFORE INSERT ON audit_events
    FOR EACH ROW EXECUTE FUNCTION enforce_audit_no_raw_pii();

CREATE OR REPLACE FUNCTION auto_lock_version_on_publish() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'content_versions' AND NEW.status = 'PUBLISHED' THEN
        NEW.is_immutable := TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_auto_lock_content_versions
    BEFORE INSERT OR UPDATE ON content_versions
    FOR EACH ROW EXECUTE FUNCTION auto_lock_version_on_publish();

CREATE OR REPLACE FUNCTION lock_question_version_when_published() RETURNS trigger AS $$
BEGIN
    IF NEW.publication_status = 'PUBLISHED' AND NEW.current_version_id IS NOT NULL THEN
        UPDATE question_versions SET is_immutable = TRUE WHERE id = NEW.current_version_id AND is_immutable = FALSE;
        UPDATE question_version_answers SET is_immutable = TRUE WHERE question_version_id = NEW.current_version_id AND is_immutable = FALSE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_lock_question_version_on_publish
    AFTER INSERT OR UPDATE OF publication_status, current_version_id ON questions
    FOR EACH ROW EXECUTE FUNCTION lock_question_version_when_published();

CREATE OR REPLACE FUNCTION prevent_version_immutability_mutation() RETURNS trigger AS $$
BEGIN
    IF OLD.is_immutable IS TRUE THEN
        IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'content_versions' AND NEW.status = 'TAKEN_DOWN' THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'IMMUTABLE_VIOLATION: Bản ghi phiên bản trong bảng % đã đóng băng (is_immutable=TRUE), phải tạo version_no + 1.', TG_TABLE_NAME;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_content_versions
    BEFORE UPDATE OR DELETE ON content_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_question_versions
    BEFORE UPDATE OR DELETE ON question_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_question_version_answers
    BEFORE UPDATE OR DELETE ON question_version_answers
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

CREATE TRIGGER trg_immutable_assessment_versions
    BEFORE UPDATE OR DELETE ON assessment_versions
    FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation();

-- 5. Phân quyền Least-Privilege Tường Minh theo Từng Bảng/Cột cho 7 DB Roles (Khắc phục P0-8: Xóa bỏ GRANT ALL trên Worker Roles)
GRANT USAGE ON SCHEMA public TO ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;

-- 5a. Role API Chính (ulp_app_rw — H-03 Hardening: Thu hồi CREATE trên schema, thu hồi quyền ghi trên các bảng chỉ dành cho Worker/System/Migrator)
REVOKE CREATE ON SCHEMA public FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ulp_app_rw;
-- H-03: ulp_app_rw không được sửa bảng cấu hình từ điển FTS, bảng rollup chỉ số hoặc bảng inbox của worker
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON fts_dictionary_versions, daily_metric_rollups, inbox_events FROM ulp_app_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE ulp_migrator IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;

-- 5b. Role Phòng Thi (ulp_exam_session_rw): Ghi được bài làm Autosave nhưng bị chặn tuyệt đối đọc bảng đáp án
GRANT SELECT ON
    v_public_question_versions, questions, question_versions, question_options, question_stimuli, question_assets,
    assets, assessments, assessment_versions, assessment_items, assessment_assignments, memberships
TO ulp_exam_session_rw;
GRANT SELECT, INSERT, UPDATE ON attempts, responses TO ulp_exam_session_rw;
GRANT SELECT, INSERT ON autosave_idempotency_registry, response_revisions, activity_events, outbox_events, audit_events TO ulp_exam_session_rw;

-- 5c. Role Worker Chấm Điểm (ulp_worker_grading): Chỉ có quyền trên các bảng liên quan đến chấm điểm & đáp án
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_worker_grading;
GRANT SELECT ON
    attempts, responses, assessment_versions, assessment_items,
    question_versions, question_version_answers, assessment_answer_keys,
    scoring_scheme_versions, rubric_versions, memberships
TO ulp_worker_grading;
GRANT SELECT, INSERT, UPDATE ON grades, grading_details, error_notes, learner_dashboard_projections, gradebook_projections TO ulp_worker_grading;
GRANT UPDATE (status, submitted_at) ON attempts TO ulp_worker_grading;
GRANT INSERT ON outbox_events, audit_events TO ulp_worker_grading;
GRANT SELECT, INSERT ON inbox_events TO ulp_worker_grading;

-- 5d. Role Worker Nạp Đề & OCR (ulp_worker_ingestion): Tuyệt đối KHÔNG có quyền trên bảng đáp án hay bảng điểm
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_worker_ingestion;
GRANT SELECT, INSERT, UPDATE ON
    sources, source_documents, document_versions, acquisition_jobs, pipeline_stage_runs,
    extraction_runs, extracted_pages, layout_regions, provenance_records, exam_import_sessions,
    content_candidates, question_candidates, duplicate_clusters, file_pairing_candidates,
    crawl_urls, crawl_fetches, source_sync_cursors, verification_runs, review_tasks
TO ulp_worker_ingestion;
GRANT SELECT, INSERT ON assets, duplicate_members, dead_letter_items TO ulp_worker_ingestion;
GRANT INSERT ON outbox_events, audit_events TO ulp_worker_ingestion;
GRANT SELECT, INSERT ON inbox_events TO ulp_worker_ingestion;

-- 5e. Role Scheduler Hệ Thống (ulp_system_scheduler): Chỉ có quyền đọc/cập nhật đúng cột phục vụ Outbox Relay, Quét Hết Giờ & Rollup
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_system_scheduler;
GRANT SELECT, UPDATE (status, published_at) ON outbox_events TO ulp_system_scheduler;
GRANT SELECT (id, tenant_id, status, deadline_at) ON attempts TO ulp_system_scheduler;
GRANT SELECT ON analytics_events TO ulp_system_scheduler;
GRANT SELECT, INSERT, UPDATE ON daily_metric_rollups TO ulp_system_scheduler;

-- 5f. Role Phân Tích Chỉ Đọc (ulp_analytics_ro)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ulp_analytics_ro;
GRANT SELECT ON v_public_question_versions, search_documents, daily_metric_rollups, analytics_events TO ulp_analytics_ro;

-- 5g. Khóa cứng quyền UPDATE/DELETE trên các bảng Append-Only và khóa bảng đáp án khỏi các role không có thẩm quyền
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events, response_revisions FROM PUBLIC, ulp_app_rw, ulp_exam_session_rw, ulp_worker_ingestion, ulp_worker_grading, ulp_system_scheduler, ulp_analytics_ro;
REVOKE ALL ON question_version_answers, assessment_answer_keys FROM PUBLIC, ulp_exam_session_rw, ulp_worker_ingestion, ulp_system_scheduler, ulp_analytics_ro;
