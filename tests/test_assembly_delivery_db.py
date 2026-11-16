"""C04 assembly + C05 delivery (TASK-20): papers, assembly, publishing, attempts, hybrid RLS, answer lockout.

DoD: `structure_snapshot_json` and `GET /attempts/{id}/paper` hold no answer data at all; the same `random_seed` gives
the same paper; a reload or another device shows exactly the same question order and A/B/C/D permutation; the exam-room
role gets 42501 on every answer table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
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
from app.modules.assembly.public import (
    AssemblyShortageError,
    PoolQuestion,
    SectionRequirement,
    select_items,
)
from app.modules.delivery.public import derive_attempt_seed, start_exam_session
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, SystemJob, system_job_transaction, tenant_transaction

SYSTEM = SYSTEM_PUBLIC_TENANT_ID
NO_KEY_ROLES = (DbRole.EXAM_SESSION_RW, DbRole.WORKER_INGESTION, DbRole.SYSTEM_SCHEDULER, DbRole.ANALYTICS_RO)
MATH_COUNTS = {"SINGLE_CHOICE": 12, "CLUSTER_TRUE_FALSE": 4, "SHORT_NUMERIC": 6}
ANSWER_WORDS = ("answer", "correct", "is_correct", "rationale", "rubric", "explanation", "solution", "answer_key")


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


async def _as_role(tenant: uuid.UUID, role: DbRole, sql: str, **params):
    async with tenant_transaction(tenant, role=role) as session:
        return (await session.execute(text(sql), params)).all()


def _sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)


def _walk(value):
    """Every key and every string value of a JSON document."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, str):
        yield value


def _assert_no_answer_data(document) -> None:
    leaked = [w for w in _walk(document) if any(word in w.lower() for word in ANSWER_WORDS)]
    assert leaked == [], f"answer data in a public document: {leaked[:5]}"


