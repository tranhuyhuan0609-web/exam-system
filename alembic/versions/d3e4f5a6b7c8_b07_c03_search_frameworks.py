"""B07 search, C03 frameworks: search_documents, collections, collection_items, assessment_frameworks,
framework_versions, scoring_scheme_versions, blueprints

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* search_documents is shared material (hybrid RLS): a tenant reads its own rows plus the system tenant's rows marked
  SYSTEM_PUBLIC; writes stay inside the tenant. It carries the generated `search_vector` (f_unaccent over title and
  body, GIN) and the unaccented trigram index on the title. A takedown removes the projection row at once (B07).
* collections, collection_items and blueprints are strict tenant tables.
* assessment_frameworks, framework_versions and scoring_scheme_versions are global catalogues (no tenant of their
  own): the exam standards every tenant builds on. They are seeded here with the two THPTQG 2025+ standards: Toan,
  22 questions / 90 minutes, and Ly, Hoa, Sinh, 28 questions / 50 minutes (3 + 4 + 3 = 10 points for Toan,
  4.5 + 4 + 1.5 = 10 for the three sciences), and with the scoring rules of that exam.

The seed is written out literally (not read from app code): a later change to the code must not rewrite what an
already released migration inserted. `app.modules.frameworks` upserts the same rows idempotently.

Privileges, section 5: ulp_app_rw works all of them (5a); ulp_worker_grading reads scoring_scheme_versions (5c) and
ulp_analytics_ro reads search_documents (5f), as in grants_manifest.csv.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-11-15
"""

import json

from alembic import op

from app.platform.rls import hybrid_read_root, hybrid_write_rls, strict_tenant_rls

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

TABLES = (
    "search_documents",
    "collections",
    "collection_items",
    "assessment_frameworks",
    "framework_versions",
    "scoring_scheme_versions",
    "blueprints",
)
STRICT_TABLES = ("collections", "collection_items", "blueprints")

FRAMEWORK_CODE = "THPTQG_2025"
FRAMEWORK_NAME = "Chuong trinh GDPT 2018 - Ky thi THPT Quoc gia 2025+"
VERSION_CODE = "2025.1"
SCORING_CODE = "THPTQG_2025_DEFAULT"


def _sections(mcq: int, tf4: int, short: int, mcq_pts: str, tf4_pts: str, short_pts: str) -> dict:
    return {
        "total_points": "10.00",
        "sections": [
            {"section_code": "PART_I_MCQ", "item_type": "SINGLE_CHOICE", "question_count": mcq,
             "points_per_question": mcq_pts},
            {"section_code": "PART_II_TF4", "item_type": "CLUSTER_TRUE_FALSE", "question_count": tf4,
             "points_per_question": tf4_pts},
            {"section_code": "PART_III_SHORT", "item_type": "SHORT_NUMERIC", "question_count": short,
             "points_per_question": short_pts},
        ],
    }  # fmt: skip


