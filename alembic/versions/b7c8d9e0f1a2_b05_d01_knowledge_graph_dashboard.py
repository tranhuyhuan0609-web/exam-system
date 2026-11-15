"""B05 knowledge graph, D01 dashboard: knowledge_edges, graph_validation_runs, activity_events, learner_dashboard_projections

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* knowledge_edges is shared material (hybrid RLS, H-01): a tenant reads its own edges, plus the system tenant's edges
  only while BOTH endpoints are `is_active IS TRUE` (the EXISTS subqueries read taxonomy_nodes under the caller's own
  RLS); writes stay inside the tenant. The default tenant is the system tenant, as in the spec. Edges are never
  edited: a changed graph is a new `graph_version`.
* graph_validation_runs, activity_events and learner_dashboard_projections are strict tenant tables.

Privileges, section 5 and grants_manifest.csv: the exam room appends activity events (5b), the grading worker keeps
the learner projection up to date (5c). The API role (5a) reads and appends edges, validation runs and activity
(append-only logs), and upserts the projection; nobody edits or deletes an edge, a run or an event.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import hybrid_write_rls, strict_tenant_rls

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

TABLES = ("knowledge_edges", "graph_validation_runs", "activity_events", "learner_dashboard_projections")
STRICT_TABLES = ("graph_validation_runs", "activity_events", "learner_dashboard_projections")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge_edges (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'
                REFERENCES tenants(id) ON DELETE CASCADE,
            source_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
            target_node_id UUID NOT NULL REFERENCES taxonomy_nodes(id) ON DELETE RESTRICT,
            edge_type VARCHAR(40) NOT NULL,
            confidence NUMERIC(4,3) NOT NULL DEFAULT 1.000,
            graph_version INT NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_knowledge_edges_edge_type CHECK (
                edge_type IN ('PREREQUISITE', 'RELATED_TO', 'EXTENDS', 'APPLIED_IN', 'COMMON_MISCONCEPTION')
            ),
            CONSTRAINT ck_knowledge_edges_confidence CHECK (confidence BETWEEN 0 AND 1),
            CONSTRAINT ck_knowledge_edges_no_self_loop CHECK (source_node_id <> target_node_id),
            CONSTRAINT uq_knowledge_edges_tenant_src_dst_type_ver
                UNIQUE (tenant_id, source_node_id, target_node_id, edge_type, graph_version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE graph_validation_runs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            graph_version INT NOT NULL,
            is_dag_valid BOOLEAN NOT NULL,
            cycle_path_json JSONB NOT NULL DEFAULT '[]',
            validated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE activity_events (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            activity_type VARCHAR(50) NOT NULL,
            entity_id UUID NOT NULL,
            duration_seconds INT NOT NULL DEFAULT 0,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_activity_events_membership_tenant
                FOREIGN KEY (membership_id, tenant_id)
                REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE learner_dashboard_projections (
            membership_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            streak_days INT NOT NULL DEFAULT 0,
            summary_json JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_learner_dashboard_projections_membership_tenant
                FOREIGN KEY (membership_id, tenant_id)
                REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """
    )

    # --- row-level security ---------------------------------------------------------------------
    for stmt in hybrid_write_rls("knowledge_edges"):
        op.execute(stmt)
    op.execute("DROP POLICY IF EXISTS hybrid_read_policy ON knowledge_edges")
    op.execute(
        """
        CREATE POLICY hybrid_read_policy ON knowledge_edges FOR SELECT USING (
            tenant_id = (SELECT current_app_tenant_id())
            OR (
                tenant_id = system_public_tenant_id()
                AND EXISTS (
                    SELECT 1 FROM taxonomy_nodes s
                    WHERE s.id = knowledge_edges.source_node_id AND s.is_active IS TRUE
                )
                AND EXISTS (
                    SELECT 1 FROM taxonomy_nodes t
                    WHERE t.id = knowledge_edges.target_node_id AND t.is_active IS TRUE
                )
            )
        )
        """
    )
    for table in STRICT_TABLES:
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON knowledge_edges, graph_validation_runs, activity_events TO ulp_app_rw")  # 5a
    op.execute("GRANT SELECT, INSERT, UPDATE ON learner_dashboard_projections TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT ON activity_events TO ulp_exam_session_rw")  # 5b
    op.execute("GRANT SELECT, INSERT, UPDATE ON learner_dashboard_projections TO ulp_worker_grading")  # 5c


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