# --- world -----------------------------------------------------------------------------------------------


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Two tenants with an author and two students each, a system author, and a MATH taxonomy under the system."""

    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"asm-a-{suffix}", name="A"), Tenant(slug=f"asm-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("author_a", "author_b", "author_system", "student_a1", "student_a2", "student_b1"):
                users[key] = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
            math = (
                await session.execute(
                    text(
                        "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path) "
                        "VALUES (:t, 'SUBJECT', 'MATH', 'MATH') RETURNING id"
                    ),
                    {"t": SYSTEM},
                )
            ).scalar_one()
            topic = (
                await session.execute(
                    text(
                        "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path, parent_id) "
                        "VALUES (:t, 'TOPIC', 'BAYES', 'MATH.BAYES', :p) RETURNING id"
                    ),
                    {"t": SYSTEM, "p": math},
                )
            ).scalar_one()
            physics = (
                await session.execute(
                    text(
                        "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path) "
                        "VALUES (:t, 'SUBJECT', 'PHYSICS', 'PHYSICS') RETURNING id"
                    ),
                    {"t": SYSTEM},
                )
            ).scalar_one()
        world = {"a": a.id, "b": b.id, "system": SYSTEM, "math": math, "topic": topic, "physics": physics, **users}
        for tenant, key, tier in (
            ("a", "student_a1", "FREE"),
            ("a", "student_a2", "FREE"),
            ("b", "student_b1", "FREE"),
        ):
            world[f"member_{key.removeprefix('student_')}"] = await _membership(world[tenant], world[key], tier)
        return world

    return run(create())


async def _membership(tenant: uuid.UUID, user: uuid.UUID, tier: str = "FREE", status: str = "ACTIVE") -> uuid.UUID:
    member = uuid.uuid4()
    async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, status, roles, entitlement_tier) "
                "VALUES (:m, :t, :u, :s, ARRAY['LEARNER'], :tier)"
            ),
            {"m": member, "t": tenant, "u": user, "s": status, "tier": tier},
        )
    return member


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(tenant, user, *, membership=None, roles=("TEACHER",), tier="TEACHER_PRO") -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user,
        tenant_id=tenant,
        membership_id=membership or uuid.uuid4(),
        roles=roles,
        entitlement_tier=tier,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}"}


def _author(world, who="a"):
    """An author of the bank (C01 writes need it; a teacher only builds papers)."""
    return _headers(world[who], world[f"author_{who}"], roles=("CONTENT_AUTHOR",))


def _student(world, key="a1"):
    tenant = world[key[0]]
    return _headers(tenant, world[f"student_{key}"], membership=world[f"member_{key}"], roles=("LEARNER",), tier="FREE")


# --- question pool ------------------------------------------------------------------------------------------


async def _seed_questions(
    tenant: uuid.UUID,
    taxonomy: uuid.UUID,
    counts: dict[str, int],
    *,
    visibility: str = "TENANT_PRIVATE",
    verified: bool = True,
    cluster_of: dict[int, uuid.UUID] | None = None,
    stimulus: uuid.UUID | None = None,
    stimulus_for: tuple[int, ...] = (),
) -> list[dict]:
    """Published questions with their options and answers, as the C01 module stores them."""
    made: list[dict] = []
    n = 0
    async with SessionLocal() as session, session.begin():
        for item_type, count in counts.items():
            for _ in range(count):
                n += 1
                qid, vid = uuid.uuid4(), uuid.uuid4()
                code = f"Q-{tenant.hex[:6]}-{uuid.uuid4().hex[:10]}"
                digest = hashlib.sha256(code.encode()).hexdigest()
                use_stimulus = stimulus if n in stimulus_for else None
                keys = {"SINGLE_CHOICE": "ABCD", "CLUSTER_TRUE_FALSE": "abcd"}.get(item_type, "")
                answer = {
                    "SINGLE_CHOICE": {"correct_option": "B"},
                    "CLUSTER_TRUE_FALSE": {"statements": {"a": True, "b": False, "c": True, "d": False}},
                    "SHORT_NUMERIC": {"value": "2,5"},
                }[item_type]
                await session.execute(
                    text(
                        "INSERT INTO questions (id, tenant_id, visibility_scope, stable_code, item_type, "
                        "primary_taxonomy_node_id, verification_status, rights_status, duplicate_cluster_id) "
                        "VALUES (:q, :t, :vis, :c, :it, :tx, :ver, 'OWNED', NULL)"
                    ),
                    {"q": qid, "t": tenant, "vis": visibility, "c": code, "it": item_type, "tx": taxonomy,
                     "ver": "VERIFIED" if verified else "UNCHECKED"},
                )  # fmt: skip
                if use_stimulus is not None:
                    await session.execute(
                        text(
                            "INSERT INTO question_stimuli (id, tenant_id, stimulus_type, body_json) "
                            "VALUES (:s, :t, 'READING_PASSAGE', '{\"text\": \"Đoạn dữ kiện chung\"}') "
                            "ON CONFLICT DO NOTHING"
                        ),
                        {"s": use_stimulus, "t": tenant},
                    )
                await session.execute(
                    text(
                        "INSERT INTO question_versions (id, tenant_id, visibility_scope, question_id, version_no, "
                        "stimulus_id, stem_json, options_json, content_hash, structural_hash) "
                        "VALUES (:v, :t, :vis, :q, 1, :st, CAST(:stem AS jsonb), CAST(:opts AS jsonb), :h, :h)"
                    ),
                    {
                        "v": vid, "t": tenant, "vis": visibility, "q": qid, "st": use_stimulus,
                        "stem": json.dumps({"text": f"Câu {n}: {item_type}"}),
                        "opts": json.dumps([{"key": k, "text": f"Phương án {k} của câu {n}"} for k in keys]),
                        "h": digest,
                    },
                )  # fmt: skip
                for order, k in enumerate(keys, start=1):
                    await session.execute(
                        text(
                            "INSERT INTO question_options (tenant_id, question_version_id, option_key, content_json, "
                            "sequence_order) VALUES (:t, :v, :k, CAST(:c AS jsonb), :o)"
                        ),
                        {"t": tenant, "v": vid, "k": k, "o": order,
                         "c": json.dumps({"text": f"Phương án {k} của câu {n}"})},
                    )  # fmt: skip
                await session.execute(
                    text(
                        "INSERT INTO question_version_answers (question_version_id, tenant_id, answer_schema) "
                        "VALUES (:v, :t, CAST(:a AS jsonb))"
                    ),
                    {"v": vid, "t": tenant, "a": json.dumps(answer)},
                )
                if verified:  # the database refuses PUBLISHED without VERIFIED and cleared rights
                    await session.execute(
                        text(
                            "UPDATE questions SET publication_status = 'PUBLISHED', current_version_id = :v "
                            "WHERE id = :q"
                        ),
                        {"v": vid, "q": qid},
                    )
                if cluster_of and n in cluster_of:
                    await session.execute(
                        text("UPDATE questions SET duplicate_cluster_id = :c WHERE id = :q"),
                        {"c": cluster_of[n], "q": qid},
                    )
                made.append({"question_id": qid, "version_id": vid, "item_type": item_type, "answer": answer})
    return made


def _seed(world, counts=None, tenant="a", **kwargs):
    return run(_seed_questions(world[tenant], world["topic"], counts or MATH_COUNTS, **kwargs))


def _create_assessment(client, world, who="a", **extra):
    body = {
        "title": "Đề thi thử Toán THPT 2025",
        "subject_code": "math",
        "grade_band": "GRADE_12",
        "exam_type_code": "THPTQG",
        "rights_status": "OWNED",
        **extra,
    }
    response = client.post("/api/v1/assessments", headers=_author(world, who), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _assemble(client, world, assessment_id, seed=7, who="a", **extra):
    return client.post(
        f"/api/v1/assessments/{assessment_id}/assemble",
        headers=_author(world, who),
        json={"random_seed": seed, **extra},
    )


def _publish(client, world, assessment_id, who="a"):
    return client.post(f"/api/v1/assessments/{assessment_id}/publish", headers=_author(world, who))


def _published_paper(client, world, who="a", seed=7, **create_extra):
    assessment = _create_assessment(client, world, who=who, **create_extra)
    assembled = _assemble(client, world, assessment["id"], seed=seed, who=who)
    assert assembled.status_code == 200, assembled.text
    published = _publish(client, world, assessment["id"], who=who)
    assert published.status_code == 200, published.text
    return published.json()


def _start(client, world, assessment_id, key="a1"):
    return client.post(
        "/api/v1/exam-sessions/start", headers=_student(world, key), json={"assessment_id": assessment_id}
    )


# --- schema, triggers, privileges -----------------------------------------------------------------------------


def test_tables_triggers_policies_and_indexes_exist():
    assert {
        "trg_check_assessment_item_question_ref",
        "trg_immutable_assessment_versions",
    } <= {r.tgname for r in run(_all("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
    policies = {
        (r.tablename, r.policyname)
        for r in run(
            _all(
                "SELECT tablename, policyname FROM pg_policies WHERE tablename IN "
                "('assessment_answer_keys', 'attempts', 'assessments', 'assessment_versions', 'assessment_items')"
            )
        )
    }
    assert ("assessment_answer_keys", "grading_read_answer_keys") in policies
    assert ("attempts", "sys_attempt_expiry_scan") in policies and ("attempts", "tenant_isolation_policy") in policies
    for table in ("assessments", "assessment_versions", "assessment_items"):
        assert (table, "hybrid_read_policy") in policies and (table, "hybrid_write_policy") in policies
    index_rows = run(
        _all("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'assessment%' OR tablename = 'attempts'")
    )
    indexes = {r.indexname for r in index_rows}
    assert {
        "uq_assessment_answer_keys_current",
        "ix_attempts_membership_status",
        "ix_attempts_auto_submit_scan",
    } <= indexes
    for table in ("assessments", "assessment_versions", "assessment_items", "assessment_answer_keys", "attempts"):
        forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
        assert forced is True, table


@pytest.mark.parametrize("role", NO_KEY_ROLES)
def test_the_answer_key_table_is_closed_to_the_exam_room_ingestion_scheduler_and_analytics(role):
    granted = run(
        _scalar(
            "SELECT has_table_privilege(:r, 'assessment_answer_keys', 'SELECT') "
            "OR has_table_privilege(:r, 'assessment_answer_keys', 'INSERT') "
            "OR has_table_privilege(:r, 'assessment_answer_keys', 'UPDATE')",
            r=role.value,
        )
    )
    assert granted is False
    with pytest.raises(DBAPIError) as caught:
        run(_as_role(uuid.uuid4(), role, "SELECT answer_key_payload_json FROM assessment_answer_keys"))
    assert _sqlstate(caught.value) == "42501"


def test_grants_follow_the_spec_for_the_api_exam_room_and_grading_roles():
    def has(role, obj, priv):
        return run(_scalar("SELECT has_table_privilege(:r, :o, :p)", r=role, o=obj, p=priv))

    assert has("ulp_worker_grading", "assessment_answer_keys", "SELECT")
    assert not has("ulp_worker_grading", "assessment_answer_keys", "UPDATE")
    assert has("ulp_exam_session_rw", "attempts", "INSERT")
    assert not has("ulp_exam_session_rw", "attempts", "UPDATE"), "UPDATE is column-level: the deadline stays fixed"
    assert not has("ulp_exam_session_rw", "assessment_versions", "UPDATE")
    assert not has("ulp_exam_session_rw", "assessments", "INSERT")
    assert has("ulp_app_rw", "assessment_answer_keys", "INSERT")
    assert not has("ulp_app_rw", "attempts", "INSERT")
    assert not has("public", "attempts", "SELECT")
    grading_update = run(
        _all(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = 'ulp_worker_grading' AND table_name = 'attempts' AND privilege_type = 'UPDATE'"
        )
    )
    assert {r.column_name for r in grading_update} == {"status", "submitted_at"}
    scheduler = run(
        _all(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE grantee = 'ulp_system_scheduler' AND table_name = 'attempts' AND privilege_type = 'SELECT'"
        )
    )
    assert {r.column_name for r in scheduler} == {"id", "tenant_id", "status", "deadline_at"}
    for grantee, columns in (
        ("ulp_exam_session_rw", {"status", "submitted_at", "submit_idempotency_key"}),
        ("ulp_system_scheduler", {"status", "submitted_at"}),
    ):
        granted = run(
            _all(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = :g AND table_name = 'attempts' AND privilege_type = 'UPDATE'",
                g=grantee,
            )
        )
        assert {r.column_name for r in granted} == columns, grantee


# --- pure selection --------------------------------------------------------------------------------------------


def _pool(counts: dict[str, int], *, seed: int = 1) -> list[PoolQuestion]:
    rng = random.Random(seed)
    return [
        PoolQuestion(
            question_id=uuid.UUID(int=rng.getrandbits(128)),
            question_version_id=uuid.UUID(int=rng.getrandbits(128)),
            item_type=item_type,
            content_hash="0" * 64,
        )
        for item_type, count in counts.items()
        for _ in range(count)
    ]


SECTIONS = [
    SectionRequirement("PART_I_MCQ", "SINGLE_CHOICE", 12, Decimal("0.25")),
    SectionRequirement("PART_II_TF4", "CLUSTER_TRUE_FALSE", 4, Decimal("1.00")),
    SectionRequirement("PART_III_SHORT", "SHORT_NUMERIC", 6, Decimal("0.50")),
]


def test_selection_is_deterministic_independent_of_input_order_and_fast():
    pool = _pool({"SINGLE_CHOICE": 1500, "CLUSTER_TRUE_FALSE": 600, "SHORT_NUMERIC": 900})
    first = select_items(pool, SECTIONS, 42)
    shuffled = list(pool)
    random.Random(3).shuffle(shuffled)
    assert select_items(shuffled, SECTIONS, 42) == first
    assert select_items(pool, SECTIONS, 43) != first
    started = time.perf_counter()
    select_items(pool, SECTIONS, 44)
    assert time.perf_counter() - started < 0.5, "spec: a THPTQG 2025+ paper is assembled in under 500 ms"
    assert [i.sequence_order for i in first] == list(range(1, 23))
    assert [i.section_code for i in first].count("PART_I_MCQ") == 12
    assert sum(i.points for i in first) == Decimal("10.00")


def test_selection_never_takes_two_questions_of_one_duplicate_cluster():
    cluster = uuid.uuid4()
    base = _pool({"SINGLE_CHOICE": 18, "CLUSTER_TRUE_FALSE": 6, "SHORT_NUMERIC": 8})
    pool = [
        PoolQuestion(q.question_id, q.question_version_id, q.item_type, q.content_hash, None, cluster) if i < 5 else q
        for i, q in enumerate(base)
    ]  # five single-choice questions are one and the same question
    for seed in range(40):
        chosen = select_items(pool, SECTIONS, seed)
        assert sum(1 for i in chosen if i.question_id in {q.question_id for q in pool[:5]}) <= 1


def test_selection_keeps_a_stimulus_cluster_whole_or_leaves_it_out():
    stimulus = uuid.uuid4()
    base = _pool({"SINGLE_CHOICE": 20, "CLUSTER_TRUE_FALSE": 6, "SHORT_NUMERIC": 8})
    group = {q.question_version_id for q in base[:3]}
    pool = [
        PoolQuestion(q.question_id, q.question_version_id, q.item_type, q.content_hash, stimulus, None)
        if q.question_version_id in group
        else q
        for q in base
    ]
    taken_counts = set()
    for seed in range(60):
        chosen = {i.question_version_id for i in select_items(pool, SECTIONS, seed)}
        taken_counts.add(len(chosen & group))
    assert taken_counts <= {0, 3}, f"a stimulus cluster was split: {taken_counts}"
    assert taken_counts == {0, 3}, "both outcomes should occur over many seeds"


def test_selection_reports_the_section_that_cannot_be_filled():
    pool = _pool({"SINGLE_CHOICE": 12, "CLUSTER_TRUE_FALSE": 4, "SHORT_NUMERIC": 5})
    with pytest.raises(AssemblyShortageError) as caught:
        select_items(pool, SECTIONS, 1)
    assert (caught.value.section_code, caught.value.required, caught.value.selected) == ("PART_III_SHORT", 6, 5)


def test_attempt_seeds_are_reproducible_and_individual():
    member = uuid.uuid4()
    assert derive_attempt_seed(20261111, member, 1) == derive_attempt_seed(20261111, member, 1)
    assert derive_attempt_seed(20261111, member, 1) != derive_attempt_seed(20261111, member, 2)
    assert derive_attempt_seed(20261111, member, 1) != derive_attempt_seed(20261111, uuid.uuid4(), 1)
    assert 0 <= derive_attempt_seed(1, member, 1) < 2**62


# --- C04 API ---------------------------------------------------------------------------------------------------


def test_a_new_paper_starts_fail_closed_and_only_authors_create_it(client, world):
    paper = _create_assessment(client, world)
    assert (paper["verification_status"], paper["publication_status"], paper["current_version_id"]) == (
        "UNCHECKED", "DRAFT", None,
    )  # fmt: skip
    assert paper["subject_code"] == "MATH" and paper["tenant_id"] == str(world["a"])
    body = {"title": "x", "subject_code": "MATH", "grade_band": "G12", "exam_type_code": "THPTQG"}
    assert client.post("/api/v1/assessments", json=body).status_code == 401
    assert client.post("/api/v1/assessments", headers=_student(world), json=body).status_code == 403
    system_only = client.post(
        "/api/v1/assessments", headers=_author(world), json={**body, "visibility_scope": "SYSTEM_PUBLIC"}
    )
    assert system_only.status_code == 422
    licensed = client.post("/api/v1/assessments", headers=_author(world), json={**body, "rights_status": "LICENSED"})
    assert licensed.status_code == 422


def test_assembling_a_thptqg_math_paper_is_deterministic_and_answer_free(client, world):
    _seed(world, {"SINGLE_CHOICE": 20, "CLUSTER_TRUE_FALSE": 8, "SHORT_NUMERIC": 10})
    paper = _create_assessment(client, world)
    started = time.perf_counter()
    first = _assemble(client, world, paper["id"], seed=2026)
    elapsed = time.perf_counter() - started
    assert first.status_code == 200, first.text
    assert elapsed < 0.5, f"assembly took {elapsed:.3f}s, spec says under 500 ms"
    version = first.json()
    assert (version["version_no"], version["is_immutable"], version["published_at"]) == (1, False, None)
    assert (version["duration_seconds"], version["total_points"], version["base_seed"]) == (5400, "10.00", 2026)
    sections = [i["section_code"] for i in version["items"]]
    assert [sections.count(s) for s in ("PART_I_MCQ", "PART_II_TF4", "PART_III_SHORT")] == [12, 4, 6]
    assert [i["sequence_order"] for i in version["items"]] == list(range(1, 23))
    assert sum(Decimal(i["points"]) for i in version["items"]) == Decimal("10.00")
    snapshot = version["structure_snapshot"]
    assert snapshot["framework_code"] == "THPTQG_2025" and snapshot["assessment_version_id"] == version["id"]
    _assert_no_answer_data(snapshot)
    stored = run(_scalar("SELECT structure_snapshot_json FROM assessment_versions WHERE id = :v", v=version["id"]))
    _assert_no_answer_data(stored)

    again = _assemble(client, world, paper["id"], seed=2026).json()
    assert [i["question_version_id"] for i in again["items"]] == [i["question_version_id"] for i in version["items"]]
    assert again["id"] == version["id"] and again["version_no"] == 1, "assembling again replaces the draft"
    other = _assemble(client, world, paper["id"], seed=2027).json()
    assert [i["question_version_id"] for i in other["items"]] != [i["question_version_id"] for i in version["items"]]
    assert run(_scalar("SELECT count(*) FROM assessment_versions")) == 1
    assert run(_scalar("SELECT count(*) FROM assessment_items")) == 22


def test_assembly_uses_only_published_verified_cleared_questions_of_the_right_subject(client, world):
    _seed(world, {"SINGLE_CHOICE": 12, "CLUSTER_TRUE_FALSE": 4, "SHORT_NUMERIC": 5})
    _seed(world, {"SHORT_NUMERIC": 3}, verified=False)  # not verified, so not published: never chosen
    run(_seed_questions(world["a"], world["physics"], {"SHORT_NUMERIC": 3}))  # another subject: not in a Math pool
    paper = _create_assessment(client, world)
    short = _assemble(client, world, paper["id"])
    assert short.status_code == 422 and short.json()["reason"] == "POOL_SHORTAGE"
    assert (short.json()["section_code"], short.json()["required"], short.json()["selected"]) == (
        "PART_III_SHORT",
        6,
        5,
    )
    assert run(_scalar("SELECT count(*) FROM assessment_versions")) == 0, "a failed assembly stores nothing"
    _seed(world, {"SHORT_NUMERIC": 1})  # one more cleared Math question completes the paper
    assert _assemble(client, world, paper["id"]).status_code == 200


def test_assembly_never_picks_two_questions_of_one_duplicate_cluster(client, world):
    cluster = uuid.uuid4()
    # exactly 6 short-numeric questions exist, two of them the same question: the paper cannot be filled
    _seed(
        world, {"SINGLE_CHOICE": 12, "CLUSTER_TRUE_FALSE": 4, "SHORT_NUMERIC": 6}, cluster_of={17: cluster, 18: cluster}
    )
    paper = _create_assessment(client, world)
    response = _assemble(client, world, paper["id"])
    assert response.status_code == 422 and response.json()["reason"] == "POOL_SHORTAGE"
    assert response.json()["selected"] == 5


def test_assembly_keeps_stimulus_clusters_together(client, world):
    stimulus = uuid.uuid4()
    _seed(
        world,
        {"SINGLE_CHOICE": 16, "CLUSTER_TRUE_FALSE": 6, "SHORT_NUMERIC": 8},
        stimulus=stimulus,
        stimulus_for=(1, 2, 3),
    )
    group = {r.id for r in run(_all("SELECT id FROM question_versions WHERE stimulus_id = :s", s=stimulus))}
    assert len(group) == 3
    paper = _create_assessment(client, world)
    for seed in range(12):
        version = _assemble(client, world, paper["id"], seed=seed).json()
        taken = {uuid.UUID(i["question_version_id"]) for i in version["items"]} & group
        assert len(taken) in (0, 3)


def test_assembling_needs_a_known_framework_the_papers_tenant_and_an_author(client, world):
    _seed(world)
    paper = _create_assessment(client, world)
    assert _assemble(client, world, paper["id"], framework_code="NOPE").status_code == 404
    assert _assemble(client, world, paper["id"], subject_code="LATIN").status_code == 404
    assert _assemble(client, world, paper["id"], who="b").status_code == 404
    assert _assemble(client, world, uuid.uuid4()).status_code == 404
    unauthorised = client.post(
        f"/api/v1/assessments/{paper['id']}/assemble", headers=_student(world), json={"random_seed": 1}
    )
    assert unauthorised.status_code == 403
    assert client.post(f"/api/v1/assessments/{paper['id']}/assemble", json={"random_seed": 1}).status_code == 401


def test_publishing_freezes_the_version_and_keeps_the_key_in_its_own_table(client, world):
    questions = _seed(world)
    paper = _create_assessment(client, world)
    assert _publish(client, world, paper["id"]).status_code == 409, "nothing is assembled yet"
    assembled = _assemble(client, world, paper["id"]).json()
    published = _publish(client, world, paper["id"])
    assert published.status_code == 200, published.text
    body = published.json()
    assert (body["assessment"]["publication_status"], body["assessment"]["verification_status"]) == (
        "PUBLISHED",
        "VERIFIED",
    )
    assert body["assessment"]["current_version_id"] == assembled["id"]
    assert body["version"]["is_immutable"] is True and body["version"]["published_at"] is not None
    _assert_no_answer_data(body["version"])

    key = run(_all("SELECT * FROM assessment_answer_keys"))[0]
    assert (key.key_revision_no, key.is_current, key.change_reason) == (1, True, "INITIAL_PUBLISH")
    payload = key.answer_key_payload_json
    assert len(payload["items"]) == 22
    by_version = {str(q["version_id"]): q["answer"] for q in questions}
    for qv_id, entry in payload["items"].items():
        assert entry["answer_source"] == "SNAPSHOT" and entry["answer_schema"] == by_version[qv_id]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert key.key_checksum_sha256 == hashlib.sha256(canonical.encode()).hexdigest() == body["answer_key_checksum"]
    publication = run(_all("SELECT entity_type, entity_id, version_id FROM publications"))[0]
    assert (publication.entity_type, str(publication.entity_id)) == ("ASSESSMENT", paper["id"])
    event = run(_all("SELECT event_type, payload_json FROM outbox_events WHERE event_type = 'assessment.published'"))[0]
    assert event.payload_json["assessment_version_id"] == assembled["id"]
    assert event.payload_json["framework_code"] == "THPTQG_2025"
    assert _publish(client, world, paper["id"]).status_code == 409, "a published paper has no draft to publish"


def test_the_database_refuses_any_change_to_a_published_version(client, world):
    _seed(world)
    published = _published_paper(client, world)
    version = published["version"]["id"]

    async def as_api():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            update = "UPDATE assessment_versions SET duration_seconds = 1 WHERE id = :v"
            await session.execute(text(update), {"v": version})

    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(as_api())
    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):  # even the owner cannot delete it
        run(_execute("DELETE FROM assessment_versions WHERE id = :v", v=version))
    assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'assessment_versions', 'DELETE')")) is False


def test_publishing_is_blocked_when_a_question_was_withdrawn_or_taken_down(client, world):
    questions = _seed(world)
    paper = _create_assessment(client, world)
    _assemble(client, world, paper["id"])
    withdrawn = "UPDATE questions SET publication_status = 'READY', verification_status = 'DISPUTED' WHERE id = :q"
    run(_execute(withdrawn, q=questions[0]["question_id"]))
    blocked = _publish(client, world, paper["id"])
    assert blocked.status_code == 422 and "PUBLISH_QUESTION_NOT_PUBLISHED" in blocked.json()["blocking_codes"]
    restored = "UPDATE questions SET verification_status = 'VERIFIED' WHERE id = :q"
    run(_execute(restored, q=questions[0]["question_id"]))
    run(_execute("UPDATE questions SET publication_status = 'PUBLISHED' WHERE id = :q", q=questions[0]["question_id"]))
    taken_down = "UPDATE questions SET publication_status = 'TAKEN_DOWN', rights_status = 'TAKEDOWN' WHERE id = :q"
    run(_execute(taken_down, q=questions[1]["question_id"]))
    again = _publish(client, world, paper["id"])
    assert again.status_code == 422 and "PUBLISH_QUESTION_NOT_PUBLISHED" in again.json()["blocking_codes"]
    assert run(_scalar("SELECT count(*) FROM assessment_answer_keys")) == 0, "a blocked publish writes no key"
    assert run(_scalar("SELECT publication_status FROM assessments")) == "DRAFT"


def test_publishing_is_blocked_when_a_question_has_a_newer_version(client, world):
    questions = _seed(world)
    paper = _create_assessment(client, world)
    _assemble(client, world, paper["id"])
    newer = uuid.uuid4()
    run(
        _execute(
            "INSERT INTO question_versions (id, tenant_id, question_id, version_no, stem_json, content_hash, "
            "structural_hash) SELECT :n, tenant_id, id, 2, CAST(:stem AS jsonb), :h, :h FROM questions "
            "WHERE id = :q",
            n=newer,
            stem=json.dumps({"text": "đã sửa"}),
            h="1" * 64,
            q=questions[0]["question_id"],
        )
    )
    run(_execute("UPDATE questions SET current_version_id = :n WHERE id = :q", n=newer, q=questions[0]["question_id"]))
    blocked = _publish(client, world, paper["id"])
    assert blocked.status_code == 422 and "QUESTION_VERSION_SUPERSEDED" in blocked.json()["blocking_codes"]


def test_a_paper_whose_own_rights_are_not_cleared_cannot_be_published(client, world):
    _seed(world)
    paper = _create_assessment(client, world, rights_status="PENDING_REVIEW")
    _assemble(client, world, paper["id"])
    blocked = _publish(client, world, paper["id"])
    assert blocked.status_code == 422 and "PUBLISH_RIGHTS_BLOCKED" in blocked.json()["blocking_codes"]


def test_an_item_may_only_use_own_or_frozen_system_public_question_versions(client, world):
    own = _seed(world, {"SHORT_NUMERIC": 1}, tenant="a")[0]
    foreign = _seed(world, {"SHORT_NUMERIC": 1}, tenant="b")[0]
    system = _seed(world, {"SHORT_NUMERIC": 1}, tenant="system", visibility="SYSTEM_PUBLIC")[0]
    private_system = _seed(world, {"SHORT_NUMERIC": 1}, tenant="system", visibility="TENANT_PRIVATE")[0]
    paper = _create_assessment(client, world)
    run(_execute("UPDATE assessments SET rights_status = 'OWNED' WHERE id = :a", a=paper["id"]))

    async def add_item(question_version: uuid.UUID, order: int):
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            scheme = (await session.execute(text("SELECT id FROM scoring_scheme_versions LIMIT 1"))).scalar_one()
            version = (
                await session.execute(
                    text(
                        "INSERT INTO assessment_versions (tenant_id, assessment_id, version_no, scoring_scheme_id, "
                        "duration_seconds, structure_snapshot_json, is_immutable) "
                        "VALUES (:t, :a, :n, :s, 60, '{}', false) RETURNING id"
                    ),
                    {"t": world["a"], "a": paper["id"], "n": order, "s": scheme},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO assessment_items (tenant_id, assessment_version_id, question_version_id, "
                    "section_code, sequence_order, points) VALUES (:t, :v, :q, 'PART_III_SHORT', 1, 0.5)"
                ),
                {"t": world["a"], "v": version, "q": question_version},
            )

    run(add_item(own["version_id"], 1))
    run(add_item(system["version_id"], 2))
    for n, refused in enumerate((foreign["version_id"], private_system["version_id"], uuid.uuid4()), start=3):
        with pytest.raises(DBAPIError, match="FK_NOT_FOUND|CROSS_TENANT_REFERENCE_FORBIDDEN"):
            run(add_item(refused, n))


def test_tenants_read_their_own_papers_and_published_system_papers_only(client, world):
    _seed(world, tenant="system", visibility="SYSTEM_PUBLIC")
    system_paper = _published_paper(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    system_draft = _create_assessment(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    _seed(world, tenant="b")
    _published_paper(client, world, who="b")
    _seed(world, tenant="a")
    own = _published_paper(client, world, who="a")

    def ids(sql, role=DbRole.APP_RW):
        return {str(r[0]) for r in run(_as_role(world["a"], role, sql))}

    visible = {own["assessment"]["id"], system_paper["assessment"]["id"]}
    assert ids("SELECT id FROM assessments") == visible
    assert system_draft["id"] not in ids("SELECT id FROM assessments")
    assert ids("SELECT id FROM assessment_versions") == {own["version"]["id"], system_paper["version"]["id"]}
    assert ids("SELECT DISTINCT assessment_version_id FROM assessment_items") == {
        own["version"]["id"], system_paper["version"]["id"],
    }  # fmt: skip
    assert ids("SELECT id FROM assessments", DbRole.EXAM_SESSION_RW) == visible
    # Keys stay strictly per tenant for the API role; the grading worker also reads the system tenant's.
    assert ids("SELECT assessment_version_id FROM assessment_answer_keys") == {own["version"]["id"]}
    grading = ids("SELECT assessment_version_id FROM assessment_answer_keys", DbRole.WORKER_GRADING)
    assert grading == {own["version"]["id"], system_paper["version"]["id"]}


# --- C05 API ---------------------------------------------------------------------------------------------------


def test_starting_a_session_fixes_seed_order_permutation_and_deadline(client, world):
    _seed(world)
    paper = _published_paper(client, world, seed=99)
    response = _start(client, world, paper["assessment"]["id"])
    assert response.status_code == 201, response.text
    started = response.json()
    assert started["resumed"] is False and started["status"] == "IN_PROGRESS" and started["duration_seconds"] == 5400
    began = datetime.fromisoformat(started["started_at"])
    assert datetime.fromisoformat(started["deadline_at"]) - began == timedelta(seconds=5400)
    assert abs((datetime.now(UTC) - began).total_seconds()) < 30, "started_at comes from the server clock"

    row = run(_all("SELECT * FROM attempts"))[0]
    assert row.attempt_seed == derive_attempt_seed(99, world["member_a1"], 1)
    assert len(row.question_order_ids) == len(set(row.question_order_ids)) == 22
    assert {str(v) for v in row.question_order_ids} == {i["question_version_id"] for i in paper["version"]["items"]}
    assert len(row.option_permutations_json) == 12, "only the single-choice questions are permuted"
    assert all(sorted(p) == ["A", "B", "C", "D"] for p in row.option_permutations_json.values())
    sections = {i["question_version_id"]: i["section_code"] for i in paper["version"]["items"]}
    ranks = [("PART_I_MCQ", "PART_II_TF4", "PART_III_SHORT").index(sections[str(v)]) for v in row.question_order_ids]
    assert ranks == sorted(ranks), "sections keep their order, questions are shuffled inside them"


def test_starting_twice_resumes_the_attempt_and_students_get_their_own_order(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    first = _start(client, world, paper["assessment"]["id"])
    again = _start(client, world, paper["assessment"]["id"])
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["attempt_id"] == first.json()["attempt_id"] and again.json()["resumed"] is True
    other = _start(client, world, paper["assessment"]["id"], key="a2")
    assert other.status_code == 201 and other.json()["attempt_id"] != first.json()["attempt_id"]
    orders = {str(r.membership_id): list(r.question_order_ids) for r in run(_all("SELECT * FROM attempts"))}
    assert len(orders) == 2 and len({tuple(o) for o in orders.values()}) == 2, "each student has an individual order"


def test_concurrent_starts_of_one_student_create_one_attempt(client, world):
    _seed(world)
    paper = _published_paper(client, world)

    async def start():
        async with tenant_transaction(world["a"], role=DbRole.EXAM_SESSION_RW) as session:
            result = await start_exam_session(
                session,
                tenant_id=world["a"],
                membership_id=world["member_a1"],
                assessment_id=uuid.UUID(paper["assessment"]["id"]),
            )
            return str(result.attempt.id), result.resumed

    async def race():
        return await asyncio.gather(*(start() for _ in range(6)))

    results = run(race())
    assert len({attempt for attempt, _ in results}) == 1
    assert sorted(resumed for _, resumed in results) == [False] + [True] * 5
    assert run(_scalar("SELECT count(*) FROM attempts")) == 1


def test_who_may_start_which_paper(client, world):
    _seed(world)
    published = _published_paper(client, world)
    draft = _create_assessment(client, world)
    pro = _published_paper(client, world, seed=5, access_tier="STUDENT_PRO")
    guest = uuid.uuid4()
    run(_execute("INSERT INTO users (id, email_blind_index) VALUES (:i, :b)", i=guest, b=uuid.uuid4().hex * 2))
    suspended = run(_membership(world["a"], guest, status="SUSPENDED"))
    assert _start(client, world, draft["id"]).status_code == 409
    assert _start(client, world, str(uuid.uuid4())).status_code == 404
    assert _start(client, world, published["assessment"]["id"], key="b1").status_code == 404, "another tenant's paper"
    refused = _start(client, world, pro["assessment"]["id"])
    assert refused.status_code == 403 and refused.json()["reason"] == "ENTITLEMENT_REQUIRED"
    run(_execute("UPDATE memberships SET entitlement_tier = 'STUDENT_PRO' WHERE id = :m", m=world["member_a1"]))
    assert _start(client, world, pro["assessment"]["id"]).status_code == 201
    inactive = client.post(
        "/api/v1/exam-sessions/start",
        headers=_headers(world["a"], guest, membership=suspended, roles=("LEARNER",)),
        json={"assessment_id": published["assessment"]["id"]},
    )
    assert inactive.status_code == 403 and inactive.json()["reason"] == "MEMBERSHIP_NOT_ACTIVE"
    unauthenticated = client.post("/api/v1/exam-sessions/start", json={"assessment_id": published["assessment"]["id"]})
    assert unauthenticated.status_code == 401


def test_the_paper_has_no_answer_data_and_is_identical_on_every_reload_and_device(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()
    url = f"/api/v1/attempts/{attempt['attempt_id']}/paper"
    first = client.get(url, headers=_student(world))
    assert first.status_code == 200, first.text
    reload_ = client.get(url, headers=_student(world))  # F5
    other_device = TestClient(app).get(url, headers=_student(world))  # a fresh client and token
    assert first.content == reload_.content == other_device.content
    body = first.json()
    _assert_no_answer_data(body)
    row = run(_all("SELECT question_order_ids, option_permutations_json FROM attempts"))[0]
    assert [q["question_version_id"] for q in body["questions"]] == [str(v) for v in row.question_order_ids]
    assert [q["display_position"] for q in body["questions"]] == list(range(1, 23))
    assert datetime.fromisoformat(body["deadline_at"]) == datetime.fromisoformat(attempt["deadline_at"])

    stored_options = {
        (str(r.question_version_id), r.option_key): r.content_json["text"]
        for r in run(_all("SELECT question_version_id, option_key, content_json FROM question_options"))
    }
    single = [q for q in body["questions"] if q["item_type"] == "SINGLE_CHOICE"]
    assert len(single) == 12
    for q in single:
        permutation = row.option_permutations_json[q["question_version_id"]]
        assert [o["display_label"] for o in q["options"]] == ["A", "B", "C", "D"]
        shown = [o["content_json"]["text"] for o in q["options"]]
        assert shown == [stored_options[(q["question_version_id"], key)] for key in permutation]
    assert any(row.option_permutations_json[q["question_version_id"]] != ["A", "B", "C", "D"] for q in single), (
        "at least one question must actually be permuted"
    )
    numeric = next(q for q in body["questions"] if q["item_type"] == "SHORT_NUMERIC")
    assert numeric["options"] == [] and numeric["numeric_parse_policy"]["max_chars"] == 4
    assert "tolerance" not in json.dumps(numeric)


def test_the_paper_is_served_only_to_its_student_while_the_attempt_is_open(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    url = f"/api/v1/attempts/{attempt}/paper"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=_student(world, "a2")).status_code == 404, "another student's attempt"
    assert client.get(url, headers=_student(world, "b1")).status_code == 404, "another tenant's attempt"
    assert client.get(f"/api/v1/attempts/{uuid.uuid4()}/paper", headers=_student(world)).status_code == 404
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '1 second'"))
    late = client.get(url, headers=_student(world))
    assert late.status_code == 403 and late.json()["reason"] == "ATTEMPT_DEADLINE_EXPIRED"
    run(_execute("UPDATE attempts SET deadline_at = now() + interval '1 hour', status = 'SUBMITTED'"))
    closed = client.get(url, headers=_student(world))
    assert closed.status_code == 409 and closed.json()["reason"] == "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED"


def test_submitting_is_idempotent_and_announced_once(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    url = f"/api/v1/attempts/{attempt}/submit"
    first = client.post(url, headers={**_student(world), "Idempotency-Key": "submit-1"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "SUBMITTED" and first.json()["replayed"] is False
    assert first.json()["submitted_at"] is not None
    replay = client.post(url, headers={**_student(world), "Idempotency-Key": "submit-1"})
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["submitted_at"] == first.json()["submitted_at"]
    different = client.post(url, headers={**_student(world), "Idempotency-Key": "submit-2"})
    assert different.status_code == 409 and different.json()["reason"] == "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED"
    assert client.post(url, headers=_student(world)).status_code == 409
    assert client.post(url, headers=_student(world, "a2")).status_code == 404
    events = run(_all("SELECT event_type, payload_json FROM outbox_events WHERE event_type LIKE 'attempt.%'"))
    assert [e.event_type for e in events] == ["attempt.submitted"]
    assert events[0].payload_json["attempt_id"] == attempt
    assert events[0].payload_json["membership_id"] == str(world["member_a1"])


def test_a_client_key_only_has_to_be_unique_per_attempt(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    for key in ("a1", "a2"):
        attempt = _start(client, world, paper["assessment"]["id"], key=key).json()["attempt_id"]
        done = client.post(
            f"/api/v1/attempts/{attempt}/submit", headers={**_student(world, key), "Idempotency-Key": "same-key"}
        )
        assert done.status_code == 200, done.text


def test_a_submission_after_the_deadline_is_auto_submitted_at_the_deadline(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '5 minutes'"))
    late = client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world))
    assert late.status_code == 200 and late.json()["status"] == "AUTO_SUBMITTED"
    row = run(_all("SELECT deadline_at, submitted_at FROM attempts"))[0]
    assert row.submitted_at == row.deadline_at
    events = run(_all("SELECT event_type, payload_json FROM outbox_events WHERE event_type LIKE 'attempt.%'"))
    assert [e.event_type for e in events] == ["attempt.expired"]
    assert datetime.fromisoformat(events[0].payload_json["auto_submitted_at"]) >= row.deadline_at


def test_starting_again_closes_an_expired_attempt_first(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    first = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '1 minute'"))
    second = _start(client, world, paper["assessment"]["id"])
    assert second.status_code == 201 and second.json()["attempt_id"] != first
    statuses = {str(r.id): r.status for r in run(_all("SELECT id, status FROM attempts"))}
    assert statuses[first] == "AUTO_SUBMITTED" and statuses[second.json()["attempt_id"]] == "IN_PROGRESS"
    seeds = [r.attempt_seed for r in run(_all("SELECT attempt_seed FROM attempts"))]
    assert len(set(seeds)) == 2, "the second attempt has its own seed"


def test_a_student_of_another_tenant_sits_a_system_public_paper(client, world):
    _seed(world, tenant="system", visibility="SYSTEM_PUBLIC")
    paper = _published_paper(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    key = run(_all("SELECT answer_key_payload_json FROM assessment_answer_keys"))[0].answer_key_payload_json
    assert all(e["answer_source"] == "SNAPSHOT" for e in key["items"].values()), "the system tenant owns these answers"
    started = _start(client, world, paper["assessment"]["id"], key="b1")
    assert started.status_code == 201, started.text
    body = client.get(f"/api/v1/attempts/{started.json()['attempt_id']}/paper", headers=_student(world, "b1"))
    assert body.status_code == 200 and len(body.json()["questions"]) == 22
    _assert_no_answer_data(body.json())
    done = client.post(f"/api/v1/attempts/{started.json()['attempt_id']}/submit", headers=_student(world, "b1"))
    assert done.status_code == 200
    row = run(_all("SELECT tenant_id, assessment_version_id FROM attempts"))[0]
    assert (row.tenant_id, str(row.assessment_version_id)) == (world["b"], paper["version"]["id"])


def test_a_paper_of_tenant_a_built_on_system_questions_names_them_in_its_key_without_copying_answers(client, world):
    _seed(world, tenant="system", visibility="SYSTEM_PUBLIC")
    paper = _published_paper(client, world, who="a")
    items = run(_all("SELECT answer_key_payload_json FROM assessment_answer_keys"))[0].answer_key_payload_json["items"]
    assert len(items) == 22
    assert {e["answer_source"] for e in items.values()} == {"SYSTEM_QUESTION_VERSION"}
    assert all("answer_schema" not in e for e in items.values())
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    assert client.get(f"/api/v1/attempts/{attempt}/paper", headers=_student(world)).status_code == 200


def test_the_expiry_job_sees_open_attempts_of_every_tenant_and_nothing_else(client, world):
    _seed(world, tenant="a")
    _seed(world, tenant="b")
    paper_a = _published_paper(client, world, who="a")
    paper_b = _published_paper(client, world, who="b")
    _start(client, world, paper_a["assessment"]["id"], key="a1")
    done = _start(client, world, paper_a["assessment"]["id"], key="a2").json()["attempt_id"]
    client.post(f"/api/v1/attempts/{done}/submit", headers=_student(world, "a2"))
    client.post(
        "/api/v1/exam-sessions/start",
        headers=_headers(world["b"], world["student_b1"], membership=world["member_b1"], roles=("LEARNER",)),
        json={"assessment_id": paper_b["assessment"]["id"]},
    )

    async def scan(sql):
        async with system_job_transaction(SystemJob.ATTEMPT_EXPIRY) as session:
            return (await session.execute(text(sql))).all()

    rows = run(scan("SELECT id, tenant_id, status, deadline_at FROM attempts"))
    assert {r.tenant_id for r in rows} == {world["a"], world["b"]} and {r.status for r in rows} == {"IN_PROGRESS"}
    assert len(rows) == 2, "the submitted attempt is not part of the scan"
    with pytest.raises(DBAPIError) as caught:  # the job gets four columns, not who is sitting the exam
        run(scan("SELECT membership_id FROM attempts"))
    assert _sqlstate(caught.value) == "42501"

    async def another_job():
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            return (await session.execute(text("SELECT count(*) FROM attempts"))).scalar()

    assert run(another_job()) == 0, "a job sees only the tables its own policy names"


def test_the_exam_room_role_cannot_read_a_single_answer(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    _start(client, world, paper["assessment"]["id"])
    for sql in (
        "SELECT answer_schema FROM question_version_answers",
        "SELECT answer_key_payload_json FROM assessment_answer_keys",
    ):
        with pytest.raises(DBAPIError) as caught:
            run(_as_role(world["a"], DbRole.EXAM_SESSION_RW, sql))
        assert _sqlstate(caught.value) == "42501"


# --- WAVE-8 hardening: expiry job (B5), column-level UPDATE (B3), registry cleanup (B2) -------------------------


def test_the_exam_room_cannot_move_the_deadline_but_can_still_close_an_attempt(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    started = _start(client, world, paper["assessment"]["id"]).json()
    tenant = world["a"]

    async def attempt_update(sql: str):
        async with tenant_transaction(tenant, role=DbRole.EXAM_SESSION_RW) as session:
            await session.execute(text(sql))

    with pytest.raises(DBAPIError) as denied:
        run(attempt_update("UPDATE attempts SET deadline_at = deadline_at + interval '1 day'"))
    assert _sqlstate(denied.value) == "42501"
    run(attempt_update("UPDATE attempts SET status = 'SUBMITTED', submitted_at = now()"))
    assert run(_scalar("SELECT status FROM attempts WHERE id = :i", i=uuid.UUID(started["attempt_id"]))) == "SUBMITTED"


def test_the_expiry_job_auto_submits_overdue_attempts_and_announces_them(client, world):
    from app.ops.attempt_expiry import expire_overdue_attempts

    _seed(world)
    paper = _published_paper(client, world)
    overdue = _start(client, world, paper["assessment"]["id"], key="a1").json()["attempt_id"]
    current = _start(client, world, paper["assessment"]["id"], key="a2").json()["attempt_id"]
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '2 minutes' WHERE id = :i", i=uuid.UUID(overdue)))
    assert run(expire_overdue_attempts()) == 1
    assert run(expire_overdue_attempts()) == 0, "a second pass finds nothing"
    statuses = {str(r.id): r for r in run(_all("SELECT id, status, submitted_at, deadline_at FROM attempts"))}
    assert statuses[overdue].status == "AUTO_SUBMITTED"
    assert statuses[overdue].submitted_at == statuses[overdue].deadline_at
    assert statuses[current].status == "IN_PROGRESS"
    events = run(_all("SELECT event_type, aggregate_id FROM outbox_events WHERE event_type LIKE 'attempt.%'"))
    assert [(e.event_type, str(e.aggregate_id)) for e in events] == [("attempt.expired", overdue)]


def test_the_expiry_job_cannot_touch_anything_but_overdue_in_progress_attempts(client, world):
    _seed(world)
    paper = _published_paper(client, world)
    _start(client, world, paper["assessment"]["id"])  # still running, deadline in the future

    async def attempt_job(sql: str) -> int:
        async with system_job_transaction(SystemJob.ATTEMPT_EXPIRY) as session:
            return (await session.execute(text(sql))).rowcount

    assert run(attempt_job("UPDATE attempts SET status = 'AUTO_SUBMITTED', submitted_at = deadline_at")) == 0
    with pytest.raises(DBAPIError):
        run(attempt_job("UPDATE attempts SET deadline_at = deadline_at + interval '1 day'"))
    with pytest.raises(DBAPIError):
        run(attempt_job("DELETE FROM attempts"))


def test_the_registry_cleanup_runs_as_the_scheduler_and_keeps_recent_keys(client, world):
    from app.ops.attempt_expiry import purge_idempotency_registry

    _seed(world)
    paper = _published_paper(client, world)
    attempt = uuid.UUID(_start(client, world, paper["assessment"]["id"]).json()["attempt_id"])
    run(
        _execute(
            "INSERT INTO autosave_idempotency_registry (tenant_id, attempt_id, idempotency_key, response_id, "
            "revision_no, client_sequence, payload_sha256, recorded_at) VALUES "
            "(:t, :a, 'old', uuid_generate_v7(), 1, 1, repeat('a', 64), now() - interval '40 days'), "
            "(:t, :a, 'new', uuid_generate_v7(), 1, 2, repeat('b', 64), now() - interval '1 day')",
            t=world["a"],
            a=attempt,
        )
    )
    assert run(purge_idempotency_registry()) == 1
    keys = [r.idempotency_key for r in run(_all("SELECT idempotency_key FROM autosave_idempotency_registry"))]
    assert keys == ["new"]
