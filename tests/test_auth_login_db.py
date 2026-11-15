"""POST /api/v1/auth/login, GET /api/v1/auth/me and GET /api/v1/time against the migrated schema."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.main import app
from app.modules.privacy.internal.crypto_shredding import blind_index_pepper
from app.modules.privacy.public import InMemoryKeyEncryptionService, create_tenant, enroll_member
from app.platform.tenancy import DbRole, tenant_transaction
from app.security import hash_password

PEPPER = blind_index_pepper(get_settings().secret_key, get_settings().blind_index_pepper)
PASSWORD = "Correct-Horse-9"


def run(coro):
    return asyncio.run(coro)


async def _tenant(slug: str):
    async with tenant_transaction(uuid.UUID(int=0), role=DbRole.APP_RW) as session:
        return await create_tenant(session, slug=slug, name=slug)


async def _enroll(tenant_id: uuid.UUID, email: str, *, roles, password: str | None = PASSWORD, tier="FREE"):
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        result = await enroll_member(
            session,
            tenant_id=tenant_id,
            email=email,
            pepper=PEPPER,
            keys=InMemoryKeyEncryptionService(),
            today=date(2026, 11, 15),
            roles=roles,
            entitlement_tier=tier,
        )
        user_id = result.membership.user_id
        if password is not None:
            await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(user_id)})
            await session.execute(
                text("UPDATE users SET password_hash = :h WHERE id = :u"), {"h": hash_password(password), "u": user_id}
            )
        return result.membership


@pytest.fixture
def world():
    suffix = uuid.uuid4().hex[:8]
    tenant_a = run(_tenant(f"login-a-{suffix}"))
    tenant_b = run(_tenant(f"login-b-{suffix}"))
    email = f"teacher-{suffix}@example.test"
    member_a = run(_enroll(tenant_a.id, email, roles=["TEACHER"], tier="TEACHER_PRO"))
    member_b = run(_enroll(tenant_b.id, email, roles=["LEARNER"], password=None))
    return {"a": tenant_a, "b": tenant_b, "email": email, "member_a": member_a, "member_b": member_b}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_login_returns_a_token_for_the_membership_and_me_reads_it_back(client, world):
    response = client.post("/api/v1/auth/login", json={"email": world["email"], "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["tenant_id"] == str(world["a"].id) and body["membership_id"] == str(world["member_a"].id)
    assert body["roles"] == ["TEACHER"] and body["entitlement_tier"] == "TEACHER_PRO"
    assert response.headers["cache-control"] == "no-store"

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["tenant_slug"] == world["a"].slug and me.json()["membership_status"] == "ACTIVE"
    assert me.json()["roles"] == ["TEACHER"]


def test_login_is_case_and_space_insensitive_for_the_email(client, world):
    response = client.post(
        "/api/v1/auth/login", json={"email": f"  {world['email'].upper()} ", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "unknown_tenant"])
def test_every_failure_is_the_same_401(client, world, case):
    payload = {"email": world["email"], "password": PASSWORD}
    if case == "wrong_password":
        payload["password"] = "nope-nope-nope"
    elif case == "unknown_email":
        payload["email"] = "nobody@example.test"
    else:
        payload["tenant_slug"] = "no-such-tenant"
    response = client.post("/api/v1/auth/login", json=payload)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "invalid e-mail, password or tenant"


def test_tenant_slug_picks_the_membership_of_a_person_who_belongs_to_two_tenants(client, world):
    response = client.post(
        "/api/v1/auth/login", json={"email": world["email"], "password": PASSWORD, "tenant_slug": world["b"].slug}
    )
    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == str(world["b"].id) and response.json()["roles"] == ["LEARNER"]


def test_a_user_without_a_password_cannot_sign_in(client):
    suffix = uuid.uuid4().hex[:8]
    tenant = run(_tenant(f"login-nopw-{suffix}"))
    email = f"nopw-{suffix}@example.test"
    run(_enroll(tenant.id, email, roles=["LEARNER"], password=None))
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "anything-at-all"}).status_code == 401


def test_a_suspended_membership_cannot_sign_in(client, world):
    async def suspend() -> None:
        async with tenant_transaction(world["a"].id, role=DbRole.APP_RW) as session:
            await session.execute(
                text("UPDATE memberships SET status = 'SUSPENDED' WHERE id = :m"), {"m": world["member_a"].id}
            )

    run(suspend())
    response = client.post(
        "/api/v1/auth/login", json={"email": world["email"], "password": PASSWORD, "tenant_slug": world["a"].slug}
    )
    assert response.status_code == 401


def test_the_login_is_not_blocked_by_the_proxy_origin_but_a_cookie_request_is(client, world):
    proxy = {"Origin": "http://localhost:3000"}
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": world["email"], "password": PASSWORD}, headers=proxy
        ).status_code
        == 200
    )
    client.cookies.set("exam_session", "x")
    try:
        blocked = client.post(
            "/api/v1/auth/login", json={"email": world["email"], "password": PASSWORD}, headers=proxy
        )
    finally:
        client.cookies.clear()
    assert blocked.status_code == 403


def test_me_needs_a_valid_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_time_is_public_and_close_to_the_clock(client):
    before = int(time.time() * 1000)
    body = client.get("/api/v1/time").json()
    assert before - 1000 <= body["server_epoch_ms"] <= int(time.time() * 1000) + 1000
    assert body["server_iso"].endswith("+00:00")