# subject, total questions, duration in seconds, sections
SUBJECTS = (
    ("MATH", 22, 5400, _sections(12, 4, 6, "0.25", "1.00", "0.50")),
    ("PHYSICS", 28, 3000, _sections(18, 4, 6, "0.25", "1.00", "0.25")),
    ("CHEMISTRY", 28, 3000, _sections(18, 4, 6, "0.25", "1.00", "0.25")),
    ("BIOLOGY", 28, 3000, _sections(18, 4, 6, "0.25", "1.00", "0.25")),
)
SCORING_RULES = {
    "framework_code": FRAMEWORK_CODE,
    "part_i_mcq": {"points_per_question": "0.25"},
    "part_ii_tf4": {"points_by_correct_subitems": {"0": "0.00", "1": "0.10", "2": "0.25", "3": "0.50", "4": "1.00"}},
    "part_iii_short": {
        "points_per_question_by_subject": {"MATH": "0.50", "PHYSICS": "0.25", "CHEMISTRY": "0.25", "BIOLOGY": "0.25"},
        "max_chars": 4,
        "allow_thousand_separator": False,
        "decimal_separators": [",", "."],
    },
}


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE search_documents (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            visibility_scope VARCHAR(20) NOT NULL DEFAULT 'TENANT_PRIVATE',
            doc_type VARCHAR(30) NOT NULL,
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
            CONSTRAINT ck_search_documents_doc_type CHECK (doc_type IN ('CONTENT', 'QUESTION', 'ASSESSMENT')),
            CONSTRAINT uq_search_documents_tenant_doc_entity UNIQUE (tenant_id, doc_type, entity_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_search_documents_fts_gin ON search_documents USING GIN (search_vector)")
    op.execute(
        "CREATE INDEX ix_search_documents_title_trgm ON search_documents USING GIN (f_unaccent(title) gin_trgm_ops)"
    )
    op.execute(
        """
        CREATE TABLE collections (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            owner_membership_id UUID NOT NULL,
            title VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_collections_id_tenant_id UNIQUE (id, tenant_id),
            CONSTRAINT fk_collections_owner_membership_tenant
                FOREIGN KEY (owner_membership_id, tenant_id)
                REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE collection_items (
            collection_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            item_type VARCHAR(30) NOT NULL,
            item_id UUID NOT NULL,
            sequence_order INT NOT NULL DEFAULT 0,
            PRIMARY KEY (collection_id, item_type, item_id),
            CONSTRAINT fk_collection_items_collection_tenant
                FOREIGN KEY (collection_id, tenant_id)
                REFERENCES collections(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE assessment_frameworks (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            code VARCHAR(60) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE framework_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            framework_id UUID NOT NULL REFERENCES assessment_frameworks(id) ON DELETE CASCADE,
            version_code VARCHAR(40) NOT NULL,
            subject_code VARCHAR(50) NOT NULL,
            total_questions INT NOT NULL,
            duration_seconds INT NOT NULL,
            section_specs_json JSONB NOT NULL,
            CONSTRAINT uq_framework_versions_fw_ver_subject UNIQUE (framework_id, version_code, subject_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE scoring_scheme_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            code VARCHAR(80) UNIQUE NOT NULL,
            rules_json JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE blueprints (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            framework_id UUID NOT NULL REFERENCES assessment_frameworks(id),
            name VARCHAR(255) NOT NULL,
            rules_json JSONB NOT NULL,
            duration_seconds INT NOT NULL,
            total_points NUMERIC(6,2) NOT NULL DEFAULT 10.00,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_blueprints_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )

    # --- seed: the THPTQG 2025+ standards -------------------------------------------------------
    op.execute(f"INSERT INTO assessment_frameworks (code, name) VALUES ({_q(FRAMEWORK_CODE)}, {_q(FRAMEWORK_NAME)})")
    for subject, total, duration, sections in SUBJECTS:
        op.execute(
            "INSERT INTO framework_versions "
            "(framework_id, version_code, subject_code, total_questions, duration_seconds, section_specs_json) "
            f"SELECT id, {_q(VERSION_CODE)}, {_q(subject)}, {total}, {duration}, {_q(json.dumps(sections))}::jsonb "
            f"FROM assessment_frameworks WHERE code = {_q(FRAMEWORK_CODE)}"
        )
    op.execute(
        f"INSERT INTO scoring_scheme_versions (code, rules_json) "
        f"VALUES ({_q(SCORING_CODE)}, {_q(json.dumps(SCORING_RULES))}::jsonb)"
    )

    # --- row-level security ---------------------------------------------------------------------
    statements = [
        *hybrid_write_rls("search_documents"),
        *hybrid_read_root("search_documents", public_condition="visibility_scope = 'SYSTEM_PUBLIC'"),
    ]
    for table in STRICT_TABLES:
        statements += strict_tenant_rls(table)
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON search_documents, collections, collection_items TO ulp_app_rw")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON blueprints, assessment_frameworks, framework_versions, "
        "scoring_scheme_versions TO ulp_app_rw"
    )
    op.execute("GRANT SELECT ON scoring_scheme_versions TO ulp_worker_grading")  # 5c
    op.execute("GRANT SELECT ON search_documents TO ulp_analytics_ro")  # 5f


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
