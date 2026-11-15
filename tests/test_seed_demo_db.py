"""app.ops.seed_demo: complete, idempotent, and the accounts it creates can sign in."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.privacy.internal.blind_index import compute_email_blind_index
from app.modules.privacy.internal.crypto_shredding import blind_index_pepper
from app.ops.seed_demo import ACCOUNTS, DEFAULT_PASSWORD, bayes_questions, email_blind_index, main, seed_demo


def run(coro):
    return asyncio.run(coro)


async def _count(sql: str) -> int:
    async with SessionLocal() as session:
        return (await session.execute(text(sql))).scalar_one()


def test_the_blind_index_matches_the_privacy_module():
    settings = get_settings()
    for pepper in ("", "x" * 40):
        expected = compute_email_blind_index(" Teacher@H2EDU.demo ", blind_index_pepper(settings.secret_key, pepper))
        assert email_blind_index("teacher@h2edu.demo", settings.secret_key, pepper) == expected


def test_the_question_set_has_the_thptqg_shape():
    questions = bayes_questions()
    kinds = [q["type"] for q in questions]
    assert (kinds.count("SINGLE_CHOICE"), kinds.count("CLUSTER_TRUE_FALSE"), kinds.count("SHORT_NUMERIC")) == (12, 4, 6)
    for q in questions:
        if q["type"] == "SINGLE_CHOICE":
            assert len(set(q["options"].values())) == 4, "the four options differ"
            assert q["answer"]["correct_option"] in q["options"]


def test_seeding_builds_everything_once_and_every_account_signs_in():
    first = run(seed_demo())
    created = first["created"]
    assert created["questions"] == 22 and created["class"] is True and created["paper"] is True
    assert created["lesson"] is True and created["subscription_plans"] == 3 and created["vertical_packs"] == 1
    assert created["locale_bundles"] == 2 and created["ai_models"] == 3
    assert set(first["accounts"]) == {a[0] for a in ACCOUNTS}

    second = run(seed_demo())
    assert all(not v for v in second["created"].values()), second["created"]
    assert run(_count("SELECT count(*) FROM tenants WHERE slug = 'h2edu-demo'")) == 1
    assert run(_count("SELECT count(*) FROM assessments WHERE title LIKE '%demo%'")) == 1
    assert run(_count("SELECT count(*) FROM questions WHERE stable_code LIKE 'DEMO-BAYES-%'")) == 22
    assert run(_count("SELECT count(*) FROM memberships")) == len(ACCOUNTS)
    assert run(_count("SELECT count(*) FROM assessment_items")) == 22
    assert run(_count("SELECT count(*) FROM assessment_versions WHERE is_immutable")) == 1
    assert run(_count("SELECT count(*) FROM content_versions WHERE status = 'PUBLISHED'")) == 1

    client = TestClient(app, base_url="http://127.0.0.1:8000")
    tokens = {}
    for key, email, _pseudonym, roles, _tier in ACCOUNTS:
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD, "tenant_slug": "h2edu-demo"}
        )
        assert response.status_code == 200, (key, response.text)
        assert response.json()["roles"] == roles
        tokens[key] = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['teacher']}"})
    assert me.status_code == 200 and me.json()["tenant_slug"] == "h2edu-demo"

    assessment_id = run(_scalar_text("SELECT id::text FROM assessments WHERE title LIKE '%demo%'"))
    started = client.post(
        "/api/v1/exam-sessions/start",
        headers={"Authorization": f"Bearer {tokens['learner']}"},
        json={"assessment_id": assessment_id},
    )
    assert started.status_code == 201, started.text


async def _scalar_text(sql: str) -> str:
    async with SessionLocal() as session:
        return (await session.execute(text(sql))).scalar_one()


def test_it_refuses_to_run_outside_dev(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    assert main([]) == 2
