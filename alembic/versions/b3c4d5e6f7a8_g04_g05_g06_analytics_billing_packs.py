"""G04 analytics, G05 billing, G06 vertical packs: analytics_events, daily_metric_rollups, subscription_plans,
subscriptions, usage_ledger, payment_webhooks, vertical_packs, locale_bundles

Tables as in docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql:
* analytics_events, daily_metric_rollups, subscriptions and usage_ledger are strict tenant tables (section 2).
  The rollup job runs as ulp_system_scheduler with `app.system_job = 'metric_rollup'`: it reads the events of every
  tenant (`sys_rollup_read`) and writes every tenant's rollups (`sys_rollup_write`), with no BYPASSRLS.
* subscription_plans, payment_webhooks, vertical_packs and locale_bundles have no tenant column and no RLS, as in the
  DDL: plans, packs and message bundles are platform catalogues, a webhook is the payment provider's own record.
  They are not seeded: prices and packs are business decisions.
* usage_ledger.idempotency_key is UNIQUE: replaying a usage event books it once.

Privileges, section 5 and grants_manifest.csv: the scheduler reads events and upserts rollups (5e), the analytics role
reads both (5f). The API role (5a) appends events and usage, reads rollups (its write privileges on
daily_metric_rollups are revoked, 5a), manages subscriptions, packs and bundles, and reads the plans. Decision #7:
payment_webhooks is INSERT only for the API role: it can record a webhook, never read, change or delete one.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-11-15
"""

from alembic import op

from app.platform.rls import strict_tenant_rls, system_job_policy

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

TABLES = (
    "analytics_events",
    "daily_metric_rollups",
    "subscription_plans",
    "subscriptions",
    "usage_ledger",
    "payment_webhooks",
    "vertical_packs",
    "locale_bundles",
)
STRICT_TABLES = ("analytics_events", "daily_metric_rollups", "subscriptions", "usage_ledger")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analytics_events (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            event_name VARCHAR(80) NOT NULL,
            dimensions_json JSONB NOT NULL DEFAULT '{}',
            metrics_json JSONB NOT NULL DEFAULT '{}',
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE daily_metric_rollups (
            metric_date DATE NOT NULL,
            tenant_id UUID NOT NULL,
            metric_code VARCHAR(80) NOT NULL,
            metric_value NUMERIC(14,4) NOT NULL,
            PRIMARY KEY (metric_date, tenant_id, metric_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE subscription_plans (
            code VARCHAR(40) PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            price_vnd BIGINT NOT NULL,
            quotas_json JSONB NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE subscriptions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
            membership_id UUID NOT NULL,
            plan_code VARCHAR(40) NOT NULL REFERENCES subscription_plans(code),
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
            current_period_end TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_subscriptions_status CHECK (status IN ('ACTIVE', 'PAST_DUE', 'CANCELLED', 'EXPIRED')),
            CONSTRAINT fk_subscriptions_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE usage_ledger (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            resource_code VARCHAR(60) NOT NULL,
            delta INT NOT NULL,
            idempotency_key VARCHAR(128) UNIQUE NOT NULL,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_usage_ledger_membership_tenant
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE payment_webhooks (
            webhook_id VARCHAR(128) PRIMARY KEY,
            provider VARCHAR(40) NOT NULL,
            raw_payload_json JSONB NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE vertical_packs (
            code VARCHAR(60) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            manifest_json JSONB NOT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE locale_bundles (
            locale_code VARCHAR(20) NOT NULL,
            namespace VARCHAR(60) NOT NULL,
            messages_json JSONB NOT NULL,
            PRIMARY KEY (locale_code, namespace)
        )
        """
    )

    # --- row-level security ---------------------------------------------------------------------
    statements = []
    for table in STRICT_TABLES:
        statements += strict_tenant_rls(table)
    statements += system_job_policy("analytics_events", "sys_rollup_read", "metric_rollup", "SELECT")
    statements += system_job_policy("daily_metric_rollups", "sys_rollup_write", "metric_rollup", "ALL")
    for stmt in statements:
        op.execute(stmt)

    # --- privileges (section 5) ----------------------------------------------------------------
    op.execute(f"REVOKE ALL ON {', '.join(TABLES)} FROM PUBLIC")
    op.execute("GRANT SELECT, INSERT ON analytics_events, usage_ledger TO ulp_app_rw")  # 5a: append-only logs
    op.execute("GRANT SELECT ON daily_metric_rollups, subscription_plans TO ulp_app_rw")  # rollups: 5a revokes writes
    op.execute("GRANT SELECT, INSERT, UPDATE ON subscriptions, vertical_packs, locale_bundles TO ulp_app_rw")
    op.execute("GRANT INSERT ON payment_webhooks TO ulp_app_rw")  # Decision #7: INSERT only
    op.execute("GRANT SELECT ON analytics_events TO ulp_system_scheduler")  # 5e
    op.execute("GRANT SELECT, INSERT, UPDATE ON daily_metric_rollups TO ulp_system_scheduler")
    op.execute("GRANT SELECT ON analytics_events, daily_metric_rollups TO ulp_analytics_ro")  # 5f


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
