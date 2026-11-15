"""PostgreSQL + RLS + FastAPI integration tests for TASK-34 (Sprint 12):
- PEP 562 lazy-loading (Pure Core imports stay DB/ORM-free)
- G04 analytics (`analytics_events`, `daily_metric_rollups`, `ulp_system_scheduler` + `sys_rollup_read/write`,
  `ulp_app_rw` write denial on rollups, `ulp_analytics_ro` KPI/yield queries, event dedup & RLS)
- G05 billing (`subscription_plans`, `subscriptions`, `usage_ledger`, `payment_webhooks`, HMAC verification,
  idempotent webhook replay zero double-grant + outbox `subscription.updated`, idempotent quota ledger & RLS)
- G06 vertical_packs (`vertical_packs`, `locale_bundles`, non-invasive activation, overlapping-prefix guard,
  locale fallback chain)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.config import get_settings
from app.db import SessionLocal
from app.modules.analytics.internal.models import AnalyticsEventRecord, DailyMetricRollupRecord  # noqa: F401
from app.modules.analytics.public import run_daily_metric_rollup
from app.modules.analytics.router import router as analytics_router
from app.modules.billing.internal.models import (  # noqa: F401
    PaymentWebhookRecord,
    SubscriptionPlanRecord,
    SubscriptionRecord,
    UsageLedgerRecord,
)
from app.modules.billing.public import sign_webhook_payload
from app.modules.billing.router import router as billing_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.vertical_packs.internal.models import LocaleBundleRecord, VerticalPackRecord  # noqa: F401
from app.modules.vertical_packs.router import router as vertical_packs_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, SystemJob, system_job_transaction, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(analytics_router)
    test_app.include_router(billing_router)
    test_app.include_router(vertical_packs_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["ADMIN"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Seed two isolated tenants with staff & learner memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        ua_adm, ua_lrn, ub_adm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ma_adm, ma_lrn, mb_adm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t34-a-{ta.hex[:8]}", name="School 34A"),
                    Tenant(id=tb, slug=f"t34-b-{tb.hex[:8]}", name="School 34B"),
                    User(id=ua_adm, email_blind_index=f"{ua_adm.hex}{ua_adm.hex}", pseudonym_handle="Admin34A"),
                    User(id=ua_lrn, email_blind_index=f"{ua_lrn.hex}{ua_lrn.hex}", pseudonym_handle="Learner34A"),
                    User(id=ub_adm, email_blind_index=f"{ub_adm.hex}{ub_adm.hex}", pseudonym_handle="Admin34B"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=ma_adm, tenant_id=ta, user_id=ua_adm, roles=["ADMIN"]),
                    Membership(id=ma_lrn, tenant_id=ta, user_id=ua_lrn, roles=["LEARNER"]),
                    Membership(id=mb_adm, tenant_id=tb, user_id=ub_adm, roles=["ADMIN"]),
                ]
            )
        return {
            "ta": ta,
            "tb": tb,
            "ua_adm": ua_adm,
            "ua_lrn": ua_lrn,
            "ub_adm": ub_adm,
            "ma_adm": ma_adm,
            "ma_lrn": ma_lrn,
            "mb_adm": mb_adm,
        }

    return run(create())


def test_task34_pep562_lazy_loading_keeps_pure_core_db_free() -> None:
    """Pure Core exports in analytics, billing & vertical_packs must not load DB/ORM modules."""
    script = """
import sys
from app.modules.analytics.public import compute_daily_metric_rollup, AnalyticsEventType
from app.modules.billing.public import process_payment_webhook, consume_quota_idempotent, sign_webhook_payload
from app.modules.vertical_packs.public import VerticalPackRegistry, resolve_localized_label

forbidden = [
    "sqlalchemy.ext.asyncio",
    "app.db",
    "app.modules.analytics.internal.models",
    "app.modules.analytics.internal.service",
    "app.modules.billing.internal.models",
    "app.modules.billing.internal.service",
    "app.modules.vertical_packs.internal.models",
    "app.modules.vertical_packs.internal.service",
]
loaded = [m for m in forbidden if m in sys.modules]
assert not loaded, f"Unexpected DB modules loaded on Pure Core import: {loaded}"

