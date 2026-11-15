"""C06 attempts (TASK-21): Fast Autosave H-04 on real PostgreSQL rows, under concurrency.

DoD: the six H-04 outcomes on PostgreSQL (IDEMPOTENT_REPLAY, 422 PAYLOAD_HASH_MISMATCH, 409
ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED, 403 ATTEMPT_DEADLINE_EXPIRED, 409 OPTIMISTIC_VERSION_CONFLICT, 409
STALE_CLIENT_SEQUENCE); concurrent saves and a racing submit never lose, duplicate or post-date an answer;
response_revisions is partitioned, append-only and closed partition by partition (F-01).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.contracts.autosave import AutosaveRequestContract, compute_payload_hash
from app.modules.attempts.public import save_response
from app.modules.delivery.public import submit_attempt
from app.platform.tenancy import DbRole, tenant_transaction
from tests.test_assembly_delivery_db import (  # the TASK-20 world: tenants, students, a published paper
    _all,
    _execute,
    _published_paper,
    _scalar,
    _seed,
    _sqlstate,
    _start,
    _student,
    client,  # noqa: F401  (fixture)
    run,
    world,  # noqa: F401  (fixture)
)


@pytest.fixture
def sitting(client, world):  # noqa: F811
    """A published THPTQG Math paper and an attempt of student a1 in progress."""
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()
    order = run(_scalar("SELECT question_order_ids FROM attempts WHERE id = :a", a=attempt["attempt_id"]))
    return {"attempt_id": attempt["attempt_id"], "questions": [str(q) for q in order], "paper": paper}


def _body(payload: dict, *, seq: int, expected: int, key: str | None = None, flagged: bool = False, bad_hash=None):
    return {
        "client_sequence": seq,
        "expected_response_version": expected,
        "idempotency_key": key or f"key-{uuid.uuid4().hex}",
        "payload_hash": bad_hash or compute_payload_hash(payload),
        "response_payload": payload,
        "is_flagged": flagged,
    }


def _put(client, world, sitting, body, question=0, key="a1", header=True):  # noqa: F811
    headers = _student(world, key)
    if header:
        headers = {**headers, "Idempotency-Key": body["idempotency_key"]}
    return client.put(
        f"/api/v1/attempts/{sitting['attempt_id']}/responses/{sitting['questions'][question]}",
        headers=headers,
        json=body,
    )


def _revisions(attempt_id: str) -> list:
    return run(
        _all(
            "SELECT rr.revision_no, rr.client_sequence, rr.idempotency_key, rr.recorded_at, "
            "rr.tableoid::regclass::text AS partition "
            "FROM response_revisions rr JOIN responses r ON r.id = rr.response_id "
            "WHERE r.attempt_id = :a ORDER BY rr.revision_no",
            a=attempt_id,
        )
    )


# --- schema -----------------------------------------------------------------------------------------


def test_response_revisions_is_partitioned_by_month_with_a_default_and_every_partition_is_closed():
    kind = run(_scalar("SELECT relkind::text FROM pg_class WHERE relname = 'response_revisions'"))
    assert kind == "p"
    partitions = {
        r.name
        for r in run(
            _all(
                "SELECT c.relname AS name FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'response_revisions'"
            )
        )
    }
    this_month = f"response_revisions_{datetime.now(UTC):%Y_%m}"
    assert {"response_revisions_default", this_month} <= partitions and len(partitions) >= 7
    for name in partitions:
        forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :n", n=name))
        assert forced is True, name
        for role in ("ulp_app_rw", "ulp_exam_session_rw", "ulp_worker_grading"):
            assert run(_scalar("SELECT has_table_privilege(:r, :n, 'SELECT')", r=role, n=name)) is False, (role, name)
    for table in ("responses", "autosave_idempotency_registry", "response_revisions"):
        forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :n", n=table))
        assert forced is True, table


def test_grants_follow_section_5():
    def has(role, obj, priv):
        return run(_scalar("SELECT has_table_privilege(:r, :o, :p)", r=role, o=obj, p=priv))

    assert has("ulp_exam_session_rw", "responses", "UPDATE") and has(
        "ulp_exam_session_rw", "response_revisions", "INSERT"
    )
    assert has("ulp_exam_session_rw", "autosave_idempotency_registry", "INSERT")
    assert not has("ulp_exam_session_rw", "autosave_idempotency_registry", "UPDATE")
    assert has("ulp_worker_grading", "responses", "SELECT") and not has("ulp_worker_grading", "responses", "UPDATE")
    assert has("ulp_app_rw", "responses", "SELECT") and not has("ulp_app_rw", "responses", "UPDATE")
    for role in ("ulp_app_rw", "ulp_exam_session_rw", "ulp_worker_grading", "ulp_system_scheduler", "ulp_analytics_ro"):
        for priv in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not has(role, "response_revisions", priv), (role, priv)
    assert not has("public", "responses", "SELECT")


# --- the six H-04 outcomes ------------------------------------------------------------------------------


def test_a_first_save_commits_version_1_and_writes_response_registry_and_revision(client, world, sitting):  # noqa: F811
    payload = {"selected_key": "C"}
    body = _body(payload, seq=1, expected=0, flagged=True)
    response = _put(client, world, sitting, body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["status"], data["committed_sequence"], data["saved_response_version"]) == ("COMMITTED", 1, 1)
    assert data["question_version_id"] == sitting["questions"][0] and data["attempt_id"] == sitting["attempt_id"]
    row = run(_all("SELECT * FROM responses"))[0]
    assert (row.current_revision_no, row.current_sequence, row.last_idempotency_key) == (1, 1, body["idempotency_key"])
    assert row.response_payload == payload and row.is_flagged is True
    registry = run(_all("SELECT * FROM autosave_idempotency_registry"))[0]
    assert (registry.response_id, registry.revision_no, registry.payload_sha256) == (row.id, 1, body["payload_hash"])
    (revision,) = _revisions(sitting["attempt_id"])
    assert revision.partition == f"response_revisions_{datetime.now(UTC):%Y_%m}", "lands in this month's partition"


def test_the_same_key_and_payload_is_an_idempotent_replay_that_writes_nothing(client, world, sitting):  # noqa: F811
    body = _body({"selected_key": "A"}, seq=1, expected=0)
    first = _put(client, world, sitting, body).json()
    replay = _put(client, world, sitting, body)
    assert replay.status_code == 200
    again = replay.json()
    assert again["status"] == "IDEMPOTENT_REPLAY"
    assert (again["saved_response_version"], again["committed_sequence"]) == (1, 1)
    assert again["server_timestamp"] == first["server_timestamp"], "the replay reports the original commit"
    assert len(_revisions(sitting["attempt_id"])) == 1


@pytest.mark.parametrize("case", ["hash_of_another_payload", "same_key_new_payload", "same_key_other_question"])
def test_payload_hash_mismatch_is_422_and_writes_nothing(client, world, sitting, case):  # noqa: F811
    committed = _body({"selected_key": "A"}, seq=1, expected=0)
    assert _put(client, world, sitting, committed).status_code == 200
    if case == "hash_of_another_payload":
        body, question = _body({"selected_key": "B"}, seq=2, expected=1, bad_hash=compute_payload_hash({"x": 1})), 0
    elif case == "same_key_new_payload":
        body, question = _body({"selected_key": "D"}, seq=2, expected=1, key=committed["idempotency_key"]), 0
    else:
        body, question = _body({"selected_key": "A"}, seq=1, expected=0, key=committed["idempotency_key"]), 1
    response = _put(client, world, sitting, body, question=question)
    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "PAYLOAD_HASH_MISMATCH"
    assert run(_scalar("SELECT count(*) FROM responses")) == 1
    assert run(_scalar("SELECT count(*) FROM autosave_idempotency_registry")) == 1
    assert run(_scalar("SELECT response_payload FROM responses")) == {"selected_key": "A"}


def test_a_stale_expected_version_is_an_optimistic_conflict_with_the_current_version(client, world, sitting):  # noqa: F811
    assert _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0)).status_code == 200
    other_device = _put(client, world, sitting, _body({"selected_key": "B"}, seq=2, expected=0))
    assert other_device.status_code == 409
    problem = other_device.json()
    assert problem["error_code"] == "OPTIMISTIC_VERSION_CONFLICT"
    assert (problem["current_response_version"], problem["current_sequence"]) == (1, 1)
    assert run(_scalar("SELECT response_payload FROM responses")) == {"selected_key": "A"}


def test_a_sequence_that_does_not_move_forward_is_stale(client, world, sitting):  # noqa: F811
    assert _put(client, world, sitting, _body({"selected_key": "A"}, seq=5, expected=0)).status_code == 200
    stale = _put(client, world, sitting, _body({"selected_key": "B"}, seq=5, expected=1))
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "STALE_CLIENT_SEQUENCE" and stale.json()["current_sequence"] == 5
    newer = _put(client, world, sitting, _body({"selected_key": "B"}, seq=6, expected=1))
    assert newer.status_code == 200 and newer.json()["saved_response_version"] == 2
    assert [r.revision_no for r in _revisions(sitting["attempt_id"])] == [1, 2]


def test_no_save_after_submit(client, world, sitting):  # noqa: F811
    done = client.post(f"/api/v1/attempts/{sitting['attempt_id']}/submit", headers=_student(world))
    assert done.status_code == 200
    late = _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0))
    assert late.status_code == 409
    assert late.json()["error_code"] == "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED"
    assert late.json()["attempt_status"] == "SUBMITTED"
    assert run(_scalar("SELECT count(*) FROM responses")) == 0


def test_the_server_deadline_allows_a_network_grace_and_nothing_more(client, world, sitting):  # noqa: F811
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '2 seconds'"))
    within_grace = _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0))
    assert within_grace.status_code == 200, "a save sent just before the deadline still lands"
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '1 minute'"))
    expired = _put(client, world, sitting, _body({"selected_key": "B"}, seq=2, expected=1))
    assert expired.status_code == 403 and expired.json()["error_code"] == "ATTEMPT_DEADLINE_EXPIRED"
    assert "deadline_at" in expired.json()
    assert run(_scalar("SELECT response_payload FROM responses")) == {"selected_key": "A"}


def test_a_replay_is_answered_even_after_the_deadline_and_the_submit(client, world, sitting):  # noqa: F811
    body = _body({"selected_key": "A"}, seq=1, expected=0)
    assert _put(client, world, sitting, body).status_code == 200
    client.post(f"/api/v1/attempts/{sitting['attempt_id']}/submit", headers=_student(world))
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '1 hour'"))
    replay = _put(client, world, sitting, body)
    assert replay.status_code == 200 and replay.json()["status"] == "IDEMPOTENT_REPLAY", "the registry is checked first"


# --- request checks and ownership ----------------------------------------------------------------------


def test_expected_response_version_is_required(client, world, sitting):  # noqa: F811
    body = _body({"selected_key": "A"}, seq=1, expected=0)
    del body["expected_response_version"]
    assert _put(client, world, sitting, body).status_code == 422
    with pytest.raises(ValueError):
        AutosaveRequestContract.model_validate(body)


def test_who_may_save_where(client, world, sitting):  # noqa: F811
    body = _body({"selected_key": "A"}, seq=1, expected=0)
    assert _put(client, world, sitting, body, key="a2").status_code == 404, "another student's attempt"
    assert _put(client, world, sitting, body, key="b1").status_code == 404, "another tenant's attempt"
    off_paper = client.put(
        f"/api/v1/attempts/{sitting['attempt_id']}/responses/{uuid.uuid4()}", headers=_student(world), json=body
    )
    assert off_paper.status_code == 404
    unauthenticated = client.put(
        f"/api/v1/attempts/{sitting['attempt_id']}/responses/{sitting['questions'][0]}", json=body
    )
    assert unauthenticated.status_code == 401
    mismatch = client.put(
        f"/api/v1/attempts/{sitting['attempt_id']}/responses/{sitting['questions'][0]}",
        headers={**_student(world), "Idempotency-Key": "another-key-123"},
        json=body,
    )
    assert mismatch.status_code == 422
    assert run(_scalar("SELECT count(*) FROM responses")) == 0
    assert _put(client, world, sitting, body, header=False).status_code == 200, "the header is optional"


def test_sync_returns_the_server_version_of_every_answer(client, world, sitting):  # noqa: F811
    first = _body({"selected_key": "B"}, seq=3, expected=0)
    _put(client, world, sitting, first, question=0)
    _put(client, world, sitting, _body({"text_value": "2,5"}, seq=1, expected=0), question=5)
    _put(client, world, sitting, _body({"selected_key": "C"}, seq=4, expected=1), question=0)
    sync = client.get(f"/api/v1/attempts/{sitting['attempt_id']}/sync", headers=_student(world))
    assert sync.status_code == 200, sync.text
    data = sync.json()
    assert data["status"] == "IN_PROGRESS" and data["attempt_id"] == sitting["attempt_id"]
    assert set(data["responses"]) == {sitting["questions"][0], sitting["questions"][5]}
    q0 = data["responses"][sitting["questions"][0]]
    assert (q0["revision_no"], q0["current_sequence"], q0["response_payload"]) == (2, 4, {"selected_key": "C"})
    assert q0["last_idempotency_key"] is not None and q0["last_idempotency_key"] != first["idempotency_key"]
    assert (
        client.get(f"/api/v1/attempts/{sitting['attempt_id']}/sync", headers=_student(world, "a2")).status_code == 404
    )
    assert client.get(f"/api/v1/attempts/{sitting['attempt_id']}/sync").status_code == 401


# --- concurrency (chaos) ----------------------------------------------------------------------------------


async def _save(world, sitting, body: dict, question: int = 0):  # noqa: F811
    async with tenant_transaction(world["a"], role=DbRole.EXAM_SESSION_RW) as session:
        result = await save_response(
            session,
            uuid.UUID(sitting["attempt_id"]),
            uuid.UUID(sitting["questions"][question]),
            membership_id=world["member_a1"],
            request=AutosaveRequestContract.model_validate(body),
        )
        return result.code


def test_the_same_save_sent_many_times_at_once_commits_exactly_once(world, sitting):  # noqa: F811
    body = _body({"selected_key": "D"}, seq=1, expected=0)

    async def storm():
        return await asyncio.gather(*(_save(world, sitting, body) for _ in range(8)))

    codes = run(storm())
    assert sorted(codes) == ["COMMITTED"] + ["IDEMPOTENT_REPLAY"] * 7
    assert len(_revisions(sitting["attempt_id"])) == 1
    assert run(_scalar("SELECT count(*) FROM autosave_idempotency_registry")) == 1


def test_two_devices_saving_the_same_version_at_once_let_exactly_one_win(world, sitting):  # noqa: F811
    async def race():
        return await asyncio.gather(
            *(_save(world, sitting, _body({"selected_key": k}, seq=1, expected=0)) for k in "ABCDABCD")
        )

    codes = run(race())
    assert codes.count("COMMITTED") == 1
    assert set(codes) - {"COMMITTED"} <= {"OPTIMISTIC_VERSION_CONFLICT"}
    assert run(_scalar("SELECT current_revision_no FROM responses")) == 1
    assert len(_revisions(sitting["attempt_id"])) == 1


def test_saves_of_different_questions_run_side_by_side(world, sitting):  # noqa: F811
    async def parallel():
        return await asyncio.gather(
            *(_save(world, sitting, _body({"selected_key": "A"}, seq=1, expected=0), question=i) for i in range(10))
        )

    assert run(parallel()) == ["COMMITTED"] * 10
    assert run(_scalar("SELECT count(*) FROM responses")) == 10


def test_a_submit_racing_with_saves_never_lets_a_save_land_after_it(world, sitting):  # noqa: F811
    async def submit():
        async with tenant_transaction(world["a"], role=DbRole.EXAM_SESSION_RW) as session:
            await submit_attempt(session, uuid.UUID(sitting["attempt_id"]), membership_id=world["member_a1"])
            return "SUBMIT"

    async def chaos():
        saves = [_save(world, sitting, _body({"selected_key": "A"}, seq=1, expected=0), question=i) for i in range(12)]
        return await asyncio.gather(*saves[:6], submit(), *saves[6:])

    codes = run(chaos())
    assert codes.count("SUBMIT") == 1
    assert set(codes) - {"SUBMIT"} <= {"COMMITTED", "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED"}
    assert len(_revisions(sitting["attempt_id"])) == codes.count("COMMITTED"), "every committed save, no other"
    assert run(_scalar("SELECT status FROM attempts WHERE id = :a", a=sitting["attempt_id"])) == "SUBMITTED"
    # whatever the interleaving, once the submit is decided no save gets through
    after = run(_save(world, sitting, _body({"selected_key": "B"}, seq=9, expected=0), question=12))
    assert after == "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED"


# --- append-only, partitions, maintenance -------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql", ["UPDATE response_revisions SET client_sequence = 99", "DELETE FROM response_revisions"]
)
def test_revisions_are_append_only_even_for_the_owner(client, world, sitting, sql):  # noqa: F811
    _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0))
    with pytest.raises(DBAPIError, match="APPEND_ONLY_VIOLATION"):
        run(_execute(sql))


def test_a_partition_cannot_be_read_around_the_parents_rls(client, world, sitting):  # noqa: F811
    _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0))
    partition = f"response_revisions_{datetime.now(UTC):%Y_%m}"
    for role in (DbRole.EXAM_SESSION_RW, DbRole.APP_RW):
        with pytest.raises(DBAPIError) as caught:
            run(_as_role_any(world["b"], role, f"SELECT * FROM {partition}"))
        assert _sqlstate(caught.value) == "42501"
    seen_by_b = run(_as_role_any(world["b"], DbRole.EXAM_SESSION_RW, "SELECT count(*) FROM response_revisions"))
    assert seen_by_b[0][0] == 0
    seen_by_a = run(_as_role_any(world["a"], DbRole.EXAM_SESSION_RW, "SELECT count(*) FROM response_revisions"))
    assert seen_by_a[0][0] == 1


async def _as_role_any(tenant, role, sql):
    async with tenant_transaction(tenant, role=role) as session:
        return (await session.execute(text(sql))).all()


def test_the_daily_maintenance_purges_old_registry_keys_and_keeps_partitions_ahead(client, world, sitting):  # noqa: F811
    _put(client, world, sitting, _body({"selected_key": "A"}, seq=1, expected=0))
    response_id = run(_scalar("SELECT id FROM responses"))
    run(
        _execute(
            "INSERT INTO autosave_idempotency_registry (tenant_id, attempt_id, idempotency_key, response_id, "
            "revision_no, client_sequence, payload_sha256, recorded_at) "
            "VALUES (:t, :a, 'old-key-000', :r, 1, 1, :h, now() - interval '31 days')",
            t=world["a"],
            a=sitting["attempt_id"],
            r=response_id,
            h="0" * 64,
        )
    )
    run(_execute("SELECT run_daily_partition_and_registry_maintenance()"))
    keys = {r.idempotency_key for r in run(_all("SELECT idempotency_key FROM autosave_idempotency_registry"))}
    assert "old-key-000" not in keys and len(keys) == 1
    six_months_out = (datetime.now(UTC).replace(day=1) + timedelta(days=5 * 31)).strftime("%Y_%m")
    assert run(_scalar("SELECT to_regclass(:n) IS NOT NULL", n=f"response_revisions_{six_months_out}"))
