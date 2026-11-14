"""B01 content: content_items, content_versions, content_assets, publications, access_grants

Tables, policies, triggers and privileges as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* content_items / content_versions / content_assets are shared material (hybrid RLS): a tenant reads its own
  rows plus the system tenant's PUBLISHED, cleared, frozen ones; writes stay inside the tenant.
* publications and access_grants are strict tenant tables.
* chk_content_publish_gate: no PUBLISHED item unless VERIFIED, rights cleared and a current version exists.
* fk_content_items_current_version is DEFERRABLE INITIALLY DEFERRED (item and first version in one transaction).
* trg_auto_lock_content_versions sets is_immutable when a version becomes PUBLISHED;
  trg_immutable_content_versions (prevent_version_immutability_mutation, spec F-03 fix) then refuses any
  change or delete of a frozen version except moving it to TAKEN_DOWN.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import FROZEN_VERSION, hybrid_read_child, hybrid_read_root, hybrid_write_rls, strict_tenant_rls

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE content_items (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            content_type VARCHAR(40) NOT NULL,
            slug VARCHAR(255) NOT NULL,
            curriculum_node_id UUID REFERENCES curriculum_nodes(id) ON DELETE SET NULL,
            primary_taxonomy_node_id UUID REFERENCES taxonomy_nodes(id) ON DELETE SET NULL,
            current_version_id UUID,
            acquisition_status VARCHAR(30) NOT NULL DEFAULT 'NORMALIZED',
            verification_status VARCHAR(30) NOT NULL DEFAULT 'UNCHECKED',
            rights_status VARCHAR(30) NOT NULL DEFAULT 'PENDING_REVIEW',
            publication_status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            access_tier VARCHAR(20) NOT NULL DEFAULT 'FREE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_content_items_visibility_scope
                CHECK (visibility_scope IN ('SYSTEM_PUBLIC', 'TENANT_PRIVATE', 'SHARED_PARTNER')),
            CONSTRAINT ck_content_items_content_type CHECK (
                content_type IN ('ARTICLE', 'LESSON', 'VIDEO', 'DOCUMENT', 'FLASHCARD_SET', 'VOCABULARY_SET')
            ),
            CONSTRAINT ck_content_items_acquisition_status
                CHECK (acquisition_status IN ('INGESTED', 'EXTRACTED', 'NORMALIZED', 'FAILED')),
            CONSTRAINT ck_content_items_verification_status CHECK (
                verification_status IN ('UNCHECKED', 'AUTO_VERIFIED', 'VERIFIED', 'DISPUTED', 'REJECTED')
            ),
            CONSTRAINT ck_content_items_rights_status CHECK (
                rights_status IN ('PENDING_REVIEW', 'OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE', 'RESTRICTED', 'TAKEDOWN')
            ),
            CONSTRAINT ck_content_items_publication_status
                CHECK (publication_status IN ('DRAFT', 'READY', 'PUBLISHED', 'ARCHIVED', 'TAKEN_DOWN')),
            CONSTRAINT ck_content_items_access_tier CHECK (access_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO')),
            CONSTRAINT uq_content_items_tenant_id_slug UNIQUE (tenant_id, slug),
            CONSTRAINT uq_content_items_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT chk_content_publish_gate CHECK (
                publication_status <> 'PUBLISHED'
                OR (verification_status = 'VERIFIED'
                    AND rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                    AND current_version_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
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
            status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
            is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_content_versions_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'SUPERSEDED', 'TAKEN_DOWN')),
            CONSTRAINT uq_content_versions_content_item_id_version_no UNIQUE (content_item_id, version_no),
            CONSTRAINT uq_content_versions_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_content_versions_item_tenant
                FOREIGN KEY (content_item_id, tenant_id) REFERENCES content_items(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_content_versions_branded_asset_tenant
                FOREIGN KEY (branded_asset_id, tenant_id) REFERENCES assets(id, tenant_id),
            CONSTRAINT fk_content_versions_preview_asset_tenant
                FOREIGN KEY (preview_asset_id, tenant_id) REFERENCES assets(id, tenant_id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE content_items
            ADD CONSTRAINT fk_content_items_current_version
            FOREIGN KEY (current_version_id, tenant_id)
            REFERENCES content_versions(id, tenant_id)
            DEFERRABLE INITIALLY DEFERRED
        """
    )
    op.execute(
        """
        CREATE TABLE content_assets (
            content_version_id UUID NOT NULL,
            asset_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            PRIMARY KEY (content_version_id, asset_id),
            CONSTRAINT fk_content_assets_version_tenant
                FOREIGN KEY (content_version_id, tenant_id)
                REFERENCES content_versions(id, tenant_id) ON DELETE CASCADE,
            CONSTRAINT fk_content_assets_asset_tenant
                FOREIGN KEY (asset_id, tenant_id) REFERENCES assets(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE publications (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            entity_type VARCHAR(40) NOT NULL,
            entity_id UUID NOT NULL,
            version_id UUID NOT NULL,
            published_by UUID NOT NULL REFERENCES users(id),
            published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_publications_entity_type CHECK (entity_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE access_grants (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            membership_id UUID NOT NULL,
            resource_type VARCHAR(40) NOT NULL,
            resource_id UUID NOT NULL,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_access_grants_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    # --- immutability (spec section 4, F-03 fix) ----------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auto_lock_version_on_publish() RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'content_versions' AND NEW.status = 'PUBLISHED' THEN
                NEW.is_immutable := TRUE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_auto_lock_content_versions
            BEFORE INSERT OR UPDATE ON content_versions
            FOR EACH ROW EXECUTE FUNCTION auto_lock_version_on_publish()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_version_immutability_mutation() RETURNS trigger AS $$
        BEGIN
            IF OLD.is_immutable IS TRUE THEN
                -- NEW.status exists only on content_versions, hence the nested IF
                IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'content_versions' THEN
                    IF NEW.status = 'TAKEN_DOWN' THEN
                        RETURN NEW;
                    END IF;
                END IF;
                RAISE EXCEPTION 'IMMUTABLE_VIOLATION: Bản ghi phiên bản trong bảng % đã đóng băng (is_immutable=TRUE), phải tạo version_no + 1.', TG_TABLE_NAME;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_immutable_content_versions
            BEFORE UPDATE OR DELETE ON content_versions
            FOR EACH ROW EXECUTE FUNCTION prevent_version_immutability_mutation()
        """
    )

    # --- row-level security ---------------------------------------------------------------------
    statements = [
        *hybrid_write_rls("content_items"),
        *hybrid_read_root("content_items"),
        *hybrid_write_rls("content_versions"),
        *hybrid_read_child(
            "content_versions",
            "content_items",
            "content_item_id",
            own_condition=f"{FROZEN_VERSION} AND status = 'PUBLISHED'",
        ),
        *hybrid_write_rls("content_assets"),
        *hybrid_read_child("content_assets", "content_versions", "content_version_id"),
        *strict_tenant_rls("publications"),
        *strict_tenant_rls("access_grants"),
    ]
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    tables = "content_items, content_versions, content_assets, publications, access_grants"
    op.execute(f"REVOKE ALL ON {tables} FROM PUBLIC")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON content_items, content_versions, content_assets, access_grants TO ulp_app_rw"
    )
    op.execute("GRANT SELECT, INSERT ON publications TO ulp_app_rw")  # a publication is a log entry, never edited


def downgrade() -> None:
    for table in ("access_grants", "publications", "content_assets"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE content_items DROP CONSTRAINT IF EXISTS fk_content_items_current_version")
    op.execute("DROP TABLE IF EXISTS content_versions")
    op.execute("DROP TABLE IF EXISTS content_items")
    op.execute("DROP FUNCTION IF EXISTS prevent_version_immutability_mutation()")
    op.execute("DROP FUNCTION IF EXISTS auto_lock_version_on_publish()")