from app.modules.analytics import public as analytics_pub
from app.modules.billing import public as billing_pub
from app.modules.vertical_packs import public as vp_pub
assert analytics_pub.DailyMetricRollupRecord.__tablename__ == "daily_metric_rollups"
assert billing_pub.PaymentWebhookRecord.__tablename__ == "payment_webhooks"
assert vp_pub.VerticalPackRecord.__tablename__ == "vertical_packs"
print("OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    assert "OK" in proc.stdout


def test_analytics_scheduler_rollup_role_grants_and_readonly_kpis(world: dict[str, uuid.UUID]) -> None:
    """G04 separates APP_RW event writes, SYSTEM_SCHEDULER rollups (`metric_rollup`), and ANALYTICS_RO KPI reads."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_adm_a = _auth_headers(tenant_id=ta, membership_id=world["ma_adm"], user_id=world["ua_adm"], roles=["ADMIN"])
    headers_lrn_a = _auth_headers(
        tenant_id=ta, membership_id=world["ma_lrn"], user_id=world["ua_lrn"], roles=["LEARNER"]
    )
    headers_adm_b = _auth_headers(tenant_id=tb, membership_id=world["mb_adm"], user_id=world["ub_adm"], roles=["ADMIN"])

    target_day = date(2026, 11, 15)
    prev_day = date(2026, 11, 13)
    learner_1 = world["ma_lrn"]
    learner_2 = uuid.uuid4()

    events_a = [
        {
            "event_type": "PRACTICE_COMPLETED",
            "learner_id": str(learner_1),
            "event_id": "ev-a-1",
            "occurred_at": datetime(2026, 11, 15, 8, 0, tzinfo=UTC).isoformat(),
        },
        # Duplicate event_id replay must only count once
        {
            "event_type": "PRACTICE_COMPLETED",
            "learner_id": str(learner_1),
            "event_id": "ev-a-1",
            "occurred_at": datetime(2026, 11, 15, 8, 1, tzinfo=UTC).isoformat(),
        },
        {
            "event_type": "EXAM_SUBMITTED",
            "learner_id": str(learner_2),
            "event_id": "ev-a-2",
            "occurred_at": datetime(prev_day.year, prev_day.month, prev_day.day, 10, 0, tzinfo=UTC).isoformat(),
        },
        {
            "event_type": "PIPELINE_EXTRACTED",
            "count": 100,
            "event_id": "ev-a-ext",
            "occurred_at": datetime(2026, 11, 15, 9, 0, tzinfo=UTC).isoformat(),
        },
        {
            "event_type": "PIPELINE_AUTO_VERIFIED",
            "count": 82,
            "event_id": "ev-a-auto",
            "occurred_at": datetime(2026, 11, 15, 9, 5, tzinfo=UTC).isoformat(),
        },
        {
            "event_type": "PIPELINE_MANUAL_EDITED",
            "count": 12,
            "event_id": "ev-a-edit",
            "occurred_at": datetime(2026, 11, 15, 9, 10, tzinfo=UTC).isoformat(),
        },
        {
            "event_type": "MASTERY_DELTA",
            "pre_mastery": 0.40,
            "post_mastery": 0.70,
            "event_id": "ev-a-gain",
            "occurred_at": datetime(2026, 11, 15, 11, 0, tzinfo=UTC).isoformat(),
        },
    ]
    for ev in events_a:
        resp = client.post("/api/v1/analytics/events", headers=headers_adm_a, json=ev)
        assert resp.status_code == 201, resp.text

    resp_b = client.post(
        "/api/v1/analytics/events",
        headers=headers_adm_b,
        json={
            "event_type": "PIPELINE_EXTRACTED",
            "count": 20,
            "event_id": "ev-b-ext",
            "occurred_at": datetime(2026, 11, 15, 9, 0, tzinfo=UTC).isoformat(),
        },
    )
    assert resp_b.status_code == 201

    # Verify least-privilege grants: APP_RW & ANALYTICS_RO cannot mutate `daily_metric_rollups`
    async def verify_role_permissions_and_run_scheduler() -> None:
        with pytest.raises(ProgrammingError):
            async with tenant_transaction(ta, role=DbRole.APP_RW) as sess_rw:
                await sess_rw.execute(
                    text(
                        "INSERT INTO daily_metric_rollups (metric_date, tenant_id, metric_code, metric_value) "
                        "VALUES (:d, :t, 'DAU', 999)"
                    ),
                    {"d": target_day, "t": ta},
                )

        with pytest.raises(ProgrammingError):
            async with tenant_transaction(ta, role=DbRole.ANALYTICS_RO) as sess_ro:
                await sess_ro.execute(
                    text(
                        "INSERT INTO daily_metric_rollups (metric_date, tenant_id, metric_code, metric_value) "
                        "VALUES (:d, :t, 'DAU', 999)"
                    ),
                    {"d": target_day, "t": ta},
                )

        # Wrong system job cannot read analytics_events (`sys_rollup_read` requires 'metric_rollup')
        async with system_job_transaction(SystemJob.OUTBOX_RELAY, role=DbRole.SYSTEM_SCHEDULER) as sess_wrong_job:
            cnt = (await sess_wrong_job.execute(text("SELECT count(*) FROM analytics_events"))).scalar_one()
            assert cnt == 0

        # Valid SYSTEM_SCHEDULER + metric_rollup aggregates both tenants cleanly under RLS
        async with system_job_transaction(SystemJob.METRIC_ROLLUP, role=DbRole.SYSTEM_SCHEDULER) as sess_sched:
            await run_daily_metric_rollup(sess_sched, metric_date=target_day, tenant_ids=(ta, tb))

    run(verify_role_permissions_and_run_scheduler())

    # Learner role blocked from KPI dashboard
    assert client.get("/api/v1/analytics/platform-kpis", headers=headers_lrn_a).status_code == 403

    # Query KPIs & pipeline yield via ANALYTICS_RO
    kpi_a = client.get("/api/v1/analytics/platform-kpis?metric_date=2026-11-15", headers=headers_adm_a)
    assert kpi_a.status_code == 200, kpi_a.text
    body_a = kpi_a.json()
    assert body_a["dau_count"] == 1
    assert body_a["wlau_count"] == 2
    assert body_a["extracted_total"] == 100
    assert body_a["auto_verified_total"] == 82
    assert body_a["manual_edited_total"] == 12
    assert body_a["pipeline_yield_rate"] == pytest.approx(0.82)
    assert body_a["manual_edit_rate"] == pytest.approx(0.12)
    assert body_a["avg_mastery_gain"] == pytest.approx(0.30)

    yield_a = client.get(
        "/api/v1/analytics/pipeline-yield?start_date=2026-11-14&end_date=2026-11-15",
        headers=headers_adm_a,
    )
    assert yield_a.status_code == 200
    assert len(yield_a.json()["items"]) == 1
    assert yield_a.json()["items"][0]["extracted_total"] == 100

    # Tenant B is completely isolated from Tenant A's rollups
    kpi_b = client.get("/api/v1/analytics/platform-kpis?metric_date=2026-11-15", headers=headers_adm_b)
    assert kpi_b.status_code == 200
    assert kpi_b.json()["extracted_total"] == 20
    assert kpi_b.json()["dau_count"] == 0


def test_billing_hmac_webhook_idempotency_outbox_and_quota(world: dict[str, uuid.UUID]) -> None:
    """G05 enforces HMAC signatures, idempotent webhook replay, `subscription.updated` outbox & quota caps."""
    client = _api_client()
    settings = get_settings()
    ta, tb = world["ta"], world["tb"]
    ma_lrn = world["ma_lrn"]
    headers_lrn_a = _auth_headers(tenant_id=ta, membership_id=ma_lrn, user_id=world["ua_lrn"], roles=["LEARNER"])
    headers_adm_b = _auth_headers(tenant_id=tb, membership_id=world["mb_adm"], user_id=world["ub_adm"], roles=["ADMIN"])

    async def seed_plans() -> None:
        async with SessionLocal() as session, session.begin():
            for code, name, price, quotas in (
                ("FREE", "Free Plan", 0, {"BRANDED_PDF_EXPORT": 2}),
                ("STUDENT_PRO", "Student Pro", 199000, {"BRANDED_PDF_EXPORT": 50}),
                ("TEACHER_PRO", "Teacher Pro", 499000, {"BRANDED_PDF_EXPORT": 200}),
            ):
                await session.execute(
                    text(
                        "INSERT INTO subscription_plans (code, name, price_vnd, quotas_json) "
                        "VALUES (:code, :name, :price, CAST(:quotas AS jsonb)) "
                        "ON CONFLICT (code) DO NOTHING"
                    ),
                    {"code": code, "name": name, "price": price, "quotas": json.dumps(quotas)},
                )

    run(seed_plans())

    plans_res = client.get("/api/v1/plans", headers=headers_lrn_a)
    assert plans_res.status_code == 200
    assert [p["code"] for p in plans_res.json()["items"]] == ["FREE", "STUDENT_PRO", "TEACHER_PRO"]

    now_epoch = int(time.time())
    webhook_body = json.dumps(
        {
            "event_type": "payment.succeeded",
            "tenant_id": str(ta),
            "membership_id": str(ma_lrn),
            "plan_code": "STUDENT_PRO",
            "period_extension_days": 30,
        }
    ).encode("utf-8")

    # 1) Invalid HMAC signature -> 401 Unauthorized
    bad_sig = client.post(
        "/api/v1/webhooks/payment",
        content=webhook_body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Id": "wh-bad-sig",
            "X-Webhook-Provider": "vnpay",
            "X-Webhook-Timestamp": str(now_epoch),
            "X-Webhook-Signature": "0" * 64,
        },
    )
    assert bad_sig.status_code == 401

    # 2) Stale replay window timestamp -> 422
    stale_epoch = now_epoch - 600
    stale_sig = sign_webhook_payload(
        webhook_id="wh-stale",
        timestamp_unix=stale_epoch,
        raw_payload=webhook_body,
        secret=settings.secret_key,
    )
    stale_res = client.post(
        "/api/v1/webhooks/payment",
        content=webhook_body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Id": "wh-stale",
            "X-Webhook-Provider": "vnpay",
            "X-Webhook-Timestamp": str(stale_epoch),
            "X-Webhook-Signature": stale_sig,
        },
    )
    assert stale_res.status_code == 422

    # 3) First valid delivery activates STUDENT_PRO + emits subscription.updated outbox event
    wh_id = f"wh-ok-{uuid.uuid4().hex[:8]}"
    valid_sig = sign_webhook_payload(
        webhook_id=wh_id,
        timestamp_unix=now_epoch,
        raw_payload=webhook_body,
        secret=settings.secret_key,
    )
    wh_headers = {
        "Content-Type": "application/json",
        "X-Webhook-Id": wh_id,
        "X-Webhook-Provider": "vnpay",
        "X-Webhook-Timestamp": str(now_epoch),
        "X-Webhook-Signature": valid_sig,
    }
    first = client.post("/api/v1/webhooks/payment", content=webhook_body, headers=wh_headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["is_duplicate"] is False
    assert first_body["emitted_outbox_event"] is True
    assert first_body["subscription"]["plan_code"] == "STUDENT_PRO"
    assert first_body["subscription"]["status"] == "ACTIVE"
    first_period_end = first_body["subscription"]["current_period_end"]

    # 4) Duplicate webhook replay must NOT extend period again and must emit zero duplicate outbox events
    replay = client.post("/api/v1/webhooks/payment", content=webhook_body, headers=wh_headers)
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["is_duplicate"] is True
    assert replay_body["emitted_outbox_event"] is False
    assert replay_body["subscription"]["current_period_end"] == first_period_end

    async def verify_outbox_count() -> int:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            return int(
                (
                    await session_a.execute(
                        text("SELECT count(*) FROM outbox_events WHERE event_type = 'subscription.updated'")
                    )
                ).scalar_one()
            )

    assert run(verify_outbox_count()) == 1

    # 5) Quota consumption with idempotency_key deduplication & limit enforcement
    use1 = client.post(
        "/api/v1/usage/consume",
        headers=headers_lrn_a,
        json={"resource_code": "BRANDED_PDF_EXPORT", "delta": 3, "idempotency_key": "export-idem-1"},
    )
    assert use1.status_code == 200, use1.text
    assert use1.json()["was_charged_now"] is True
    assert use1.json()["used_total"] == 3
    assert use1.json()["remaining_quota"] == 47

    use1_replay = client.post(
        "/api/v1/usage/consume",
        headers=headers_lrn_a,
        json={"resource_code": "BRANDED_PDF_EXPORT", "delta": 3, "idempotency_key": "export-idem-1"},
    )
    assert use1_replay.status_code == 200
    assert use1_replay.json()["was_charged_now"] is False
    assert use1_replay.json()["remaining_quota"] == 47

    use_mismatch = client.post(
        "/api/v1/usage/consume",
        headers=headers_lrn_a,
        json={"resource_code": "BRANDED_PDF_EXPORT", "delta": 5, "idempotency_key": "export-idem-1"},
    )
    assert use_mismatch.status_code == 422

    over_quota = client.post(
        "/api/v1/usage/consume",
        headers=headers_lrn_a,
        json={"resource_code": "BRANDED_PDF_EXPORT", "delta": 48, "idempotency_key": "export-idem-2"},
    )
    assert over_quota.status_code == 429

    # 6) RLS hides tenant A subscriptions and usage ledger from tenant B
    assert client.get("/api/v1/subscriptions", headers=headers_adm_b).json()["items"] == []


def test_vertical_packs_activation_isolation_and_locale_fallback(world: dict[str, uuid.UUID]) -> None:
    """G06 activates expansion packs non-invasively, blocks overlapping prefixes & resolves locales."""
    client = _api_client()
    ta = world["ta"]
    # Packs and locale bundles are platform catalogues: only a platform administrator writes them.
    headers_adm = _auth_headers(
        tenant_id=ta, membership_id=world["ma_adm"], user_id=world["ua_adm"], roles=["PLATFORM_ADMIN"]
    )
    headers_tenant_admin = _auth_headers(
        tenant_id=ta, membership_id=world["ma_adm"], user_id=world["ua_adm"], roles=["TENANT_ADMIN", "ADMIN"]
    )
    headers_lrn = _auth_headers(tenant_id=ta, membership_id=world["ma_lrn"], user_id=world["ua_lrn"], roles=["LEARNER"])

    # A tenant administrator is blocked from every write route (valid bodies: only the role can stop it)
    pack_body = {
        "code": "TENANT_TRY",
        "name": "Tenant try",
        "taxonomy_root_prefix": "TT",
        "assessment_framework_code": "THPT_2025",
        "scoring_policy_code": "THPT_025_STEP",
    }
    locale_body = {"locale_code": "vi-VN", "namespace": "common", "messages": {"k": "v"}}
    for method, url, body in (
        ("post", "/api/v1/vertical-packs", pack_body),
        ("post", "/api/v1/vertical-packs/K12_VN_KNTT/activate", None),
        ("post", "/api/v1/vertical-packs/K12_VN_KNTT/deactivate", None),
        ("put", "/api/v1/vertical-packs/locales", locale_body),
    ):
        kwargs = {"json": body} if body is not None else {}
        blocked = getattr(client, method)(url, headers=headers_tenant_admin, **kwargs)
        assert blocked.status_code == 403, (method, url, blocked.text)

    # Non-admin blocked from registering packs
    forbid = client.post(
        "/api/v1/vertical-packs",
        headers=headers_lrn,
        json={
            "code": "IELTS_ACADEMIC",
            "name": "IELTS Academic",
            "taxonomy_root_prefix": "IELTS",
            "assessment_framework_code": "IELTS_BAND_9",
            "scoring_policy_code": "IELTS_AVG_05",
        },
    )
    assert forbid.status_code == 403

    # Register Core K-12 pack & IELTS pack (initially inactive)
    reg_k12 = client.post(
        "/api/v1/vertical-packs",
        headers=headers_adm,
        json={
            "code": "K12_VN_KNTT",
            "name": "K-12 Vietnam KNTT",
            "taxonomy_root_prefix": "K12_VN",
            "assessment_framework_code": "THPT_2025",
            "scoring_policy_code": "THPT_025_STEP",
            "default_locale": "vi-VN",
            "is_core_k12": True,
            "is_enabled": True,
        },
    )
    assert reg_k12.status_code == 201, reg_k12.text

    reg_ielts = client.post(
        "/api/v1/vertical-packs",
        headers=headers_adm,
        json={
            "code": "IELTS_ACADEMIC",
            "name": "IELTS Academic",
            "taxonomy_root_prefix": "IELTS",
            "assessment_framework_code": "IELTS_BAND_9",
            "scoring_policy_code": "IELTS_AVG_05",
            "default_locale": "en-GB",
            "is_core_k12": False,
            "is_enabled": False,
        },
    )
    assert reg_ielts.status_code == 201, reg_ielts.text
    assert reg_ielts.json()["is_enabled"] is False

    # Overlapping taxonomy prefix ('IELTS_GT' nests under 'IELTS') must fail closed with 422
    overlap = client.post(
        "/api/v1/vertical-packs",
        headers=headers_adm,
        json={
            "code": "IELTS_GENERAL",
            "name": "IELTS General",
            "taxonomy_root_prefix": "IELTS_GT",
            "assessment_framework_code": "IELTS_GT_9",
            "scoring_policy_code": "IELTS_AVG_05",
        },
    )
    assert overlap.status_code == 422

    # Non-invasive activation enables IELTS_ACADEMIC alongside active K12_VN_KNTT
    act = client.post("/api/v1/vertical-packs/IELTS_ACADEMIC/activate", headers=headers_adm)
    assert act.status_code == 200
    assert act.json()["is_enabled"] is True

    active_list = client.get("/api/v1/vertical-packs?active_only=true", headers=headers_lrn)
    assert active_list.status_code == 200
    assert {p["code"] for p in active_list.json()["items"]} == {"K12_VN_KNTT", "IELTS_ACADEMIC"}

    # Core K-12 pack cannot be deactivated
    deact_k12 = client.post("/api/v1/vertical-packs/K12_VN_KNTT/deactivate", headers=headers_adm)
    assert deact_k12.status_code == 422

    # Locale bundles + exact -> base language -> default vi-VN fallback
    for loc, msgs in (
        ("vi-VN", {"submit_btn": "Nop bai", "fallback_only": "Mac dinh tieng Viet"}),
        ("en", {"submit_btn": "Submit", "base_only": "Base English"}),
        ("en-GB", {"submit_btn": "Submit Assessment"}),
    ):
        put_res = client.put(
            "/api/v1/vertical-packs/locales",
            headers=headers_adm,
            json={"locale_code": loc, "namespace": "common", "messages": msgs},
        )
        assert put_res.status_code == 200, put_res.text

    exact = client.get("/api/v1/vertical-packs/labels/resolve?key=submit_btn&locale=en-GB", headers=headers_lrn)
    assert exact.json()["resolved_label"] == "Submit Assessment"

    base_fb = client.get("/api/v1/vertical-packs/labels/resolve?key=base_only&locale=en-GB", headers=headers_lrn)
    assert base_fb.json()["resolved_label"] == "Base English"

    default_fb = client.get(
        "/api/v1/vertical-packs/labels/resolve?key=fallback_only&locale=en-GB",
        headers=headers_lrn,
    )
    assert default_fb.json()["resolved_label"] == "Mac dinh tieng Viet"


def test_special_grants_payment_webhooks_insert_only_rollups_read_only_and_job_policies() -> None:
    """Decision #7 and section 5: the API role only INSERTs webhooks and only reads rollups; the rollup job is
    reachable through its two system-job policies and nothing else."""

    async def privileges() -> dict[str, bool]:
        async with SessionLocal() as session:
            out = {}
            for role, table, priv in (
                ("ulp_app_rw", "payment_webhooks", "INSERT"),
                ("ulp_app_rw", "payment_webhooks", "SELECT"),
                ("ulp_app_rw", "payment_webhooks", "UPDATE"),
                ("ulp_app_rw", "payment_webhooks", "DELETE"),
                ("ulp_app_rw", "daily_metric_rollups", "SELECT"),
                ("ulp_app_rw", "daily_metric_rollups", "INSERT"),
                ("ulp_app_rw", "daily_metric_rollups", "UPDATE"),
                ("ulp_system_scheduler", "daily_metric_rollups", "UPDATE"),
                ("ulp_system_scheduler", "analytics_events", "SELECT"),
                ("ulp_system_scheduler", "analytics_events", "INSERT"),
                ("ulp_analytics_ro", "daily_metric_rollups", "SELECT"),
            ):
                out[f"{role}:{table}:{priv}"] = (
                    await session.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": table, "p": priv}
                    )
                ).scalar_one()
            return out

    got = run(privileges())
    assert {k for k, v in got.items() if v} == {
        "ulp_app_rw:payment_webhooks:INSERT",
        "ulp_app_rw:daily_metric_rollups:SELECT",
        "ulp_system_scheduler:daily_metric_rollups:UPDATE",
        "ulp_system_scheduler:analytics_events:SELECT",
        "ulp_analytics_ro:daily_metric_rollups:SELECT",
    }

    async def policies() -> set[tuple[str, str]]:
        async with SessionLocal() as session:
            rows = await session.execute(
                text("SELECT tablename, policyname FROM pg_policies WHERE policyname LIKE 'sys_rollup_%'")
            )
            return {(r.tablename, r.policyname) for r in rows}

    assert run(policies()) == {
        ("analytics_events", "sys_rollup_read"),
        ("daily_metric_rollups", "sys_rollup_write"),
    }
