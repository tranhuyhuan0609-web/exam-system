"""`seed_attempts.py --materialize`: the VU sessions of a GATE-05 run exist as real rows, and the k6 chain runs on them.

The synthetic generator makes ids no database knows. Materializing creates the load-test tenants (own slug prefix),
their teacher, learners, class, published paper, assignment and one IN_PROGRESS attempt per VU, and returns records
whose tokens, attempts and question versions are real: PUT / replay / submit as `k6_exam_5000ccu.js` sends them are
accepted by the API. A second run adds nothing, and no other tenant is touched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.platform.auth_context import decode_and_verify_actor_token
from tests.load.materialize import materialize_sessions
from tests.load.seed_attempts import main

SECRET = "load-materialize-secret-0123456789ab"
PREFIX = "ulp-loadtest"


async def _count(sql: str, **params) -> int:
    async with SessionLocal() as session:
        return int((await session.execute(text(sql), params)).scalar_one())


def _n(sql: str, **params) -> int:
    return asyncio.run(_count(sql, **params))


def _materialize(**kwargs):
    return asyncio.run(
        materialize_sessions(
            count=kwargs.pop("count", 12),
            tenant_count=kwargs.pop("tenant_count", 2),
            seed=kwargs.pop("seed", 777),
            signing_secret=SECRET,
            tenant_prefix=kwargs.pop("tenant_prefix", PREFIX),
            **kwargs,
        )
    )


@pytest.fixture
def bystander() -> uuid.UUID:
    """A tenant outside the load prefix, with a row of its own, that materializing must leave alone."""

    async def make() -> uuid.UUID:
        async with SessionLocal() as session, session.begin():
            await session.execute(text("INSERT INTO tenants (slug, name) VALUES ('bystander', 'Bystander')"))
            return (await session.execute(text("SELECT id FROM tenants WHERE slug = 'bystander'"))).scalar_one()

    return asyncio.run(make())


def test_materialize_makes_real_tenants_people_papers_and_attempts(client, monkeypatch, bystander):
    monkeypatch.setattr(get_settings(), "secret_key", SECRET)
    now = int(time.time())
    records = _materialize(issued_at=now)

    assert [r["vu_index"] for r in records] == list(range(1, 13))
    assert len({r["attempt_id"] for r in records}) == 12
    tenants = {r["tenant_id"] for r in records}
    assert len(tenants) == 2
    assert _n("SELECT count(*) FROM tenants WHERE slug LIKE :p", p=f"{PREFIX}-%") == 2

    # what the k6 script reads, and what the API verifies
    for record in records:
        assert len(record["question_version_ids"]) == 22
        actor = decode_and_verify_actor_token(record["jwt"], secret=SECRET, now_epoch=now)
        assert str(actor.tenant_id) == record["tenant_id"] and str(actor.membership_id) == record["membership_id"]
        assert str(actor.user_id) == record["user_id"] and actor.roles == {"LEARNER"}

    # real rows: an IN_PROGRESS attempt per VU, on the tenant's published paper and the class assignment
    for record in records:
        row = asyncio.run(
            _row(
                "SELECT a.status, a.membership_id, a.tenant_id, a.assignment_id, a.deadline_at > now() AS open, "
                "p.publication_status, m.status AS member_status "
                "FROM attempts a JOIN assessment_versions v ON v.id = a.assessment_version_id "
                "JOIN assessments p ON p.id = v.assessment_id JOIN memberships m ON m.id = a.membership_id "
                "WHERE a.id = :a",
                a=uuid.UUID(record["attempt_id"]),
            )
        )
        assert row["status"] == "IN_PROGRESS" and row["open"] is True, record["vu_index"]
        assert str(row["membership_id"]) == record["membership_id"] and str(row["tenant_id"]) == record["tenant_id"]
        assert row["publication_status"] == "PUBLISHED" and row["member_status"] == "ACTIVE"
        assert str(row["assignment_id"]) == record["assignment_id"]
    for tenant in tenants:
        t = uuid.UUID(tenant)
        assert _n("SELECT count(*) FROM classes WHERE tenant_id = :t", t=t) == 1
        assert _n("SELECT count(*) FROM class_members WHERE tenant_id = :t", t=t) == 6
        assert _n("SELECT count(*) FROM assessment_assignments WHERE tenant_id = :t", t=t) == 1
        assert _n("SELECT count(*) FROM questions WHERE tenant_id = :t", t=t) == 22

    # nothing outside the load prefix was created or changed
    assert _n("SELECT count(*) FROM memberships WHERE tenant_id = :t", t=bystander) == 0
    assert _n("SELECT count(*) FROM attempts WHERE tenant_id = :t", t=bystander) == 0
    assert _n("SELECT count(*) FROM tenants") == 1 + 1 + 2, "system tenant, bystander, two load tenants"


def test_a_second_run_adds_nothing_and_returns_the_same_attempts(client):
    first = _materialize()
    tables = ("tenants", "users", "memberships", "attempts", "class_members", "assessments", "assessment_assignments")
    counts = {table: _n(f"SELECT count(*) FROM {table}") for table in tables}
    second = _materialize()
    assert [r["attempt_id"] for r in second] == [r["attempt_id"] for r in first]
    assert [r["membership_id"] for r in second] == [r["membership_id"] for r in first]
    assert [r["tenant_id"] for r in second] == [r["tenant_id"] for r in first]
    for table, before in counts.items():
        assert _n(f"SELECT count(*) FROM {table}") == before, f"{table} grew on the second run"


def test_the_k6_chain_runs_on_materialized_sessions(client, monkeypatch):
    """PUT, the 5% replay, and the end-of-exam submit burst, exactly as the script sends them, for several VUs."""
    monkeypatch.setattr(get_settings(), "secret_key", SECRET)
    records = _materialize(count=6, tenant_count=2, issued_at=int(time.time()))
    for record in records:
        attempt_id = record["attempt_id"]
        headers = {"Authorization": f"Bearer {record['jwt']}"}

        # the paper of the attempt is the one the record names
        paper = client.get(f"/api/v1/attempts/{attempt_id}/paper", headers=headers)
        assert paper.status_code == 200, paper.text
        listed = {q["question_version_id"] for q in paper.json()["questions"]}
        assert set(record["question_version_ids"]) == listed

        # runAutosaveLoop: a SINGLE_CHOICE has the selected_key payload; the first question may be of another type,
        # so the loop is exercised on the first single-choice question of the paper
        single = next(q["question_version_id"] for q in paper.json()["questions"] if q["item_type"] == "SINGLE_CHOICE")
        assert single in record["question_version_ids"]
        payload = {"selected_key": "B"}
        key = f"{attempt_id}:{single}:seq_1"
        body = {
            "client_sequence": 1,
            "expected_response_version": 0,
            "idempotency_key": key,
            "payload_hash": hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest(),
            "response_payload": payload,
            "is_flagged": False,
        }
        url = f"/api/v1/attempts/{attempt_id}/responses/{single}"
        first = client.put(url, headers={**headers, "Idempotency-Key": key}, json=body)
        assert first.status_code == 200 and first.json()["status"] == "COMMITTED", (record["vu_index"], first.text)
        replay = client.put(url, headers={**headers, "Idempotency-Key": key}, json=body)
        assert replay.status_code == 200 and replay.json()["status"] == "IDEMPOTENT_REPLAY", replay.text

        # runSubmitBurst
        submit_headers = {**headers, "Idempotency-Key": f"sub:{attempt_id}"}
        submitted = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=submit_headers)
        assert submitted.status_code == 200 and submitted.json()["replayed"] is False, submitted.text
        assert client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=submit_headers).json()["replayed"] is True


def test_the_cli_writes_the_real_sessions_and_refuses_a_bad_prefix(client, monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("LOAD_TEST_JWT_SECRET", SECRET)
    out = tmp_path / "sessions.json"
    assert (
        main(["--count", "6", "--tenants", "2", "--materialize", "--tenant-prefix", "cli-load", "--out", str(out)]) == 0
    )
    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(written) == 6
    printed = capsys.readouterr().out
    assert "cli-load-00" in printed and "real (materialized)" in printed
    for record in written:
        attempt = uuid.UUID(record["attempt_id"])
        assert _n("SELECT count(*) FROM attempts WHERE id = :a AND status = 'IN_PROGRESS'", a=attempt) == 1
    assert (
        main(["--count", "2", "--tenants", "1", "--materialize", "--tenant-prefix", "Bad_Prefix!", "--out", str(out)])
        == 2
    )
    assert "tenant-prefix" in capsys.readouterr().err


async def _row(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).mappings().one()
