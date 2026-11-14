"""E01 ai_gateway: ai_providers, ai_models, prompt_versions, ai_jobs, ai_traces

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql (AI group). The three catalogs are
global (no tenant, see app.platform.rls.GLOBAL_TABLES): the API role reads them, and may change only the
circuit state of a provider (the gateway opens a breaker after repeated 429/5xx/timeouts). Models and prompt
versions come from migrations or an operator, never from a tenant request. ai_jobs and ai_traces are strict
tenant tables; a trace is append-only. The provider `gemini` is seeded (the only one the code uses today);
models are not seeded because their prices are not in the repository.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-11-14
"""

from alembic import op

from app.platform.rls import strict_tenant_rls

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ai_providers (
            code VARCHAR(40) PRIMARY KEY,
            base_url VARCHAR(255) NOT NULL,
            circuit_state VARCHAR(20) NOT NULL DEFAULT 'CLOSED',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ai_providers_circuit_state CHECK (circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ai_models (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            provider_code VARCHAR(40) NOT NULL REFERENCES ai_providers(code),
            model_name VARCHAR(100) NOT NULL,
            task_role VARCHAR(40) NOT NULL,
            input_cost_per_1m NUMERIC(8,4) NOT NULL,
            output_cost_per_1m NUMERIC(8,4) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE prompt_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            code VARCHAR(80) NOT NULL,
            version_no INT NOT NULL,
            system_prompt TEXT NOT NULL,
            user_template TEXT NOT NULL,
            output_schema_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_prompt_versions_code_version_no UNIQUE (code, version_no)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE ai_jobs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            task_type VARCHAR(50) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'QUEUED',
            prompt_version_id UUID REFERENCES prompt_versions(id),
            input_refs_json JSONB NOT NULL DEFAULT '{}',
            output_json JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ai_jobs_status CHECK (status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
            CONSTRAINT uq_ai_jobs_id_tenant_id UNIQUE (id, tenant_id)
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )
    op.execute("CREATE INDEX ix_ai_traces_tenant_date ON ai_traces (tenant_id, occurred_at)")
    op.execute(
        "INSERT INTO ai_providers (code, base_url) VALUES "
        "('gemini', 'https://generativelanguage.googleapis.com') ON CONFLICT (code) DO NOTHING"
    )

    for table in ("ai_jobs", "ai_traces"):
        for stmt in strict_tenant_rls(table):
            op.execute(stmt)

    op.execute("REVOKE ALL ON ai_providers, ai_models, prompt_versions, ai_jobs, ai_traces FROM PUBLIC")
    op.execute("GRANT SELECT ON ai_providers, ai_models, prompt_versions TO ulp_app_rw")
    op.execute("GRANT UPDATE (circuit_state, updated_at) ON ai_providers TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ai_jobs TO ulp_app_rw")
    op.execute("GRANT SELECT, INSERT ON ai_traces TO ulp_app_rw")  # append-only


def downgrade() -> None:
    for table in ("ai_traces", "ai_jobs", "prompt_versions", "ai_models", "ai_providers"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
