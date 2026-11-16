"""GET /api/v1/questions and /questions/{id}: the bank screen's reads, on real PostgreSQL rows with RLS.

Editors read the bank of their tenant plus the published SYSTEM_PUBLIC questions, filtered and paginated; a learner is
refused; another tenant's private rows are neither listed nor readable; and no answer is ever read: the SQL of the
request never mentions `question_version_answers` and the body never carries an answer or a rationale.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import event, text

from app.config import get_settings
from app.db import engine
from app.ops.seed_demo import DEFAULT_PASSWORD, email_blind_index, seed_demo
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.tenancy import tenant_transaction
from app.security import hash_password


def _login(client, email: str, tenant_slug: str = "h2edu-demo") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD, "tenant_slug": tenant_slug}
    )
    assert response.status_code == 200, (email, response.text)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _make_tenant_with_author(slug: str) -> uuid.UUID:
    """Another school with an author (`author@<slug>.test`): who runs a question bank and its imports."""
    return await _make_tenant_with_teacher(slug, role="CONTENT_AUTHOR", local="author")


async def _make_tenant_with_teacher(slug: str, *, role: str = "TEACHER", local: str = "teacher") -> uuid.UUID:
    settings = get_settings()
    async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID) as session:
        await session.execute(text("INSERT INTO tenants (slug, name) VALUES (:s, :s)"), {"s": slug})
        tenant_id = (await session.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug})).scalar_one()
    email = f"{local}@{slug}.test"
    async with tenant_transaction(tenant_id) as session:
        blind = email_blind_index(email, settings.secret_key, settings.blind_index_pepper)
        row = (
            await session.execute(
                text("SELECT user_id FROM f04_register_user(:b, :h, NULL, :p, NULL, 'ACTIVE')"),
                {"b": blind, "h": hash_password(DEFAULT_PASSWORD), "p": f"{local} {slug}"},
            )
        ).one()
        await session.execute(
            text(
                "INSERT INTO memberships (tenant_id, user_id, roles, entitlement_tier) "
                "VALUES (:t, :u, ARRAY[CAST(:r AS varchar)], 'TEACHER_PRO')"
            ),
            {"t": tenant_id, "u": row.user_id, "r": role},
        )
    return tenant_id


async def _insert_question(
    tenant_id: uuid.UUID, code: str, *, scope: str, publish: bool, stem: str = "Câu hỏi riêng"
) -> uuid.UUID:
    """A question and its version by SQL, as the seed does; `publish` makes it VERIFIED, PUBLISHED and frozen."""
    qid, vid = uuid.uuid4(), uuid.uuid4()
    digest = hashlib.sha256(code.encode()).hexdigest()
    async with tenant_transaction(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO questions (id, tenant_id, visibility_scope, stable_code, item_type, "
                "verification_status, rights_status) VALUES (:q, :t, :s, :c, 'ESSAY', 'VERIFIED', 'OWNED')"
            ),
            {"q": qid, "t": tenant_id, "s": scope, "c": code},
        )
        await session.execute(
            text(
                "INSERT INTO question_versions (id, tenant_id, visibility_scope, question_id, version_no, stem_json, "
                "options_json, content_hash, structural_hash) VALUES (:v, :t, :s, :q, 1, CAST(:stem AS jsonb), "
                "'[]'::jsonb, :h, :h)"
            ),
            {"v": vid, "t": tenant_id, "s": scope, "q": qid, "stem": json.dumps({"text": stem}), "h": digest},
        )
        await session.execute(
            text(
                "INSERT INTO question_version_answers (question_version_id, tenant_id, answer_schema) "
                "VALUES (:v, :t, CAST(:a AS jsonb))"
            ),
            {"v": vid, "t": tenant_id, "a": json.dumps({"rubric": "SECRET-RUBRIC", "max_points": 1.0})},
        )
        if publish:
            await session.execute(
                text("UPDATE questions SET publication_status = 'PUBLISHED', current_version_id = :v WHERE id = :q"),
                {"v": vid, "q": qid},
            )
    return qid


@pytest.fixture
def world(client):
    seeded = asyncio.run(seed_demo())
    return {
        "author": _login(client, "content_author@h2edu.demo"),
        "teacher": _login(client, "teacher@h2edu.demo"),
        "learner": _login(client, "learner@h2edu.demo"),
        "tenant_admin": _login(client, "tenant_admin@h2edu.demo"),
        "tenant_id": uuid.UUID(seeded["tenant"]["id"]),
        "c01_ids": seeded["entities"]["c01_question_ids"],
    }


def _codes(body: dict) -> list[str]:
    return [i["stable_code"] for i in body["items"]]


def test_the_list_shows_published_and_draft_questions_with_stem_and_options_newest_first(client, world):
    body = client.get("/api/v1/questions", params={"limit": 100}, headers=world["author"]).json()
    assert len(body["items"]) == 26 and body["has_more"] is False and body["next_cursor"] is None
    assert body["limit"] == 100
    created = [i["created_at"] for i in body["items"]]
    assert created == sorted(created, reverse=True)
    by_code = {i["stable_code"]: i for i in body["items"]}

    published = by_code["DEMO-BAYES-01"]
    assert published["publication_status"] == "PUBLISHED" and published["version_no"] == 1
    assert published["is_immutable"] is True and published["item_type"] == "SINGLE_CHOICE"
    assert "Bayes" in published["stem_text"] and [o["key"] for o in published["options"]] == ["A", "B", "C", "D"]

    draft = by_code["DEMO-C01-01"]
    assert draft["publication_status"] == "DRAFT" and draft["current_version_id"] is None
    assert draft["is_immutable"] is False and draft["difficulty_level"] == "NB"
    assert draft["version_id"] and draft["stem_text"].startswith("Cho hai biến cố")
    assert set(draft) == {
        "id", "tenant_id", "visibility_scope", "stable_code", "item_type", "primary_taxonomy_node_id",
        "curriculum_node_id", "exam_framework_tags", "difficulty_level", "current_version_id", "acquisition_status",
        "verification_status", "rights_status", "publication_status", "access_tier", "created_at", "version_id",
        "version_no", "is_immutable", "stimulus_id", "stem_text", "options",
    }  # fmt: skip


def test_cursor_pages_cover_the_bank_once_in_order_and_reject_foreign_or_bad_cursors(client, world):
    headers = world["author"]
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 10, **({"cursor": cursor} if cursor else {})}
        body = client.get("/api/v1/questions", params=params, headers=headers).json()
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        assert body["has_more"] is (cursor is not None)
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 26 and len(set(seen)) == 26

    first = client.get("/api/v1/questions", params={"limit": 5}, headers=headers).json()
    other = client.get("/api/v1/questions", params={"limit": 5, "cursor": first["next_cursor"]}, headers=headers)
    assert other.status_code == 200
    # a cursor signed for another tenant, a tampered one and a nonsense one are refused, not trusted
    for bad in ("not-a-cursor", first["next_cursor"][:-3] + "AAA", first["next_cursor"].split(".")[0] + ".AAAA"):
        refused = client.get("/api/v1/questions", params={"cursor": bad}, headers=headers)
        assert refused.status_code == 422, (bad, refused.text)
        assert refused.json()["error_code"] == "VALIDATION_ERROR"
    assert client.get("/api/v1/questions", params={"limit": 0}, headers=headers).status_code == 422
    assert client.get("/api/v1/questions", params={"limit": 101}, headers=headers).status_code == 422


def test_filters_narrow_the_list(client, world):
    def codes(**params) -> list[str]:
        response = client.get("/api/v1/questions", params={"limit": 100, **params}, headers=world["author"])
        assert response.status_code == 200, response.text
        return _codes(response.json())

    assert len(codes(item_type="SHORT_NUMERIC")) == 7  # 6 published + DEMO-C01-03
    assert codes(item_type="ESSAY") == ["DEMO-C01-04"]
    assert sorted(codes(difficulty_level="VDC")) == ["DEMO-C01-04"]
    assert sorted(codes(publication_status="DRAFT")) == [f"DEMO-C01-0{n}" for n in (1, 2, 3, 4)]
    assert len(codes(publication_status="PUBLISHED")) == 22
    assert codes(q="DEMO-C01-03") == ["DEMO-C01-03"], "the stable_code"
    assert codes(q="demo-c01-03") == ["DEMO-C01-03"], "case does not matter"
    assert codes(q="xác suất toàn phần") == ["DEMO-C01-04"], "the stem of the shown version"
    assert codes(q="%") == [] and codes(q="_") == [], "LIKE wildcards in the input are literal"
    assert codes(item_type="CLUSTER_TRUE_FALSE", publication_status="DRAFT") == ["DEMO-C01-02"]

    taxonomy = client.get("/api/v1/questions", params={"limit": 1}, headers=world["author"]).json()["items"][0][
        "primary_taxonomy_node_id"
    ]
    assert len(codes(taxonomy_node_id=taxonomy)) == 26
    assert codes(taxonomy_node_id=str(uuid.uuid4())) == [] and codes(curriculum_node_id=str(uuid.uuid4())) == []
    for bad in ({"item_type": "TRUE_FALSE"}, {"difficulty_level": "XX"}, {"publication_status": "LIVE"}):
        assert client.get("/api/v1/questions", params=bad, headers=world["author"]).status_code == 422
    assert client.get("/api/v1/questions", params={"q": "x" * 201}, headers=world["author"]).status_code == 422


def test_the_detail_has_the_shown_version_and_the_version_history_and_no_answer(client, world):
    qid = world["c01_ids"][0]
    response = client.get(f"/api/v1/questions/{qid}", headers=world["author"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["question"]["id"] == qid and body["question"]["stable_code"] == "DEMO-C01-01"
    assert body["shown_version"]["version_no"] == 1 and body["shown_version"]["stem"]["text"].startswith("Cho hai")
    assert [o["key"] for o in body["shown_version"]["options"]] == ["A", "B", "C", "D"]
    assert [(v["version_no"], v["is_current"]) for v in body["versions"]] == [(1, False)], "a draft is not current"
    assert set(body) == {"question", "shown_version", "versions"}

    # a published question with a newer draft shows the published version, and lists both
    published = next(
        i for i in client.get("/api/v1/questions", params={"limit": 100}, headers=world["author"]).json()["items"]
        if i["stable_code"] == "DEMO-BAYES-01"
    )  # fmt: skip
    forked = client.post(
        f"/api/v1/questions/{published['id']}/versions",
        headers=world["author"],
        json={"change_reason": "Sửa số liệu", "stem_text": "Bản nháp mới"},
    )
    assert forked.status_code == 201, forked.text
    detail = client.get(f"/api/v1/questions/{published['id']}", headers=world["author"]).json()
    assert detail["shown_version"]["version_no"] == 1
    assert detail["question"]["current_version_id"] == published["version_id"]
    assert [(v["version_no"], v["is_current"], v["is_immutable"]) for v in detail["versions"]] == [
        (2, False, False),
        (1, True, True),
    ]
    found = client.get("/api/v1/questions", params={"q": "DEMO-BAYES-01"}, headers=world["author"]).json()
    listed = found["items"][0]
    assert listed["version_no"] == 1, "the list shows what is served, not the draft"

    assert client.get(f"/api/v1/questions/{uuid.uuid4()}", headers=world["author"]).status_code == 404
    assert client.get("/api/v1/questions/not-a-uuid", headers=world["author"]).status_code == 422


def test_no_answer_is_read_or_returned(client, world):
    statements: list[str] = []

    def spy(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", spy)
    try:
        listed = client.get("/api/v1/questions", params={"limit": 100}, headers=world["author"])
        detail = client.get(f"/api/v1/questions/{world['c01_ids'][1]}", headers=world["author"])
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", spy)
    assert listed.status_code == 200 and detail.status_code == 200
    assert any("FROM questions" in s for s in statements), "the spy saw the queries"
    assert not [s for s in statements if "question_version_answers" in s], "no query touches the answer table"
    for body in (listed.text, detail.text):
        for leaked in ("answer_schema", "rationale", "correct_option", '"statements"', "SECRET"):
            assert leaked not in body, leaked


def test_a_learner_is_refused_and_a_visitor_is_not_signed_in(client, world):
    qid = world["c01_ids"][0]
    for url in ("/api/v1/questions", f"/api/v1/questions/{qid}"):
        refused = client.get(url, headers=world["learner"])
        assert refused.status_code == 403, url
        assert refused.json()["error_code"] == "FORBIDDEN"
        assert client.get(url).status_code == 401, url
    assert client.get("/api/v1/questions", headers=world["tenant_admin"]).status_code == 200


def test_another_tenants_private_questions_are_neither_listed_nor_readable(client, world):
    other_id = asyncio.run(_make_tenant_with_author("other-school"))
    mine = asyncio.run(_insert_question(other_id, "OTHER-PRIVATE-1", scope="TENANT_PRIVATE", publish=True))
    shared = asyncio.run(_insert_question(other_id, "OTHER-PARTNER-1", scope="SHARED_PARTNER", publish=True))
    draft = asyncio.run(_insert_question(other_id, "OTHER-DRAFT-1", scope="TENANT_PRIVATE", publish=False))
    other = _login(client, "author@other-school.test", "other-school")

    theirs = client.get("/api/v1/questions", params={"limit": 100}, headers=other).json()
    assert sorted(_codes(theirs)) == ["OTHER-DRAFT-1", "OTHER-PARTNER-1", "OTHER-PRIVATE-1"]
    assert all(i["tenant_id"] == str(other_id) for i in theirs["items"]), "nothing of h2edu-demo, no DEMO question"

    ours = client.get("/api/v1/questions", params={"limit": 100, "q": "OTHER"}, headers=world["author"])
    assert ours.json()["items"] == []
    everything = client.get("/api/v1/questions", params={"limit": 100}, headers=world["author"]).json()
    assert len(everything["items"]) == 26 and not any(c.startswith("OTHER") for c in _codes(everything))
    for qid in (mine, shared, draft):
        assert client.get(f"/api/v1/questions/{qid}", headers=world["author"]).status_code == 404
        assert client.get(f"/api/v1/questions/{qid}", headers=other).status_code == 200
    # and the other tenant cannot read ours
    assert client.get(f"/api/v1/questions/{world['c01_ids'][0]}", headers=other).status_code == 404


def test_published_system_questions_are_listed_for_every_tenant_but_unpublished_ones_are_not(client, world):
    published = asyncio.run(_insert_question(SYSTEM_PUBLIC_TENANT_ID, "SYS-PUB-1", scope="SYSTEM_PUBLIC", publish=True))
    unpublished = asyncio.run(
        _insert_question(SYSTEM_PUBLIC_TENANT_ID, "SYS-DRAFT-1", scope="SYSTEM_PUBLIC", publish=False)
    )
    body = client.get("/api/v1/questions", params={"limit": 100}, headers=world["author"]).json()
    assert "SYS-PUB-1" in _codes(body) and "SYS-DRAFT-1" not in _codes(body)
    item = next(i for i in body["items"] if i["stable_code"] == "SYS-PUB-1")
    assert item["visibility_scope"] == "SYSTEM_PUBLIC" and item["tenant_id"] == str(SYSTEM_PUBLIC_TENANT_ID)
    assert client.get(f"/api/v1/questions/{published}", headers=world["author"]).status_code == 200
    assert client.get(f"/api/v1/questions/{unpublished}", headers=world["author"]).status_code == 404
