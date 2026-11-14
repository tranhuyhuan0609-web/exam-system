"""E01 ai_gateway (TASK-13): jobs, daily budget, model choice with circuit breakers, traces, usage and keys.

DoD: a tenant that reached its daily budget is refused 100% of the time (429); a 429 on one key rotates to the
next in under 50 ms; a provider that keeps failing stops being chosen and its fallback takes over.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.ai_gateway.internal.service import _local_circuits
from app.modules.ai_gateway.public import (
    ApiKeyInput,
    DailyBudgetExceededError,
    GatewayKeyRings,
    JobStateError,
    allow_provider,
    budget_snapshot,
    complete_job,
    fail_job,
    record_trace,
    report_provider_outcome,
    select_models,
    start_job,
    submit_job,
    vn_day_bounds,
    vn_today,
)
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

M = Decimal  # model prices are USD per million tokens


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _all(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).all()


async def _execute(sql: str, **params) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(text(sql), params)


@pytest.fixture(autouse=True)
def _fresh_circuits():
    _local_circuits.clear()
    yield
    _local_circuits.clear()


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a = Tenant(slug=f"ai-a-{suffix}", name="A", daily_ai_budget_usd=Decimal("10.00"))
            b = Tenant(slug=f"ai-b-{suffix}", name="B", daily_ai_budget_usd=Decimal("10.00"))
            session.add_all([a, b])
            await session.flush()
            return {"a": a.id, "b": b.id, "user": uuid.uuid4(), "other": uuid.uuid4()}

    return run(create())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(tenant: uuid.UUID, user: uuid.UUID, roles: tuple[str, ...] = ("TEACHER",)) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user,
        tenant_id=tenant,
        membership_id=uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}"}


def _provider(code: str) -> str:
    run(
        _execute(
            "INSERT INTO ai_providers (code, base_url) VALUES (:c, 'https://x.example') ON CONFLICT DO NOTHING", c=code
        )
    )
    return code


def _model(provider="gemini", name="flash", role="TUTOR_CHAT", in_cost="1.0", out_cost="4.0", active=True) -> uuid.UUID:
    async def create():
        async with SessionLocal() as session, session.begin():
            return (
                await session.execute(
                    text(
                        "INSERT INTO ai_models (provider_code, model_name, task_role, input_cost_per_1m, "
                        "output_cost_per_1m, is_active) VALUES (:p, :n, :r, :i, :o, :a) RETURNING id"
                    ),
                    {"p": provider, "n": name, "r": role, "i": M(in_cost), "o": M(out_cost), "a": active},
                )
            ).scalar_one()

    return run(create())


def _budget(tenant: uuid.UUID, usd: str) -> None:
    run(_execute("UPDATE tenants SET daily_ai_budget_usd = :b WHERE id = :t", b=M(usd), t=tenant))


async def _submit(tenant, user, **kw):
    async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
        return await submit_job(session, tenant_id=tenant, requested_by=user, **kw)


async def _trace(tenant, model, prompt, completion, **kw):
    async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
        return await record_trace(
            session, tenant_id=tenant, model_id=model, prompt_tokens=prompt, completion_tokens=completion,
            latency_ms=120, **kw,
        )  # fmt: skip


# --- schema ----------------------------------------------------------------------------------------


def test_catalogs_are_global_and_jobs_and_traces_are_tenant_tables():
    for table in ("ai_providers", "ai_models", "prompt_versions"):
        assert run(_scalar("SELECT relrowsecurity FROM pg_class WHERE relname = :t", t=table)) is False, table
    for table in ("ai_jobs", "ai_traces"):
        assert run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
        qual = run(
            _scalar(
                "SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation_policy'", t=table
            )
        )
        assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in qual
    assert run(_scalar("SELECT count(*) FROM ai_providers WHERE code = 'gemini'")) == 1  # seeded


def test_privileges():
    def has(role, table, privilege):
        return run(_scalar("SELECT has_table_privilege(:r, :t, :p)", r=role, t=table, p=privilege))

    for table in ("ai_providers", "ai_models", "prompt_versions"):
        assert has("ulp_app_rw", table, "SELECT")
        assert not has("ulp_app_rw", table, "INSERT") and not has("ulp_app_rw", table, "DELETE")
    assert run(_scalar("SELECT has_column_privilege('ulp_app_rw', 'ai_providers', 'circuit_state', 'UPDATE')"))
    assert not run(_scalar("SELECT has_column_privilege('ulp_app_rw', 'ai_providers', 'base_url', 'UPDATE')"))
    assert has("ulp_app_rw", "ai_traces", "INSERT")
    assert not has("ulp_app_rw", "ai_traces", "UPDATE") and not has("ulp_app_rw", "ai_traces", "DELETE")
    for role in ("ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_worker_grading", "ulp_analytics_ro"):
        assert not has(role, "ai_jobs", "SELECT"), role


# --- POST /api/v1/ai/jobs ------------------------------------------------------------------------


def test_submit_queues_a_job_with_a_cost_estimate_and_ordered_models(client, world):
    cheap = _model(name="flash-lite", in_cost="0.10", out_cost="0.40")
    pricey = _model(name="flash", in_cost="0.30", out_cost="2.50")
    response = client.post(
        "/api/v1/ai/jobs",
        headers=_headers(world["a"], world["user"]),
        json={
            "task_type": "TUTOR_HINT", "task_role": "TUTOR_CHAT", "input_refs": {"question_version_id": "q1"},
            "estimated_prompt_tokens": 2_000_000, "estimated_completion_tokens": 500_000,
        },
    )  # fmt: skip
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "QUEUED" and body["tenant_id"] == str(world["a"]) and body["task_role"] == "TUTOR_CHAT"
    assert body["input_refs"] == {"question_version_id": "q1"}  # the gateway's own bookkeeping is not echoed
    assert body["primary_model_id"] == str(cheap) and body["fallback_model_ids"] == [str(pricey)]
    assert Decimal(body["estimated_cost_usd"]) == M("0.4")  # 2M * 0.10 + 0.5M * 0.40, per million


def test_submit_without_a_role_has_no_estimate_and_no_model(client, world):
    response = client.post(
        "/api/v1/ai/jobs", headers=_headers(world["a"], world["user"]), json={"task_type": "OCR_PAGE"}
    )
    assert response.status_code == 201
    body = response.json()
    assert (
        Decimal(body["estimated_cost_usd"]) == 0
        and body["primary_model_id"] is None
        and body["fallback_model_ids"] == []
    )


def test_validation(client, world):
    headers = _headers(world["a"], world["user"])
    base = {"task_type": "OCR_PAGE"}
    for bad in (
        {**base, "task_type": "lower case"},
        {**base, "task_role": "WRITE_NOVEL"},
        {**base, "input_refs": {"_gateway": {"estimated_cost_usd": "0"}}},  # cannot forge the reservation
        {**base, "estimated_prompt_tokens": -1},
        {**base, "unknown": 1},
    ):
        assert client.post("/api/v1/ai/jobs", headers=headers, json=bad).status_code == 422, bad
    assert client.post("/api/v1/ai/jobs", json=base).status_code == 401
    spoofed = {**headers, "X-Tenant-Id": str(world["b"])}
    assert client.post("/api/v1/ai/jobs", headers=spoofed, json=base).status_code == 403
    assert run(_scalar("SELECT count(*) FROM ai_jobs")) == 0


def test_unknown_prompt_and_no_model_for_the_role(client, world):
    headers = _headers(world["a"], world["user"])
    missing_prompt = client.post("/api/v1/ai/jobs", headers=headers, json={"task_type": "X", "prompt_code": "nope"})
    assert missing_prompt.status_code == 404
    no_model = client.post("/api/v1/ai/jobs", headers=headers, json={"task_type": "X", "task_role": "OCR_VISION"})
    assert no_model.status_code == 503 and "OCR_VISION" in no_model.json()["detail"]
    assert run(_scalar("SELECT count(*) FROM ai_jobs")) == 0


def test_the_newest_prompt_version_is_used(client, world):
    run(
        _execute(
            "INSERT INTO prompt_versions (code, version_no, system_prompt, user_template) "
            "VALUES ('healer', 1, 's1', 'u1')"
        )
    )
    run(
        _execute(
            "INSERT INTO prompt_versions (code, version_no, system_prompt, user_template) "
            "VALUES ('healer', 2, 's2', 'u2')"
        )
    )
    newest = run(_scalar("SELECT id FROM prompt_versions WHERE code = 'healer' AND version_no = 2"))
    body = client.post(
        "/api/v1/ai/jobs",
        headers=_headers(world["a"], world["user"]),
        json={"task_type": "HEAL", "prompt_code": "healer"},
    ).json()
    assert body["prompt_version_id"] == str(newest)


# --- budget (DoD) ---------------------------------------------------------------------------------


def test_a_tenant_at_its_ceiling_is_refused_every_time(client, world):
    model = _model(in_cost="1.0", out_cost="4.0")
    _budget(world["a"], "1.00")
    run(_trace(world["a"], model, 1_000_000, 0))  # spends exactly 1.00
    headers = _headers(world["a"], world["user"])
    for payload in ({"task_type": "T"}, {"task_type": "T", "task_role": "TUTOR_CHAT"}):
        for _ in range(3):
            response = client.post("/api/v1/ai/jobs", headers=headers, json=payload)
            assert response.status_code == 429, response.text
            assert response.headers["content-type"].startswith("application/problem+json")
            assert "daily_ai_budget_usd" in response.json()["detail"]
    assert run(_scalar("SELECT count(*) FROM ai_jobs")) == 0
    # another tenant is unaffected
    assert (
        client.post(
            "/api/v1/ai/jobs", headers=_headers(world["b"], world["other"]), json={"task_type": "T"}
        ).status_code
        == 201
    )


def test_a_zero_budget_blocks_everything(client, world):
    _budget(world["a"], "0.00")
    assert (
        client.post("/api/v1/ai/jobs", headers=_headers(world["a"], world["user"]), json={"task_type": "T"}).status_code
        == 429
    )


def test_reservations_count_until_the_job_finishes(client, world):
    model = _model(in_cost="1.0", out_cost="1.0")
    _budget(world["a"], "1.00")
    headers = _headers(world["a"], world["user"])
    big = {"task_type": "T", "task_role": "TUTOR_CHAT", "estimated_prompt_tokens": 600_000}
    first = client.post("/api/v1/ai/jobs", headers=headers, json=big)
    assert first.status_code == 201 and Decimal(first.json()["estimated_cost_usd"]) == M("0.6")
    assert client.post("/api/v1/ai/jobs", headers=headers, json=big).status_code == 429  # 0.6 reserved + 0.6 > 1.0
    small = client.post("/api/v1/ai/jobs", headers=headers, json={**big, "estimated_prompt_tokens": 300_000})
    assert small.status_code == 201  # 0.6 + 0.3 fits

    async def finish():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            job_id = uuid.UUID(first.json()["id"])
            await start_job(session, job_id)
            await complete_job(session, job_id, {"ok": True})
            return await budget_snapshot(session, world["a"])

    after = run(finish())
    assert after.reserved_usd == M("0.3")  # only the small job still holds money
    assert after.spent_usd == 0 and after.remaining_usd == M("0.7")
    run(_trace(world["a"], model, 600_000, 0, ai_job_id=uuid.UUID(first.json()["id"])))
    assert (
        client.post("/api/v1/ai/jobs", headers=headers, json={**big, "estimated_prompt_tokens": 200_000}).status_code
        == 429
    )


def test_concurrent_submissions_cannot_squeeze_under_the_ceiling(world):
    _model(in_cost="1.0", out_cost="1.0")
    _budget(world["a"], "1.00")

    async def attempt():
        try:
            await _submit(
                world["a"], world["user"], task_type="T", task_role="TUTOR_CHAT", estimated_prompt_tokens=600_000
            )
            return "ok"
        except DailyBudgetExceededError:
            return "refused"

    async def race():
        return await asyncio.gather(*(attempt() for _ in range(6)))

    outcomes = run(race())
    assert outcomes.count("ok") == 1 and outcomes.count("refused") == 5
    assert run(_scalar("SELECT count(*) FROM ai_jobs")) == 1


def test_the_budget_day_is_the_vietnamese_day(world):
    model = _model(in_cost="1.0", out_cost="1.0")
    _budget(world["a"], "5.00")
    now = datetime.now(UTC)
    start, end = vn_day_bounds(vn_today(now))
    assert end - start == timedelta(days=1) and start.utcoffset() == timedelta(0)
    run(_trace(world["a"], model, 1_000_000, 0, now=start + timedelta(minutes=1)))  # first minute of today (VN)
    run(_trace(world["a"], model, 2_000_000, 0, now=start - timedelta(minutes=1)))  # last minute of yesterday (VN)

    async def snapshot(day=None):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return await budget_snapshot(session, world["a"], day=day)

    assert run(snapshot()).spent_usd == M("1.0")
    assert run(snapshot(vn_today(now) - timedelta(days=1))).spent_usd == M("2.0")


# --- traces ----------------------------------------------------------------------------------------


def test_a_trace_costs_tokens_times_prices_and_announces_thresholds(world):
    model = _model(in_cost="2.0", out_cost="8.0")
    _budget(world["a"], "10.00")
    first = run(_trace(world["a"], model, 3_000_000, 500_000))  # 3M * 2.0 + 0.5M * 8.0 = 10.0
    assert first.cost_usd == M("10.000000")
    events = run(
        _all(
            "SELECT payload_json FROM outbox_events "
            "WHERE event_type = 'ai.budget.threshold_exceeded' ORDER BY created_at"
        )
    )
    assert [e.payload_json["threshold_pct"] for e in events] == [80, 100]  # one trace crossed both
    assert (
        events[0].payload_json["daily_budget_usd"] == "10.00"
        and events[1].payload_json["current_spend_usd"] == "10.000000"
    )
    run(_trace(world["a"], model, 100, 100))  # already over: no new announcement
    assert run(_scalar("SELECT count(*) FROM outbox_events WHERE event_type = 'ai.budget.threshold_exceeded'")) == 2


def test_thresholds_fire_once_each_as_spend_grows(world):
    model = _model(in_cost="1.0", out_cost="0.0")
    _budget(world["a"], "10.00")
    for tokens in (5_000_000, 2_000_000, 1_500_000, 1_500_000):  # 5, 7, 8.5 (crosses 80), 10 (crosses 100)
        run(_trace(world["a"], model, tokens, 0))
    pcts = [
        r[0] for r in run(_all("SELECT (payload_json->>'threshold_pct')::int FROM outbox_events ORDER BY created_at"))
    ]
    assert pcts == [80, 100]


def test_trace_rejects_unknown_references_and_negative_numbers(world):
    model = _model()

    async def attempt(**kw):
        defaults = {
            "tenant_id": world["a"],
            "model_id": model,
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "latency_ms": 1,
        }
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await record_trace(session, **{**defaults, **kw})

    from app.modules.ai_gateway.public import JobNotFoundError, ModelNotFoundError

    with pytest.raises(ModelNotFoundError):
        run(attempt(model_id=uuid.uuid4()))
    with pytest.raises(JobNotFoundError):
        run(attempt(ai_job_id=uuid.uuid4()))
    with pytest.raises(ValueError, match=">= 0"):
        run(attempt(prompt_tokens=-1))
    assert run(_scalar("SELECT count(*) FROM ai_traces")) == 0


def test_traces_are_append_only_for_the_api_role(world):
    model = _model()
    run(_trace(world["a"], model, 10, 10))

    async def tamper(sql):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text(sql))

    for sql in ("UPDATE ai_traces SET cost_usd = 0", "DELETE FROM ai_traces"):
        with pytest.raises(DBAPIError, match="permission denied"):
            run(tamper(sql))


# --- jobs: state, visibility ------------------------------------------------------------------------


def test_job_state_machine(world):
    async def scenario():
        job = await _submit(world["a"], world["user"], task_type="T")
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            with pytest.raises(JobStateError):
                await complete_job(session, job.id, {})  # QUEUED cannot complete
            running = await start_job(session, job.id)
            done = await complete_job(session, job.id, {"answer": 42})
            with pytest.raises(JobStateError):
                await fail_job(session, job.id)  # COMPLETED is final
        return running.status, done.status, done.output

    assert run(scenario()) == ("RUNNING", "COMPLETED", {"answer": 42})


def test_a_job_is_readable_by_its_requester_and_by_administrators(client, world):
    job = client.post("/api/v1/ai/jobs", headers=_headers(world["a"], world["user"]), json={"task_type": "T"}).json()
    url = f"/api/v1/ai/jobs/{job['id']}"
    assert client.get(url, headers=_headers(world["a"], world["user"])).json()["id"] == job["id"]
    assert client.get(url, headers=_headers(world["a"], world["other"])).status_code == 404  # a colleague
    assert client.get(url, headers=_headers(world["a"], world["other"], ("TENANT_ADMIN",))).status_code == 200
    assert (
        client.get(url, headers=_headers(world["b"], world["user"], ("TENANT_ADMIN",))).status_code == 404
    )  # other tenant
    assert client.get(f"/api/v1/ai/jobs/{uuid.uuid4()}", headers=_headers(world["a"], world["user"])).status_code == 404
    assert client.get(url).status_code == 401


def test_another_tenant_sees_no_jobs_or_traces_in_the_database(world):
    model = _model()
    run(_submit(world["a"], world["user"], task_type="T"))
    run(_trace(world["a"], model, 10, 10))

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            return [
                (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar() for t in ("ai_jobs", "ai_traces")
            ]

    assert run(as_b()) == [0, 0]


# --- GET /api/v1/ai/usage -------------------------------------------------------------------------------


def test_usage_for_administrators_per_model(client, world):
    cheap, other = (
        _model(name="a-cheap", in_cost="1.0", out_cost="1.0"),
        _model(name="b-other", in_cost="2.0", out_cost="2.0"),
    )
    run(_trace(world["a"], cheap, 1_000_000, 0))
    run(_trace(world["a"], cheap, 500_000, 500_000))
    run(_trace(world["a"], other, 250_000, 0))
    run(_trace(world["b"], cheap, 9_000_000, 0))  # another tenant's spend is not ours
    client.post(
        "/api/v1/ai/jobs", headers=_headers(world["a"], world["user"]),
        json={"task_type": "T", "task_role": "TUTOR_CHAT", "estimated_prompt_tokens": 1_000_000},
    )  # fmt: skip
    response = client.get("/api/v1/ai/usage", headers=_headers(world["a"], world["user"], ("TENANT_ADMIN",)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == str(world["a"]) and Decimal(body["daily_ai_budget_usd"]) == M("10.00")
    assert Decimal(body["spent_usd"]) == M("2.5") and body["calls"] == 3
    assert Decimal(body["reserved_usd"]) == M("1.0")  # the queued job's estimate (cheapest model: a-cheap)
    assert Decimal(body["remaining_usd"]) == M("6.5")
    assert [m["model_name"] for m in body["by_model"]] == ["a-cheap", "b-other"]  # most expensive first
    assert body["by_model"][0]["calls"] == 2 and body["by_model"][0]["prompt_tokens"] == 1_500_000


def test_usage_by_day_and_authorization(client, world):
    model = _model()
    yesterday = vn_today() - timedelta(days=1)
    start, _ = vn_day_bounds(yesterday)
    run(_trace(world["a"], model, 1_000_000, 0, now=start + timedelta(hours=3)))
    admin = _headers(world["a"], world["user"], ("TENANT_ADMIN",))
    assert Decimal(client.get(f"/api/v1/ai/usage?day={yesterday}", headers=admin).json()["spent_usd"]) == M("1.0")
    assert Decimal(client.get("/api/v1/ai/usage", headers=admin).json()["spent_usd"]) == 0
    assert client.get("/api/v1/ai/usage", headers=_headers(world["a"], world["user"])).status_code == 403
    assert client.get("/api/v1/ai/usage").status_code == 401
    assert client.get("/api/v1/ai/usage?day=not-a-date", headers=admin).status_code == 422


# --- models and circuit breakers ----------------------------------------------------------------------


def test_select_models_orders_by_price_and_skips_inactive_ones(world):
    _model(name="mid", in_cost="1.0", out_cost="1.0")
    _model(name="cheap", in_cost="0.1", out_cost="0.1")
    _model(name="off", in_cost="0.01", out_cost="0.01", active=False)
    _model(name="other-role", role="OCR_VISION", in_cost="0.01", out_cost="0.01")

    async def names():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return [m.model_name for m in await select_models(session, "TUTOR_CHAT")]

    assert run(names()) == ["cheap", "mid"]


def test_a_failing_provider_opens_its_breaker_and_the_fallback_takes_over(client, world):
    _provider("backup")
    primary = _model(provider="gemini", name="gemini-flash", in_cost="0.1", out_cost="0.1")
    fallback = _model(provider="backup", name="backup-large", in_cost="1.0", out_cost="1.0")
    now = datetime.now(UTC)

    async def fail(times, **kw):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            states = []
            for i in range(times):
                states.append(
                    await report_provider_outcome(
                        session, "gemini", succeeded=False, latency_seconds=0.2, status_code=429,
                        now=now + timedelta(seconds=i), **kw,
                    )
                )  # fmt: skip
            return states

    states = run(fail(5))
    assert states == ["CLOSED"] * 4 + ["OPEN"]  # five 429/5xx in a row trip it
    assert run(_scalar("SELECT circuit_state FROM ai_providers WHERE code = 'gemini'")) == "OPEN"

    body = client.post(
        "/api/v1/ai/jobs",
        headers=_headers(world["a"], world["user"]),
        json={"task_type": "T", "task_role": "TUTOR_CHAT"},
    ).json()
    assert body["primary_model_id"] == str(fallback) and body["fallback_model_ids"] == []  # gemini is skipped
    assert primary != fallback


def test_a_slow_call_counts_as_a_failure_and_a_success_resets_the_count(world):
    now = datetime.now(UTC)

    async def report(succeeded, latency, i, status=None):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return await report_provider_outcome(
                session, "gemini", succeeded=succeeded, latency_seconds=latency, status_code=status,
                now=now + timedelta(seconds=i),
            )  # fmt: skip

    for i in range(4):
        assert run(report(True, 16.0, i)) == "CLOSED"  # "succeeded" but over the 15 s timeout: a failure
    assert run(report(True, 0.3, 4)) == "CLOSED"  # a good call resets the streak
    for i in range(5, 9):
        assert run(report(False, 0.3, i, 503)) == "CLOSED"
    assert run(report(False, 0.3, 9, 500)) == "OPEN"


def test_cooldown_then_half_open_probe_then_closed_again(world):
    now = datetime.now(UTC)

    async def call(fn, *args, **kw):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return await fn(session, *args, **kw)

    for i in range(5):
        run(
            call(
                report_provider_outcome,
                "gemini",
                succeeded=False,
                latency_seconds=0.1,
                status_code=500,
                now=now + timedelta(seconds=i),
            )
        )
    opened_at = now + timedelta(seconds=4)
    assert run(call(allow_provider, "gemini", now=opened_at + timedelta(seconds=10))) is False  # cooling down
    assert run(call(allow_provider, "gemini", now=opened_at + timedelta(seconds=31))) is True  # one probe
    assert run(_scalar("SELECT circuit_state FROM ai_providers WHERE code = 'gemini'")) == "HALF_OPEN"
    for i in range(2):  # two good probes close it
        state = run(
            call(
                report_provider_outcome,
                "gemini",
                succeeded=True,
                latency_seconds=0.2,
                now=opened_at + timedelta(seconds=32 + i),
            )
        )
    assert state == "CLOSED"
    assert run(_scalar("SELECT circuit_state FROM ai_providers WHERE code = 'gemini'")) == "CLOSED"


def test_a_failed_probe_reopens_the_breaker(world):
    now = datetime.now(UTC)

    async def call(fn, *args, **kw):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return await fn(session, *args, **kw)

    for i in range(5):
        run(
            call(
                report_provider_outcome,
                "gemini",
                succeeded=False,
                latency_seconds=0.1,
                status_code=503,
                now=now + timedelta(seconds=i),
            )
        )
    later = now + timedelta(seconds=60)
    assert run(call(allow_provider, "gemini", now=later)) is True
    assert (
        run(call(report_provider_outcome, "gemini", succeeded=False, latency_seconds=0.1, status_code=503, now=later))
        == "OPEN"
    )
    assert run(call(allow_provider, "gemini", now=later + timedelta(seconds=5))) is False


def test_unknown_provider(world):
    from app.modules.ai_gateway.public import ProviderNotFoundError

    async def attempt():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await allow_provider(session, "nope")

    with pytest.raises(ProviderNotFoundError):
        run(attempt())


# --- provider keys -----------------------------------------------------------------------------------


def test_a_429_rotates_to_the_next_key_in_under_50_ms():
    rings = GatewayKeyRings()
    rings.register(
        "gemini", [ApiKeyInput("k1", "secret-1"), ApiKeyInput("k2", "secret-2"), ApiKeyInput("k3", "secret-3")]
    )
    first = rings.acquire("gemini")
    assert first.acquired and first.key_id == "k1"
    started = time.perf_counter()
    second = rings.on_rate_limited("gemini", first.key_id)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert second.acquired and second.key_id != "k1" and elapsed_ms < 50
    printed = repr(second) + str(rings.acquire("gemini"))
    assert not any(f"secret-{n}" in printed for n in (1, 2, 3))  # a key's value is never printed


def test_an_unauthorized_key_is_dropped_for_good_and_unknown_providers_fail():
    from app.modules.ai_gateway.public import ProviderNotFoundError

    rings = GatewayKeyRings()
    ring = rings.register("gemini", [ApiKeyInput("k1", "s1"), ApiKeyInput("k2", "s2")])
    rings.on_unauthorized("gemini", "k1")
    assert {rings.acquire("gemini").key_id for _ in range(4)} == {"k2"} and ring.healthy_count() == 1
    with pytest.raises(ProviderNotFoundError):
        rings.acquire("nope")
