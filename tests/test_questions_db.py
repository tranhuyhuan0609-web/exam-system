"""C01 questions (TASK-17): legacy rename, typed versions and answers, immutability, hybrid RLS, answer lockout.

DoD: all 6 item types are created and stored as versions; changing a published question creates
`version_no + 1` and leaves the old version as it was; `ulp_exam_session_rw` and `ulp_worker_ingestion` (and the
scheduler and analytics roles) get 42501 InsufficientPrivilege on `question_version_answers`.
Also: C02 `POST /question-versions/{id}/verify` against the stored answer, and the verification_runs foreign key.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.quality.internal import service as quality_service
from app.modules.quality.public import AnswerCheck
from app.modules.questions.public import (
    PublishGateBlockedError,
    QuestionConflictError,
    publish_question_version,
)
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction

SYSTEM = SYSTEM_PUBLIC_TENANT_ID
NEW_TABLES = (
    "questions", "question_stimuli", "question_versions", "question_version_answers", "question_options",
    "question_assets",
)  # fmt: skip
NO_ANSWER_ROLES = (
    DbRole.EXAM_SESSION_RW, DbRole.WORKER_INGESTION, DbRole.SYSTEM_SCHEDULER, DbRole.ANALYTICS_RO,
)  # fmt: skip

# One valid body per item type (spec C01: the 6 THPTQG 2025+ formats).
ABCD = [{"key": k, "text": f"Phương án {k}"} for k in "ABCD"]
ITEMS = {
    "SINGLE_CHOICE": {"options": ABCD, "answer_schema": {"correct_option": "B"}},
    "MULTIPLE_SELECT": {"options": ABCD, "answer_schema": {"correct_options": ["A", "C"]}},
    "CLUSTER_TRUE_FALSE": {
        "options": [{"key": k, "text": f"Mệnh đề {k}"} for k in "abcd"],
        "answer_schema": {"statements": {"a": True, "b": False, "c": True, "d": False}},
    },
    "SHORT_NUMERIC": {"answer_schema": {"value": "2,5"}},
    "ESSAY": {"answer_schema": {"rubric": "Nêu đủ 3 ý, mỗi ý 0,5 điểm", "max_points": 1.5}},
    "GROUP_STEM": {
        "stimulus": {"stimulus_type": "READING_PASSAGE", "body": {"text": "Đọc đoạn thí nghiệm sau..."}},
        "answer_schema": {"child_question_ids": ["Q-CHILD-1", "Q-CHILD-2"]},
    },
}


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


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Two tenants and a user for each, plus a user of the system tenant (users.id is referenced by versions)."""

    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"q-a-{suffix}", name="A"), Tenant(slug=f"q-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("user_a", "user_b", "user_system"):
                users[key] = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
            return {"a": a.id, "b": b.id, "system": SYSTEM, **users}

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


def _h(world, who="a", roles=("TEACHER",)):
    return _headers(world[who], world[f"user_{who}"], roles)


def _create(client, world, item_type="SHORT_NUMERIC", who="a", code=None, **extra):
    body = {
        "stable_code": code or f"Q-{uuid.uuid4().hex[:10]}",
        "item_type": item_type,
        "stem_text": f"Câu hỏi {item_type}: tính giá trị",
        **ITEMS[item_type],
        **extra,
    }
    response = client.post("/api/v1/questions", headers=_h(world, who), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _clear(question_id: str) -> None:
    """What the review (A09) does: verify the question and clear its rights."""
    run(
        _execute(
            "UPDATE questions SET verification_status = 'VERIFIED', rights_status = 'OWNED' WHERE id = :q",
            q=question_id,
        )
    )


def _publish(world, version_id, who="a"):
    async def go():
        async with tenant_transaction(world[who], role=DbRole.APP_RW) as session:
            return await publish_question_version(
                session, uuid.UUID(str(version_id)), approved_by=world[f"user_{who}"], correlation_id="corr-q"
            )

    return run(go())


def _published(client, world, who="a", item_type="SHORT_NUMERIC", **extra):
    created = _create(client, world, item_type=item_type, who=who, **extra)
    _clear(created["question"]["id"])
    _publish(world, created["version"]["id"], who=who)
    return created


# --- schema: legacy rename, tables, view, triggers, grants -----------------------------------------


def test_the_legacy_table_is_renamed_with_its_objects():
    names = {r.tablename for r in run(_all("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    assert {"legacy_questions", "questions"} <= names
    legacy_columns = {
        r.column_name
        for r in run(_all("SELECT column_name FROM information_schema.columns WHERE table_name = 'legacy_questions'"))
    }
    assert {"content", "choices", "root_id"} <= legacy_columns
    assert run(_scalar("SELECT pg_get_serial_sequence('legacy_questions', 'id')")) == "public.legacy_questions_id_seq"
    leftovers = run(
        _all(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'legacy_questions' "
            "AND indexname NOT LIKE '%legacy_questions%'"
        )
    )
    assert leftovers == []
    # The legacy link tables follow the rename (foreign keys point at the table, not its name).
    referenced = run(
        _all(
            "SELECT conrelid::regclass::text AS tbl FROM pg_constraint "
            "WHERE contype = 'f' AND confrelid = 'legacy_questions'::regclass"
        )
    )
    assert {"exam_questions", "question_categories"} <= {r.tbl for r in referenced}


def test_new_tables_are_under_forced_rls_and_the_view_is_security_invoker():
    for table in NEW_TABLES:
        forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
        assert forced is True, table
    options = run(_scalar("SELECT reloptions FROM pg_class WHERE relname = 'v_public_question_versions'"))
    assert "security_invoker=true" in options
    columns = [
        r.column_name
        for r in run(
            _all(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'v_public_question_versions' ORDER BY ordinal_position"
            )
        )
    ]
    assert "content_hash" not in columns and "answer_schema" not in columns and len(columns) == 9


def test_triggers_policies_and_the_verification_run_foreign_key_exist():
    triggers = {r.tgname for r in run(_all("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
    assert {
        "trg_lock_question_version_on_publish",
        "trg_immutable_question_versions",
        "trg_immutable_question_version_answers",
    } <= triggers
    policies = {
        (r.tablename, r.policyname)
        for r in run(_all("SELECT tablename, policyname FROM pg_policies WHERE tablename LIKE 'question%'"))
    }
    assert ("question_version_answers", "grading_read_question_answers") in policies
    assert ("question_version_answers", "tenant_isolation_policy") in policies
    for table in ("questions", "question_versions", "question_options", "question_assets", "question_stimuli"):
        assert (table, "hybrid_read_policy") in policies and (table, "hybrid_write_policy") in policies
    fk = run(
        _scalar(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE conname = 'fk_verification_runs_question_version_tenant'"
        )
    )
    assert fk == "question_versions"


@pytest.mark.parametrize("role", NO_ANSWER_ROLES)
def test_answer_table_privileges_are_revoked_from_every_role_that_must_not_see_answers(role):
    granted = run(
        _scalar(
            "SELECT has_table_privilege(:r, 'question_version_answers', 'SELECT') "
            "OR has_table_privilege(:r, 'question_version_answers', 'INSERT') "
            "OR has_table_privilege(:r, 'question_version_answers', 'UPDATE')",
            r=role.value,
        )
    )
    assert granted is False


def test_the_grading_worker_and_the_api_role_keep_their_spec_grants():
    assert run(_scalar("SELECT has_table_privilege('ulp_worker_grading', 'question_version_answers', 'SELECT')"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_worker_grading', 'question_version_answers', 'UPDATE')"))
    assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'question_version_answers', 'INSERT')"))
    assert run(_scalar("SELECT has_table_privilege('ulp_exam_session_rw', 'v_public_question_versions', 'SELECT')"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_exam_session_rw', 'question_versions', 'UPDATE')"))
    assert not run(_scalar("SELECT has_table_privilege('public', 'questions', 'SELECT')"))


# --- GATE-03: the exam room never reads an answer ---------------------------------------------------


@pytest.mark.parametrize("role", NO_ANSWER_ROLES)
def test_reading_answers_fails_with_42501_for_the_exam_room_ingestion_scheduler_and_analytics(client, world, role):
    _create(client, world)
    with pytest.raises(DBAPIError) as caught:
        run(_as_role(world["a"], role, "SELECT answer_schema FROM question_version_answers"))
    assert _sqlstate(caught.value) == "42501", caught.value


def test_the_exam_room_reads_the_question_but_not_its_answer(client, world):
    created = _published(client, world, item_type="SINGLE_CHOICE")
    rows = run(
        _as_role(world["a"], DbRole.EXAM_SESSION_RW, "SELECT stem_json, options_json FROM v_public_question_versions")
    )
    assert len(rows) == 1 and rows[0].stem_json["text"].startswith("Câu hỏi SINGLE_CHOICE")
    options = run(_as_role(world["a"], DbRole.EXAM_SESSION_RW, "SELECT option_key, content_json FROM question_options"))
    assert sorted(r.option_key for r in options) == list("ABCD")
    assert all("correct" not in str(r.content_json).lower() for r in options)
    # Nothing in the version row tells the answer either.
    assert "B" not in {str(v) for v in created["version"]["stem"].values()}
    assert "answer_schema" not in created["version"]


def test_the_grading_worker_reads_answers_of_its_tenant_and_of_system_papers(client, world):
    _published(client, world, who="system", item_type="SHORT_NUMERIC")
    _create(client, world, who="b", item_type="SHORT_NUMERIC")
    seen = run(_as_role(world["a"], DbRole.WORKER_GRADING, "SELECT tenant_id FROM question_version_answers"))
    assert {r.tenant_id for r in seen} == {SYSTEM}


# --- the six item types -----------------------------------------------------------------------------


@pytest.mark.parametrize("item_type", list(ITEMS))
def test_each_item_type_is_stored_with_its_answer_kept_apart(client, world, item_type):
    created = _create(client, world, item_type=item_type)
    question, version = created["question"], created["version"]
    assert question["item_type"] == item_type and version["version_no"] == 1 and not version["is_immutable"]
    assert (question["verification_status"], question["rights_status"], question["publication_status"]) == (
        "UNCHECKED", "PENDING_REVIEW", "DRAFT",
    )  # fmt: skip
    assert question["current_version_id"] is None  # no version is served before publication
    assert len(version["content_hash"]) == 64 and len(version["structural_hash"]) == 64
    answer = run(
        _scalar("SELECT answer_schema FROM question_version_answers WHERE question_version_id = :v", v=version["id"])
    )
    assert answer == ITEMS[item_type]["answer_schema"]
    option_keys = run(
        _all(
            "SELECT option_key FROM question_options WHERE question_version_id = :v ORDER BY sequence_order",
            v=version["id"],
        )
    )
    assert [r.option_key for r in option_keys] == [o["key"] for o in ITEMS[item_type].get("options", [])]
    if item_type == "GROUP_STEM":
        stimulus = run(_scalar("SELECT stimulus_type FROM question_stimuli WHERE id = :s", s=version["stimulus_id"]))
        assert stimulus == "READING_PASSAGE"


@pytest.mark.parametrize(
    ("item_type", "override", "reason"),
    [
        ("SINGLE_CHOICE", {"answer_schema": {"correct_option": "E"}}, "INVALID_ANSWER_KEY"),
        ("MULTIPLE_SELECT", {"answer_schema": {"correct_options": []}}, "INVALID_ANSWER_KEY"),
        ("CLUSTER_TRUE_FALSE", {"options": ABCD}, "INVALID_CLUSTER_TF_OPTIONS"),
        ("SHORT_NUMERIC", {"answer_schema": {"value": "12345"}}, "NUMERIC_ANSWER_TOO_LONG"),
        ("ESSAY", {"answer_schema": {"rubric": "x", "max_points": 0}}, "INVALID_ESSAY_POINTS"),
        ("GROUP_STEM", {"stimulus": None}, "MISSING_STIMULUS_ID"),
    ],
)
def test_an_invalid_question_is_refused_and_nothing_is_stored(client, world, item_type, override, reason):
    body = {"stable_code": "Q-BAD", "item_type": item_type, "stem_text": "Câu hỏi", **ITEMS[item_type], **override}
    response = client.post("/api/v1/questions", headers=_h(world), json=body)
    assert response.status_code == 422, response.text
    assert response.json()["reason"] == reason
    assert run(_scalar("SELECT count(*) FROM questions")) == 0
    assert run(_scalar("SELECT count(*) FROM question_stimuli")) == 0


def test_a_stable_code_is_unique_per_tenant_only(client, world):
    _create(client, world, code="LEG-1")
    again = client.post(
        "/api/v1/questions",
        headers=_h(world),
        json={"stable_code": "LEG-1", "item_type": "SHORT_NUMERIC", "stem_text": "x", **ITEMS["SHORT_NUMERIC"]},
    )
    assert again.status_code == 409 and again.json()["reason"] == "STABLE_CODE_TAKEN"
    _create(client, world, who="b", code="LEG-1")


def test_a_group_stem_can_reuse_a_stimulus_of_its_tenant_only(client, world):
    first = _create(client, world, item_type="GROUP_STEM")
    stimulus_id = first["version"]["stimulus_id"]
    body = {**ITEMS["GROUP_STEM"], "stimulus": None, "stimulus_id": stimulus_id}
    second = _create(client, world, item_type="GROUP_STEM", **body)
    assert second["version"]["stimulus_id"] == stimulus_id
    foreign = client.post(
        "/api/v1/questions",
        headers=_h(world, "b"),
        json={"stable_code": "Q-X", "item_type": "GROUP_STEM", "stem_text": "x", **body},
    )
    assert foreign.status_code == 404


def test_system_public_questions_come_only_from_the_system_tenant(client, world):
    response = client.post(
        "/api/v1/questions",
        headers=_h(world),
        json={"stable_code": "Q-1", "item_type": "ESSAY", "stem_text": "x", "visibility_scope": "SYSTEM_PUBLIC",
              **ITEMS["ESSAY"]},
    )  # fmt: skip
    assert response.status_code == 422
    assert run(_scalar("SELECT count(*) FROM questions")) == 0


def test_authentication_and_roles(client, world):
    body = {"stable_code": "Q-1", "item_type": "ESSAY", "stem_text": "x", **ITEMS["ESSAY"]}
    assert client.post("/api/v1/questions", json=body).status_code == 401
    assert client.post("/api/v1/questions", headers=_h(world, roles=("STUDENT",)), json=body).status_code == 403
    created = _published(client, world)
    url = f"/api/v1/questions/{created['question']['id']}/versions"
    assert client.post(url, headers=_h(world, "b"), json={"change_reason": "x"}).status_code == 404
    validate = f"/api/v1/question-versions/{created['version']['id']}/validate"
    assert client.post(validate, headers=_h(world, "b")).status_code == 404


# --- versions: publish freezes, a change is version_no + 1 ------------------------------------------


def test_publishing_freezes_the_version_and_its_answer_and_announces_it(client, world):
    created = _create(client, world, item_type="SINGLE_CHOICE")
    _clear(created["question"]["id"])
    result = _publish(world, created["version"]["id"])
    assert result.question.publication_status == "PUBLISHED"
    assert result.question.current_version_id == result.version.id and result.version.is_immutable
    assert result.version.approved_by == world["user_a"]
    frozen = run(
        _scalar(
            "SELECT is_immutable FROM question_version_answers WHERE question_version_id = :v",
            v=created["version"]["id"],
        )
    )
    assert frozen is True
    publication = run(_all("SELECT entity_type, entity_id, version_id FROM publications"))[0]
    assert (publication.entity_type, str(publication.entity_id)) == ("QUESTION", created["question"]["id"])
    event = run(_all("SELECT event_type, payload_json FROM outbox_events"))[0]
    assert event.event_type == "question.version.published"
    assert event.payload_json["version_id"] == created["version"]["id"]


def test_the_publish_gate_needs_verification_and_cleared_rights(client, world):
    created = _create(client, world)
    with pytest.raises(PublishGateBlockedError) as blocked:
        _publish(world, created["version"]["id"])
    assert {"PUBLISH_NOT_VERIFIED", "PUBLISH_RIGHTS_BLOCKED"} <= set(blocked.value.codes)
    # The database refuses the same thing on its own (chk_question_publish_gate).
    with pytest.raises(IntegrityError, match="chk_question_publish_gate"):
        run(
            _execute(
                "UPDATE questions SET publication_status = 'PUBLISHED', current_version_id = :v WHERE id = :q",
                v=created["version"]["id"],
                q=created["question"]["id"],
            )
        )


def test_changing_a_published_question_creates_the_next_version_and_keeps_the_old_one(client, world):
    created = _published(client, world, item_type="SINGLE_CHOICE")
    question_id, v1 = created["question"]["id"], created["version"]
    response = client.post(
        f"/api/v1/questions/{question_id}/versions",
        headers=_h(world),
        json={"change_reason": "Sửa lỗi chính tả ở đề", "stem_text": "Câu hỏi đã sửa",
              "answer_schema": {"correct_option": "C"}},
    )  # fmt: skip
    assert response.status_code == 201, response.text
    v2 = response.json()
    assert (v2["version_no"], v2["is_immutable"], v2["change_reason"]) == (2, False, "Sửa lỗi chính tả ở đề")
    assert v2["stem"] == {"text": "Câu hỏi đã sửa"} and v2["options"] == v1["options"]
    old = run(_all("SELECT stem_json, content_hash, is_immutable FROM question_versions WHERE id = :v", v=v1["id"]))[0]
    assert (old.stem_json, old.content_hash, old.is_immutable) == (v1["stem"], v1["content_hash"], True)
    answers = dict(
        (str(r.question_version_id), r.answer_schema)
        for r in run(_all("SELECT question_version_id, answer_schema FROM question_version_answers"))
    )
    assert answers == {v1["id"]: {"correct_option": "B"}, v2["id"]: {"correct_option": "C"}}
    # The question still serves version 1 until version 2 is published.
    assert str(run(_scalar("SELECT current_version_id FROM questions WHERE id = :q", q=question_id))) == v1["id"]
    _publish(world, v2["id"])
    assert str(run(_scalar("SELECT current_version_id FROM questions WHERE id = :q", q=question_id))) == v2["id"]
    assert run(_scalar("SELECT count(*) FROM question_versions WHERE is_immutable")) == 2


def test_only_one_draft_at_a_time_and_a_new_version_is_validated(client, world):
    draft = _create(client, world)
    url = f"/api/v1/questions/{draft['question']['id']}/versions"
    conflict = client.post(url, headers=_h(world), json={"change_reason": "x"})
    assert conflict.status_code == 409 and conflict.json()["reason"] == "DRAFT_EXISTS"
    published = _published(client, world)
    url = f"/api/v1/questions/{published['question']['id']}/versions"
    invalid = client.post(url, headers=_h(world), json={"change_reason": "x", "answer_schema": {"value": "2e3"}})
    assert invalid.status_code == 422 and invalid.json()["reason"] == "INVALID_NUMERIC_ANSWER"
    assert client.post(url, headers=_h(world), json={"change_reason": "x"}).status_code == 201
    assert client.post(url, headers=_h(world), json={"change_reason": "y"}).json()["reason"] == "DRAFT_EXISTS"


def test_publishing_an_older_or_frozen_version_is_a_conflict(client, world):
    created = _published(client, world)
    with pytest.raises(QuestionConflictError, match="frozen"):
        _publish(world, created["version"]["id"])


@pytest.mark.parametrize(
    "sql",
    [
        'UPDATE question_versions SET stem_json = \'{"text": "sửa trộm"}\' WHERE id = :v',
        "DELETE FROM question_versions WHERE id = :v",
        'UPDATE question_version_answers SET answer_schema = \'{"value": "9"}\' WHERE question_version_id = :v',
        "DELETE FROM question_version_answers WHERE question_version_id = :v",
    ],
)
def test_the_database_refuses_any_change_to_a_frozen_version_or_answer(client, world, sql):
    created = _published(client, world)

    async def as_api():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text(sql), {"v": created["version"]["id"]})

    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(as_api())


def test_a_draft_version_can_still_be_edited_by_the_api_role(client, world):
    _create(client, world)

    async def as_api():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return (
                await session.execute(text('UPDATE question_version_answers SET answer_schema = \'{"value": "3"}\''))
            ).rowcount

    assert run(as_api()) == 1


# --- validate ----------------------------------------------------------------------------------------


def test_validate_reports_on_the_stored_version_and_changes_nothing(client, world):
    created = _create(client, world, item_type="SINGLE_CHOICE")
    url = f"/api/v1/question-versions/{created['version']['id']}/validate"
    ok = client.post(url, headers=_h(world))
    assert ok.status_code == 200 and ok.json() == {
        "version_id": created["version"]["id"], "valid": True, "code": None, "message": None,
    }  # fmt: skip
    run(
        _execute(
            'UPDATE question_version_answers SET answer_schema = \'{"correct_option": "Z"}\' '
            "WHERE question_version_id = :v",
            v=created["version"]["id"],
        )
    )
    bad = client.post(url, headers=_h(world)).json()
    assert bad["valid"] is False and bad["code"] == "INVALID_ANSWER_KEY"
    assert client.post(f"/api/v1/question-versions/{uuid.uuid4()}/validate", headers=_h(world)).status_code == 404


# --- hybrid RLS ---------------------------------------------------------------------------------------


def test_tenants_see_their_own_questions_and_published_system_ones_only(client, world):
    own = _create(client, world, who="a")
    _create(client, world, who="b")
    system_draft = _create(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    system_published = _published(client, world, who="system", visibility_scope="SYSTEM_PUBLIC",
                                  item_type="SINGLE_CHOICE")  # fmt: skip
    _published(client, world, who="system")  # published, but TENANT_PRIVATE to the system tenant

    def ids(sql):
        return {str(r[0]) for r in run(_as_role(world["a"], DbRole.APP_RW, sql))}

    assert ids("SELECT id FROM questions") == {own["question"]["id"], system_published["question"]["id"]}
    assert ids("SELECT id FROM question_versions") == {own["version"]["id"], system_published["version"]["id"]}
    assert ids("SELECT question_version_id FROM question_options") == {system_published["version"]["id"]}
    assert system_draft["question"]["id"] not in ids("SELECT question_id FROM v_public_question_versions")
    # Answers stay strictly per tenant, even for the system tenant's published questions.
    assert ids("SELECT question_version_id FROM question_version_answers") == {own["version"]["id"]}


def test_the_analytics_role_reads_the_view_under_rls(client, world):
    """F-02: through the invoker view the role sees what RLS lets it see: its tenant's versions and the published
    system ones, never another tenant's or a system draft."""
    own = _create(client, world, who="a")
    _create(client, world, who="b")
    _create(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    published = _published(client, world, who="system", visibility_scope="SYSTEM_PUBLIC")
    rows = run(_as_role(world["a"], DbRole.ANALYTICS_RO, "SELECT id FROM v_public_question_versions"))
    assert {str(r.id) for r in rows} == {own["version"]["id"], published["version"]["id"]}
    with pytest.raises(DBAPIError) as caught:  # the hashes and audit columns are not part of the view's grant
        run(_as_role(world["a"], DbRole.ANALYTICS_RO, "SELECT content_hash FROM question_versions"))
    assert _sqlstate(caught.value) == "42501"


def test_a_tenant_cannot_write_into_another_tenant(client, world):
    created = _create(client, world, who="a")

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            changed = (await session.execute(text("UPDATE questions SET difficulty_level = 'VDC'"))).rowcount
            await session.execute(
                text(
                    "INSERT INTO question_stimuli (tenant_id, stimulus_type, body_json) VALUES (:t, 'DATA_TABLE', '{}')"
                ),
                {"t": world["a"]},
            )
            return changed

    with pytest.raises(DBAPIError, match="row-level security"):
        run(as_b())
    assert run(_scalar("SELECT difficulty_level FROM questions WHERE id = :q", q=created["question"]["id"])) == "TH"


# --- C02: verify a question version -------------------------------------------------------------------


def _verify(client, world, version_id, who="a", **body):
    return client.post(
        f"/api/v1/question-versions/{version_id}/verify", headers=_h(world, who), json={"expression": "5/2", **body}
    )


def test_verify_compares_with_the_stored_answer_and_moves_the_question(client, world, monkeypatch):
    seen = []

    def fake(expr, answer, tolerance):
        seen.append((expr, answer, tolerance))
        return AnswerCheck(matches=answer in {"2,5", "2.5"}, computed="2.5")

    monkeypatch.setattr(quality_service, "_default_verifier", fake)
    created = _create(client, world)  # SHORT_NUMERIC, stored answer "2,5"
    response = _verify(client, world, created["version"]["id"])
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["question_verification_status"] == "AUTO_VERIFIED"
    assert data["run"]["question_version_id"] == created["version"]["id"] and data["run"]["candidate_id"] is None
    assert data["run"]["findings"]["source_answer_from"] == "question_version_answers"
    assert seen == [("5/2", "2,5", Decimal("0.01"))]
    mismatch = _verify(client, world, created["version"]["id"], source_answer="3")
    assert mismatch.json()["question_verification_status"] == "DISPUTED"
    assert run(_scalar("SELECT count(*) FROM verification_runs WHERE question_version_id IS NOT NULL")) == 2


def test_verify_leaves_human_decisions_published_questions_and_failed_checks_alone(client, world, monkeypatch):
    monkeypatch.setattr(quality_service, "_default_verifier", lambda e, s, t: AnswerCheck(matches=False, computed="1"))
    published = _published(client, world)
    assert _verify(client, world, published["version"]["id"]).json()["question_verification_status"] == "VERIFIED"
    monkeypatch.setattr(
        quality_service,
        "_default_verifier",
        lambda e, s, t: AnswerCheck(matches=False, error="SANDBOX_WORKER_UNAVAILABLE"),
    )
    draft = _create(client, world)
    out = _verify(client, world, draft["version"]["id"]).json()
    assert out["question_verification_status"] == "UNCHECKED" and out["run"]["is_match_with_source"] is None


def test_verify_checks_input_tenant_and_role(client, world, monkeypatch):
    monkeypatch.setattr(quality_service, "_default_verifier", lambda e, s, t: AnswerCheck(matches=True, computed="1"))
    essay = _create(client, world, item_type="ESSAY")
    no_answer = _verify(client, world, essay["version"]["id"])
    assert no_answer.status_code == 422 and "source_answer is required" in no_answer.json()["detail"]
    numeric = _create(client, world)
    assert _verify(client, world, numeric["version"]["id"], who="b").status_code == 404
    student = client.post(
        f"/api/v1/question-versions/{numeric['version']['id']}/verify",
        headers=_h(world, roles=("STUDENT",)),
        json={"expression": "1"},
    )
    assert student.status_code == 403
    assert _verify(client, world, numeric["version"]["id"], expression="x" * 501).status_code == 422
    assert run(_scalar("SELECT count(*) FROM verification_runs")) == 0


def test_a_verification_run_must_name_a_version_of_its_own_tenant(client, world):
    created = _create(client, world, who="a")
    with pytest.raises(IntegrityError, match="fk_verification_runs_question_version_tenant"):
        run(
            _execute(
                "INSERT INTO verification_runs (tenant_id, question_version_id, verifier_type, model_or_tool_version) "
                "VALUES (:t, :v, 'HUMAN_EXPERT', 'x')",
                t=world["b"],
                v=created["version"]["id"],
            )
        )
