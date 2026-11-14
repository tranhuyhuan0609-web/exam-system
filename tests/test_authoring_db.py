"""B02 authoring (TASK-12): drafts, Block Studio validation, asset ownership and the published block rows.

DoD: a draft with broken KaTeX, a forbidden command or another tenant's asset is reported (100%) and never
reaches a version; a valid draft is applied to the item's DRAFT version; the blocks of a published version are
written as rows in the same transaction as the freeze.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction

SYSTEM = SYSTEM_PUBLIC_TENANT_ID
GOOD = [
    {"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"text": "Định luật $F = ma$"}},
    {"block_order": 1, "block_type": "FORMULA_BOX", "payload_json": {"latex": "$$v = \\frac{s}{t}$$"}},
]


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


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"aut-a-{suffix}", name="A"), Tenant(slug=f"aut-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("author", "colleague", "user_b"):
                users[key] = (
                    await session.execute(
                        text("INSERT INTO users (email_blind_index) VALUES (:b) RETURNING id"),
                        {"b": uuid.uuid4().hex + uuid.uuid4().hex},
                    )
                ).scalar_one()
            return {"a": a.id, "b": b.id, **users}

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


def _item(client, world, slug="bai-1", blocks=None, who="a", user="author"):
    body = {"content_type": "LESSON", "slug": slug, "title": "Bài 1", "blocks": GOOD if blocks is None else blocks}
    response = client.post("/api/v1/content", headers=_headers(world[who], world[user]), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _draft(client, world, item_id, user="author", who="a", **body):
    return client.post(f"/api/v1/content/{item_id}/drafts", headers=_headers(world[who], world[user]), json=body)


def _asset(tenant: uuid.UUID, role: str = "PAGE_RENDER") -> uuid.UUID:
    async def create():
        async with SessionLocal() as session, session.begin():
            return (
                await session.execute(
                    text(
                        "INSERT INTO assets (tenant_id, asset_role, object_key, mime_type, sha256, size_bytes) "
                        "VALUES (:t, :r, :k, 'image/png', :s, 1) RETURNING id"
                    ),
                    {"t": tenant, "r": role, "k": f"k/{uuid.uuid4()}", "s": "0" * 64},
                )
            ).scalar_one()

    return run(create())


# --- schema ----------------------------------------------------------------------------------------


def test_tables_and_policies_follow_the_spec():
    for table in ("content_drafts", "content_blocks", "comment_threads"):
        assert run(
            _scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table)
        ), table
    for table in ("content_drafts", "comment_threads"):
        qual = run(
            _scalar(
                "SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation_policy'", t=table
            )
        )
        assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in qual
    blocks = run(
        _scalar("SELECT qual FROM pg_policies WHERE tablename = 'content_blocks' AND policyname = 'hybrid_read_policy'")
    )
    assert "content_versions" in blocks and "system_public_tenant_id()" in blocks  # the spec lists blocks as hybrid
    for role in ("ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_worker_grading", "ulp_analytics_ro"):
        assert not run(_scalar("SELECT has_table_privilege(:r, 'content_drafts', 'SELECT')", r=role)), role


# --- POST /content/{id}/drafts ---------------------------------------------------------------------


def test_a_draft_starts_as_a_copy_of_the_newest_version_and_is_reused(client, world):
    item = _item(client, world)["item"]
    first = _draft(client, world, item["id"])
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["author_id"] == str(world["author"]) and body["tenant_id"] == str(world["a"])
    assert body["working_blocks"] == GOOD and body["validation_errors"] == []

    again = _draft(client, world, item["id"])
    assert again.status_code == 200 and again.json()["id"] == body["id"]  # retry loses nothing
    assert run(_scalar("SELECT count(*) FROM content_drafts")) == 1
    colleague = _draft(client, world, item["id"], user="colleague")
    assert colleague.status_code == 201 and colleague.json()["id"] != body["id"]  # one draft per author


def test_a_draft_can_start_from_given_blocks(client, world):
    item = _item(client, world)["item"]
    blocks = [{"block_order": 0, "block_type": "TIP_WARNING", "payload_json": {"text": "Cẩn thận"}}]
    created = _draft(client, world, item["id"], blocks=blocks)
    assert created.status_code == 201 and created.json()["working_blocks"] == blocks
    replaced = _draft(client, world, item["id"], blocks=GOOD)
    assert replaced.status_code == 200 and replaced.json()["working_blocks"] == GOOD


def test_draft_creation_is_for_the_tenants_own_items_only(client, world):
    item = _item(client, world)["item"]
    assert _draft(client, world, item["id"], who="b", user="user_b").status_code == 404
    assert _draft(client, world, str(uuid.uuid4())).status_code == 404
    assert run(_scalar("SELECT count(*) FROM content_drafts")) == 0


def test_a_system_item_visible_to_the_tenant_cannot_be_drafted_on(client, world):
    async def seed():
        async with tenant_transaction(SYSTEM) as session:
            item_id, version_id = uuid.uuid4(), uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO content_items (id, tenant_id, content_type, slug, visibility_scope, "
                    "publication_status, "
                    "rights_status, verification_status, current_version_id) "
                    "VALUES (:i, :t, 'LESSON', 'sys', 'SYSTEM_PUBLIC', 'PUBLISHED', 'OWNED', 'VERIFIED', :v)"
                ),
                {"i": item_id, "t": SYSTEM, "v": version_id},
            )
            await session.execute(
                text(
                    "INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status) "
                    "VALUES (:v, :t, :i, 1, 's', 'PUBLISHED')"
                ),
                {"v": version_id, "t": SYSTEM, "i": item_id},
            )
            return item_id

    system_item = run(seed())
    assert _draft(client, world, str(system_item)).status_code == 404


def test_roles_and_tokens(client, world):
    item = _item(client, world)["item"]
    assert client.post(f"/api/v1/content/{item['id']}/drafts", json={}).status_code == 401
    student = _headers(world["a"], world["author"], ("STUDENT",))
    assert client.post(f"/api/v1/content/{item['id']}/drafts", headers=student, json={}).status_code == 403
    bad_block = {"blocks": [{"block_order": -1, "block_type": "THEORY_SUMMARY"}]}
    assert _draft(client, world, item["id"], **bad_block).status_code == 422


# --- PATCH /drafts/{id} ----------------------------------------------------------------------------


def test_patch_replaces_the_working_blocks_and_clears_old_errors(client, world):
    item = _item(client, world)["item"]
    draft = _draft(client, world, item["id"]).json()
    broken = [{"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"text": "$\\frac{1}{$"}}]
    patched = client.patch(
        f"/api/v1/drafts/{draft['id']}", headers=_headers(world["a"], world["author"]), json={"blocks": broken}
    )
    assert patched.status_code == 200 and patched.json()["working_blocks"] == broken
    client.post(f"/api/v1/drafts/{draft['id']}/validate", headers=_headers(world["a"], world["author"]), json={})
    assert run(_scalar("SELECT jsonb_array_length(validation_errors_json) FROM content_drafts")) == 1
    fixed = client.patch(
        f"/api/v1/drafts/{draft['id']}", headers=_headers(world["a"], world["author"]), json={"blocks": GOOD}
    )
    assert fixed.json()["validation_errors"] == []  # the errors described the old blocks


def test_only_the_author_or_an_administrator_changes_a_draft(client, world):
    item = _item(client, world)["item"]
    draft = _draft(client, world, item["id"]).json()
    url = f"/api/v1/drafts/{draft['id']}"
    colleague = _headers(world["a"], world["colleague"])
    assert client.patch(url, headers=colleague, json={"blocks": []}).status_code == 403
    assert client.post(f"{url}/validate", headers=colleague, json={}).status_code == 403
    admin = _headers(world["a"], world["colleague"], ("TENANT_ADMIN",))
    assert client.patch(url, headers=admin, json={"blocks": []}).status_code == 200
    stranger = _headers(world["b"], world["user_b"], ("TENANT_ADMIN",))
    assert client.patch(url, headers=stranger, json={"blocks": GOOD}).status_code == 404  # other tenant: not there
    assert client.post(f"{url}/validate", headers=stranger, json={}).status_code == 404
    assert client.patch(f"/api/v1/drafts/{uuid.uuid4()}", headers=colleague, json={"blocks": []}).status_code == 404


# --- POST /drafts/{id}/validate ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"text": "$\\frac{1}{$"}, "UNBALANCED_KATEX_BRACE"),
        ({"text": "$\\input{secret}$"}, "FORBIDDEN_KATEX_COMMAND"),
        ({"text": "$\\href{http://x}{y}$"}, "FORBIDDEN_KATEX_COMMAND"),
        ({"text": "$\\def\\x{1}$"}, "FORBIDDEN_KATEX_COMMAND"),
        ({"src": "https://evil.example/a.png"}, "EXTERNAL_MEDIA_URL_FORBIDDEN"),
        ({"image": "asset://00000000-0000-0000-0000-00000000dead"}, "MISSING_ASSET"),
    ],
)
def test_validation_reports_every_class_of_error_and_records_it(client, world, payload, code):
    item = _item(client, world)["item"]
    draft = _draft(
        client, world, item["id"],
        blocks=[{"block_order": 3, "block_type": "WORKED_EXAMPLE", "payload_json": payload}],
    ).json()  # fmt: skip
    response = client.post(
        f"/api/v1/drafts/{draft['id']}/validate", headers=_headers(world["a"], world["author"]), json={}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is False and body["applied_version_id"] is None
    assert code in {i["code"] for i in body["issues"]} and all(i["block_order"] == 3 for i in body["issues"])
    assert body["draft"]["validation_errors"] == body["issues"]
    stored = run(_scalar("SELECT validation_errors_json FROM content_drafts WHERE id = :d", d=draft["id"]))
    assert stored == body["issues"]


def test_an_interactive_question_embed_needs_a_question_id(client, world):
    item = _item(client, world)["item"]
    draft = _draft(
        client, world, item["id"],
        blocks=[{"block_order": 0, "block_type": "INTERACTIVE_QUESTION_EMBED", "payload_json": {}}],
    ).json()  # fmt: skip
    body = client.post(
        f"/api/v1/drafts/{draft['id']}/validate", headers=_headers(world["a"], world["author"]), json={}
    ).json()
    assert [i["code"] for i in body["issues"]] == ["MISSING_EMBEDDED_QUESTION_ID"]


def test_asset_ownership_own_system_and_foreign(client, world):
    own, system_asset, foreign = _asset(world["a"]), _asset(SYSTEM), _asset(world["b"])
    system_raw = _asset(SYSTEM, "RAW_SOURCE")
    item = _item(client, world)["item"]
    url = lambda d: f"/api/v1/drafts/{d}/validate"  # noqa: E731
    headers = _headers(world["a"], world["author"])

    def validate_with(asset_id) -> dict:
        blocks = [{"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"image": f"asset://{asset_id}"}}]
        draft = _draft(client, world, item["id"], blocks=blocks).json()
        return client.post(url(draft["id"]), headers=headers, json={}).json()

    assert validate_with(own)["valid"] is True
    assert validate_with(system_asset)["valid"] is True  # public material of the system tenant
    for hidden in (foreign, system_raw):  # another tenant's asset, and a bronze original: invisible, "missing"
        outcome = validate_with(hidden)
        assert outcome["valid"] is False and [i["code"] for i in outcome["issues"]] == ["MISSING_ASSET"]


def test_a_valid_draft_is_applied_to_the_draft_version_and_then_published(client, world):
    created = _item(client, world, blocks=[])
    item_id, version_id = created["item"]["id"], created["version"]["id"]
    draft = _draft(client, world, item_id, blocks=GOOD).json()
    headers = _headers(world["a"], world["author"])
    validated = client.post(f"/api/v1/drafts/{draft['id']}/validate", headers=headers, json={"apply_to_version": True})
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True and validated.json()["applied_version_id"] == version_id
    assert run(_scalar("SELECT blocks_json FROM content_versions WHERE id = :v", v=version_id)) == GOOD

    run(
        _execute(
            "UPDATE content_items SET verification_status = 'VERIFIED', rights_status = 'OWNED' WHERE id = :i",
            i=item_id,
        )
    )
    published = client.post(f"/api/v1/content-versions/{version_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    rows = run(
        _all(
            "SELECT block_order, block_type, payload_json, tenant_id FROM content_blocks "
            "WHERE content_version_id = :v ORDER BY block_order",
            v=version_id,
        )
    )
    assert [(r.block_order, r.block_type, r.payload_json) for r in rows] == [
        (b["block_order"], b["block_type"], b["payload_json"]) for b in GOOD
    ]
    assert {r.tenant_id for r in rows} == {world["a"]}


def test_an_invalid_draft_is_never_applied(client, world):
    created = _item(client, world)
    draft = _draft(
        client, world, created["item"]["id"],
        blocks=[{"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"text": "$\\input{x}$"}}],
    ).json()  # fmt: skip
    body = client.post(
        f"/api/v1/drafts/{draft['id']}/validate",
        headers=_headers(world["a"], world["author"]),
        json={"apply_to_version": True},
    ).json()
    assert body["valid"] is False and body["applied_version_id"] is None
    assert run(_scalar("SELECT blocks_json FROM content_versions WHERE id = :v", v=created["version"]["id"])) == GOOD


def test_applying_to_a_published_version_needs_the_next_version_first(client, world):
    created = _item(client, world)
    item_id, version_id = created["item"]["id"], created["version"]["id"]
    run(
        _execute(
            "UPDATE content_items SET verification_status = 'VERIFIED', rights_status = 'OWNED' WHERE id = :i",
            i=item_id,
        )
    )
    headers = _headers(world["a"], world["author"])
    assert client.post(f"/api/v1/content-versions/{version_id}/publish", headers=headers).status_code == 200

    draft = _draft(client, world, item_id).json()
    blocked = client.post(f"/api/v1/drafts/{draft['id']}/validate", headers=headers, json={"apply_to_version": True})
    assert blocked.status_code == 409 and "next version" in blocked.json()["detail"]
    assert client.post(f"/api/v1/content/{item_id}/versions", headers=headers, json={}).status_code == 201
    edited = [{"block_order": 0, "block_type": "TIP_WARNING", "payload_json": {"text": "Bản sửa"}}]
    client.patch(f"/api/v1/drafts/{draft['id']}", headers=headers, json={"blocks": edited})
    applied = client.post(f"/api/v1/drafts/{draft['id']}/validate", headers=headers, json={"apply_to_version": True})
    assert applied.status_code == 200 and applied.json()["applied_version_id"] is not None
    assert run(_scalar("SELECT blocks_json FROM content_versions WHERE version_no = 1")) == GOOD  # history untouched
    assert run(_scalar("SELECT blocks_json FROM content_versions WHERE version_no = 2")) == edited


# --- content_blocks rows ---------------------------------------------------------------------------


def test_republishing_the_next_version_writes_its_own_block_rows(client, world):
    created = _item(client, world)
    item_id, v1 = created["item"]["id"], created["version"]["id"]
    run(
        _execute(
            "UPDATE content_items SET verification_status = 'VERIFIED', rights_status = 'OWNED' WHERE id = :i",
            i=item_id,
        )
    )
    headers = _headers(world["a"], world["author"])
    client.post(f"/api/v1/content-versions/{v1}/publish", headers=headers)
    edited = [{"block_order": 0, "block_type": "TIP_WARNING", "payload_json": {"text": "v2"}}]
    v2 = client.post(f"/api/v1/content/{item_id}/versions", headers=headers, json={"blocks": edited}).json()["id"]
    assert client.post(f"/api/v1/content-versions/{v2}/publish", headers=headers).status_code == 200
    counts = dict(run(_all("SELECT content_version_id::text, count(*) FROM content_blocks GROUP BY 1")))
    assert counts == {v1: 2, v2: 1}


def test_tenant_b_cannot_read_or_change_tenant_a_authoring_rows(client, world):
    created = _item(client, world)
    item_id, version_id = created["item"]["id"], created["version"]["id"]
    run(
        _execute(
            "UPDATE content_items SET verification_status = 'VERIFIED', rights_status = 'OWNED' WHERE id = :i",
            i=item_id,
        )
    )
    client.post(f"/api/v1/content-versions/{version_id}/publish", headers=_headers(world["a"], world["author"]))
    _draft(client, world, item_id)
    run(
        _execute(
            "INSERT INTO comment_threads (tenant_id, target_type, target_id, author_id, body_text) "
            "VALUES (:t, 'CONTENT', :i, :u, 'ổn')",
            t=world["a"],
            i=item_id,
            u=world["author"],
        )
    )

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            counts = [
                (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
                for t in ("content_drafts", "content_blocks", "comment_threads")
            ]
            changed = (await session.execute(text("UPDATE content_drafts SET working_blocks_json = '[]'"))).rowcount
            deleted = (await session.execute(text("DELETE FROM content_blocks"))).rowcount
            return counts, changed, deleted

    assert run(as_b()) == ([0, 0, 0], 0, 0)
    assert run(_scalar("SELECT count(*) FROM content_blocks")) == 2


def test_system_blocks_are_visible_only_through_a_visible_published_version(world):
    async def seed():
        async with tenant_transaction(SYSTEM) as session:
            for slug, publication, status in (("pub", "PUBLISHED", "PUBLISHED"), ("draft", "DRAFT", "DRAFT")):
                item_id, version_id = uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO content_items (id, tenant_id, content_type, slug, visibility_scope, "
                        "publication_status, "
                        "rights_status, verification_status, current_version_id) "
                        "VALUES (:i, :t, 'LESSON', :s, 'SYSTEM_PUBLIC', :p, 'OWNED', 'VERIFIED', :cv)"
                    ),
                    {
                        "i": item_id,
                        "t": SYSTEM,
                        "s": slug,
                        "p": publication,
                        "cv": version_id if publication == "PUBLISHED" else None,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status) "
                        "VALUES (:v, :t, :i, 1, :s, :st)"
                    ),
                    {"v": version_id, "t": SYSTEM, "i": item_id, "s": slug, "st": "DRAFT"},
                )
                # Blocks are written while the version is a draft: once frozen, the database refuses them.
                await session.execute(
                    text(
                        "INSERT INTO content_blocks "
                        "(tenant_id, content_version_id, block_order, block_type, payload_json) "
                        "VALUES (:t, :v, 0, 'THEORY_SUMMARY', CAST(:p AS jsonb))"
                    ),
                    {"t": SYSTEM, "v": version_id, "p": '{"text": "' + slug + '"}'},
                )
                await session.execute(
                    text("UPDATE content_versions SET status = :st WHERE id = :v"), {"st": status, "v": version_id}
                )

    run(seed())

    async def as_a():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return sorted((await session.execute(text("SELECT payload_json->>'text' FROM content_blocks"))).scalars())

    assert run(as_a()) == ["pub"]  # the blocks of the draft system version stay hidden


def test_comment_threads_are_tenant_scoped(world):
    async def comment(context_tenant, row_tenant, user, body):
        async with tenant_transaction(context_tenant, role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO comment_threads (tenant_id, target_type, target_id, author_id, body_text) "
                    "VALUES (:t, 'CONTENT', :i, :u, :b)"
                ),
                {"t": row_tenant, "i": uuid.uuid4(), "u": user, "b": body},
            )

    run(comment(world["a"], world["a"], world["author"], "của A"))
    with pytest.raises(DBAPIError, match="row-level security"):
        run(comment(world["a"], SYSTEM, world["author"], "giả mạo"))  # a row of another tenant, from A's context
    with pytest.raises(DBAPIError, match="row-level security"):
        run(comment(world["b"], world["a"], world["user_b"], "chen ngang"))

    async def count(tenant):
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            return (await session.execute(text("SELECT count(*) FROM comment_threads"))).scalar()

    assert run(count(world["a"])) == 1 and run(count(world["b"])) == 0
