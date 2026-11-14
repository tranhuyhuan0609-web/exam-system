"""B01 content (TASK-11): items, immutable versions, the publish gate, hybrid RLS and the /api/v1 routes.

DoD: a direct UPDATE of a PUBLISHED version is refused by the database (IMMUTABLE_VIOLATION); changing
published content creates `version_no + 1` as DRAFT; the publish gate cannot be bypassed.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction

SYSTEM = SYSTEM_PUBLIC_TENANT_ID
BLOCKS = [
    {"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"text": "Công thức $E = mc^2$"}},
    {"block_order": 1, "block_type": "FORMULA_BOX", "payload_json": {"latex": "$$\\frac{a}{b}$$"}},
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
    """Two tenants, a user for each (users.id is referenced by created_by / published_by)."""

    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"cnt-a-{suffix}", name="A"), Tenant(slug=f"cnt-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            users = {}
            for key in ("user_a", "user_b"):
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


def _create(client, world, who="a", slug="bai-1", **extra):
    body = {"content_type": "LESSON", "slug": slug, "title": "Bài 1", "blocks": BLOCKS, **extra}
    response = client.post("/api/v1/content", headers=_headers(world[who], world[f"user_{who}"]), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _clear(item_id: str, rights: str = "OWNED") -> None:
    """What the review (A09) does: verify the item and clear its rights."""
    run(
        _execute(
            "UPDATE content_items SET verification_status = 'VERIFIED', rights_status = :r WHERE id = :i",
            r=rights,
            i=item_id,
        )
    )


def _publish(client, world, version_id, who="a"):
    return client.post(
        f"/api/v1/content-versions/{version_id}/publish", headers=_headers(world[who], world[f"user_{who}"])
    )


# --- schema --------------------------------------------------------------------------------------


def test_tables_triggers_and_the_deferred_foreign_key_exist():
    for table in ("content_items", "content_versions", "content_assets", "publications", "access_grants"):
        assert run(
            _scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table)
        ), table
    fk = run(
        _all("SELECT condeferrable, condeferred FROM pg_constraint WHERE conname = 'fk_content_items_current_version'")
    )
    assert fk == [(True, True)]
    triggers = {
        r[0]
        for r in run(
            _all("SELECT tgname FROM pg_trigger WHERE tgrelid = 'content_versions'::regclass AND NOT tgisinternal")
        )
    }
    assert {"trg_auto_lock_content_versions", "trg_immutable_content_versions"} <= triggers
    gate = run(
        _scalar("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'chk_content_publish_gate'")
    )
    assert "VERIFIED" in gate and "current_version_id IS NOT NULL" in gate


def test_policies_match_the_spec():
    def policy(table, name):
        return run(_scalar("SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = :n", t=table, n=name))

    assert "publication_status" in policy("content_items", "hybrid_read_policy")
    assert "system_public_tenant_id()" in policy("content_items", "hybrid_read_policy")
    versions = policy("content_versions", "hybrid_read_policy")
    assert "is_immutable IS TRUE" in versions and "'PUBLISHED'" in versions and "content_items" in versions
    assert "content_versions" in policy("content_assets", "hybrid_read_policy")
    for table in ("publications", "access_grants"):
        assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in policy(table, "tenant_isolation_policy")


def test_privileges():
    def has(role, table, privilege):
        return run(_scalar("SELECT has_table_privilege(:r, :t, :p)", r=role, t=table, p=privilege))

    assert has("ulp_app_rw", "content_versions", "UPDATE") and has("ulp_app_rw", "publications", "INSERT")
    assert not has("ulp_app_rw", "publications", "UPDATE") and not has("ulp_app_rw", "publications", "DELETE")
    for role in ("ulp_exam_session_rw", "ulp_worker_ingestion", "ulp_worker_grading", "ulp_analytics_ro"):
        assert not has(role, "content_items", "SELECT"), role


# --- POST /content ---------------------------------------------------------------------------------


def test_create_starts_fail_closed_with_a_draft_version(client, world):
    body = _create(client, world, summary_md="Tóm tắt $x$")
    item, version = body["item"], body["version"]
    assert item["tenant_id"] == str(world["a"]) and item["current_version_id"] is None
    axes = (item["acquisition_status"], item["verification_status"], item["rights_status"], item["publication_status"])
    assert axes == (
        "NORMALIZED", "UNCHECKED", "PENDING_REVIEW", "DRAFT",
    )  # fmt: skip
    assert version["version_no"] == 1 and version["status"] == "DRAFT" and version["is_immutable"] is False
    assert version["created_by"] == str(world["user_a"]) and version["blocks"] == BLOCKS


def test_create_validations(client, world):
    _create(client, world)
    headers = _headers(world["a"], world["user_a"])
    base = {"content_type": "LESSON", "slug": "bai-1", "title": "x"}
    assert client.post("/api/v1/content", headers=headers, json=base).status_code == 409  # slug taken in the tenant
    assert client.post("/api/v1/content", headers=_headers(world["b"], world["user_b"]), json=base).status_code == 201
    for bad in (
        {**base, "slug": "Bad Slug"},
        {**base, "content_type": "PODCAST"},
        {**base, "rights_status": "LICENSED"},  # a rights review decides that, not the author
        {**base, "slug": "s2", "visibility_scope": "SYSTEM_PUBLIC"},  # system tenant only
        {**base, "slug": "s3", "blocks": [{"block_order": 0, "block_type": "NOPE"}]},
        {**base, "slug": "s4", "unknown": 1},
    ):
        assert client.post("/api/v1/content", headers=headers, json=bad).status_code == 422, bad
    ghost = client.post(
        "/api/v1/content", headers=headers, json={**base, "slug": "s5", "curriculum_node_id": str(uuid.uuid4())}
    )
    assert ghost.status_code == 404


def test_roles_and_tokens(client, world):
    payload = {"content_type": "LESSON", "slug": "x", "title": "x"}
    assert client.post("/api/v1/content", json=payload).status_code == 401
    student = _headers(world["a"], world["user_a"], ("STUDENT",))
    assert client.post("/api/v1/content", headers=student, json=payload).status_code == 403
    spoofed = {**_headers(world["a"], world["user_a"]), "X-Tenant-Id": str(world["b"])}
    assert client.post("/api/v1/content", headers=spoofed, json=payload).status_code == 403
    assert run(_scalar("SELECT count(*) FROM content_items")) == 0


# --- publishing ------------------------------------------------------------------------------------


def test_publish_is_blocked_until_the_item_is_verified_and_cleared(client, world):
    body = _create(client, world)
    version_id = body["version"]["id"]
    blocked = _publish(client, world, version_id)
    assert blocked.status_code == 422, blocked.text
    assert blocked.headers["content-type"].startswith("application/problem+json")
    detail = blocked.json()["detail"]
    assert "VERIFIED" in detail and "rights_status" in detail
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=version_id)) == "DRAFT"
    assert run(_scalar("SELECT publication_status FROM content_items WHERE id = :i", i=body["item"]["id"])) == "DRAFT"
    assert run(_scalar("SELECT count(*) FROM publications")) == 0
    assert run(_scalar("SELECT count(*) FROM outbox_events")) == 0


def test_the_database_enforces_the_gate_by_itself(world):
    async def attempt():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            item = (
                await session.execute(
                    text(
                        "INSERT INTO content_items (tenant_id, content_type, slug, publication_status) "
                        "VALUES (:t, 'LESSON', 'g', 'PUBLISHED') RETURNING id"
                    ),
                    {"t": world["a"]},
                )
            ).scalar_one()
            return item

    with pytest.raises(IntegrityError, match="chk_content_publish_gate"):
        run(attempt())


def test_publish_freezes_the_version_and_records_everything(client, world):
    body = _create(client, world, curriculum_node_id=None)
    item_id, version_id = body["item"]["id"], body["version"]["id"]
    _clear(item_id)
    response = _publish(client, world, version_id)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["version"]["status"] == "PUBLISHED" and out["version"]["is_immutable"] is True  # set by the trigger
    assert out["item"]["publication_status"] == "PUBLISHED" and out["item"]["current_version_id"] == version_id

    publication = run(_all("SELECT entity_type, entity_id, version_id, published_by, tenant_id FROM publications"))
    assert [tuple(map(str, row)) for row in publication] == [
        ("CONTENT", item_id, version_id, str(world["user_a"]), str(world["a"]))
    ]
    (event,) = run(_all("SELECT event_type, aggregate_type, aggregate_id, tenant_id, payload_json FROM outbox_events"))
    assert event.event_type == "content.published" and event.aggregate_type == "content_item"
    assert str(event.aggregate_id) == item_id and event.tenant_id == world["a"]
    assert event.payload_json["version_id"] == version_id and event.payload_json["version_no"] == 1
    assert str(out["event_id"]) in {str(r[0]) for r in run(_all("SELECT event_id FROM outbox_events"))}


def test_a_published_version_cannot_be_published_again(client, world):
    body = _create(client, world)
    _clear(body["item"]["id"])
    assert _publish(client, world, body["version"]["id"]).status_code == 200
    again = _publish(client, world, body["version"]["id"])
    assert again.status_code == 409
    assert run(_scalar("SELECT count(*) FROM publications")) == 1


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"text": "$\\input{secret}$"}, "FORBIDDEN_KATEX_COMMAND"),
        ({"text": "$\\frac{1}{$"}, "UNBALANCED_KATEX_BRACE"),
        ({"image": "asset://00000000-0000-0000-0000-00000000dead"}, "MISSING_ASSET"),
        ({"src": "https://evil.example/x.png"}, "EXTERNAL_MEDIA_URL_FORBIDDEN"),
    ],
)
def test_invalid_blocks_are_not_published(client, world, payload, code):
    body = _create(client, world, blocks=[{"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": payload}])
    _clear(body["item"]["id"])
    response = _publish(client, world, body["version"]["id"])
    assert response.status_code == 422 and code in response.json()["detail"]
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=body["version"]["id"])) == "DRAFT"
    assert run(_scalar("SELECT count(*) FROM outbox_events")) == 0


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


def test_assets_are_checked_and_the_tenants_own_ones_are_linked(client, world):
    own, system_asset, foreign = _asset(world["a"]), _asset(SYSTEM), _asset(world["b"])
    blocks = [
        {"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"image": f"asset://{own}"}},
        {"block_order": 1, "block_type": "TIP_WARNING", "payload_json": {"image": f"asset://{system_asset}"}},
    ]
    good = _create(client, world, slug="with-assets", blocks=blocks)
    _clear(good["item"]["id"])
    published = _publish(client, world, good["version"]["id"])
    assert published.status_code == 200, published.text
    assert published.json()["linked_asset_ids"] == [str(own)]  # the system asset is referenced, not linked
    linked = run(_all("SELECT asset_id FROM content_assets WHERE content_version_id = :v", v=good["version"]["id"]))
    assert [r[0] for r in linked] == [own]

    stolen = _create(
        client, world, slug="stolen",
        blocks=[{"block_order": 0, "block_type": "THEORY_SUMMARY", "payload_json": {"image": f"asset://{foreign}"}}],
    )  # fmt: skip
    _clear(stolen["item"]["id"])
    refused = _publish(client, world, stolen["version"]["id"])
    assert refused.status_code == 422 and "MISSING_ASSET" in refused.json()["detail"]  # not even confirmed to exist
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=stolen["version"]["id"])) == "DRAFT"


# --- immutability (DoD) ----------------------------------------------------------------------------


def _published(client, world, **extra):
    body = _create(client, world, **extra)
    _clear(body["item"]["id"])
    assert _publish(client, world, body["version"]["id"]).status_code == 200
    return body["item"]["id"], body["version"]["id"]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE content_versions SET title = 'hacked' WHERE id = :v",
        "UPDATE content_versions SET blocks_json = '[]' WHERE id = :v",
        "UPDATE content_versions SET is_immutable = FALSE WHERE id = :v",
        "UPDATE content_versions SET status = 'DRAFT' WHERE id = :v",
        "DELETE FROM content_versions WHERE id = :v",
    ],
)
def test_a_published_version_cannot_be_changed_by_the_runtime_role(client, world, sql):
    _, version_id = _published(client, world)

    async def attempt():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text(sql), {"v": version_id})

    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(attempt())
    assert run(_scalar("SELECT title FROM content_versions WHERE id = :v", v=version_id)) == "Bài 1"


def test_not_even_the_owner_can_change_a_published_version(client, world):
    _, version_id = _published(client, world)
    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(_execute("UPDATE content_versions SET title = 'hacked' WHERE id = :v", v=version_id))
    with pytest.raises(DBAPIError, match="IMMUTABLE_VIOLATION"):
        run(
            _execute(
                "DELETE FROM content_items WHERE id = (SELECT content_item_id FROM content_versions WHERE id = :v)",
                v=version_id,
            )
        )


def test_a_published_version_can_still_be_taken_down(client, world):
    _, version_id = _published(client, world)

    async def take_down():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(
                text("UPDATE content_versions SET status = 'TAKEN_DOWN' WHERE id = :v"), {"v": version_id}
            )

    run(take_down())
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=version_id)) == "TAKEN_DOWN"


def test_a_draft_stays_editable(client, world):
    body = _create(client, world)

    async def edit():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(
                text("UPDATE content_versions SET title = 'sửa' WHERE id = :v"), {"v": body["version"]["id"]}
            )

    run(edit())
    assert run(_scalar("SELECT title FROM content_versions WHERE id = :v", v=body["version"]["id"])) == "sửa"


# --- new versions (DoD) ---------------------------------------------------------------------------


def test_changing_published_content_creates_the_next_version_as_a_draft(client, world):
    item_id, v1 = _published(client, world)
    headers = _headers(world["a"], world["user_a"])
    forked = client.post(f"/api/v1/content/{item_id}/versions", headers=headers, json={"title": "Bài 1 (sửa)"})
    assert forked.status_code == 201, forked.text
    v2 = forked.json()
    assert v2["version_no"] == 2 and v2["status"] == "DRAFT" and v2["is_immutable"] is False
    assert v2["title"] == "Bài 1 (sửa)" and v2["blocks"] == BLOCKS  # forked from v1, title overridden
    assert v2["id"] != v1

    v1_row = run(_all("SELECT title, status, is_immutable FROM content_versions WHERE id = :v", v=v1))
    assert [tuple(r) for r in v1_row] == [("Bài 1", "PUBLISHED", True)]  # history untouched
    assert run(_scalar("SELECT current_version_id FROM content_items WHERE id = :i", i=item_id)) == uuid.UUID(v1)
    assert run(_scalar("SELECT publication_status FROM content_items WHERE id = :i", i=item_id)) == "PUBLISHED"

    again = client.post(f"/api/v1/content/{item_id}/versions", headers=headers, json={})
    assert again.status_code == 409 and "DRAFT" in again.json()["detail"]  # one draft at a time

    assert _publish(client, world, v2["id"]).status_code == 200
    assert run(_scalar("SELECT current_version_id FROM content_items WHERE id = :i", i=item_id)) == uuid.UUID(v2["id"])
    statuses = run(_all("SELECT version_no, status, is_immutable FROM content_versions ORDER BY version_no"))
    assert [tuple(r) for r in statuses] == [(1, "PUBLISHED", True), (2, "PUBLISHED", True)]
    assert run(_scalar("SELECT count(*) FROM publications")) == 2


def test_a_draft_item_cannot_fork_and_a_taken_down_one_neither(client, world):
    body = _create(client, world)
    headers = _headers(world["a"], world["user_a"])
    draft = client.post(f"/api/v1/content/{body['item']['id']}/versions", headers=headers, json={})
    assert draft.status_code == 409
    item_id, v1 = _published(client, world, slug="taken")

    async def take_down():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(text("UPDATE content_versions SET status = 'TAKEN_DOWN' WHERE id = :v"), {"v": v1})

    run(take_down())
    forked = client.post(f"/api/v1/content/{item_id}/versions", headers=headers, json={})
    assert forked.status_code == 409 and run(_scalar("SELECT count(*) FROM content_versions")) == 2


def test_the_deferred_foreign_key_allows_an_item_and_its_version_in_one_transaction(world):
    async def both():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            version_id = uuid.uuid4()
            item_id = (
                await session.execute(
                    text(
                        "INSERT INTO content_items (tenant_id, content_type, slug, current_version_id) "
                        "VALUES (:t, 'ARTICLE', 'deferred', :v) RETURNING id"
                    ),
                    {"t": world["a"], "v": version_id},
                )
            ).scalar_one()
            await session.execute(
                text(
                    "INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title) "
                    "VALUES (:v, :t, :i, 1, 't')"
                ),
                {"v": version_id, "t": world["a"], "i": item_id},
            )

    run(both())
    assert run(_scalar("SELECT count(*) FROM content_items WHERE current_version_id IS NOT NULL")) == 1

    async def dangling():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await session.execute(
                text(
                    "INSERT INTO content_items (tenant_id, content_type, slug, current_version_id) "
                    "VALUES (:t, 'ARTICLE', 'd2', :v)"
                ),
                {"t": world["a"], "v": uuid.uuid4()},
            )

    with pytest.raises(IntegrityError, match="fk_content_items_current_version"):
        run(dangling())  # still enforced at commit


# --- tenants ---------------------------------------------------------------------------------------


def test_another_tenant_gets_404_for_items_and_versions_and_changes_nothing(client, world):
    item_id, version_id = _published(client, world)
    stranger = _headers(world["b"], world["user_b"])
    assert client.post(f"/api/v1/content/{item_id}/versions", headers=stranger, json={}).status_code == 404
    draft = _create(client, world, slug="mine-only")
    _clear(draft["item"]["id"])
    assert (
        client.post(f"/api/v1/content-versions/{draft['version']['id']}/publish", headers=stranger).status_code == 404
    )
    assert run(_scalar("SELECT status FROM content_versions WHERE id = :v", v=draft["version"]["id"])) == "DRAFT"
    assert run(_scalar("SELECT count(*) FROM content_versions")) == 2


def test_tenant_b_sees_nothing_of_tenant_a_in_the_database(client, world):
    _published(client, world)

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            counts = [
                (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar()
                for t in ("content_items", "content_versions", "content_assets", "publications")
            ]
            changed = (await session.execute(text("UPDATE content_items SET access_tier = 'TEACHER_PRO'"))).rowcount
            return counts, changed

    assert run(as_b()) == ([0, 0, 0, 0], 0)


def test_system_content_is_public_only_when_published_cleared_and_frozen(world):
    """Hybrid read: tenant A sees the system tenant's PUBLISHED items and frozen versions, nothing else."""

    async def seed():
        async with tenant_transaction(SYSTEM) as session:

            async def item(slug, publication, rights, verification="VERIFIED", scope="SYSTEM_PUBLIC"):
                item_id = uuid.uuid4()
                version_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO content_items (id, tenant_id, content_type, slug, visibility_scope, "
                        "publication_status, rights_status, verification_status, current_version_id) "
                        "VALUES (:i, :t, 'LESSON', :s, :sc, :p, :r, :v, :cv)"
                    ),
                    {
                        "i": item_id, "t": SYSTEM, "s": slug, "sc": scope, "p": publication, "r": rights,
                        "v": verification, "cv": version_id if publication == "PUBLISHED" else None,
                    },
                )  # fmt: skip
                status = "PUBLISHED" if publication == "PUBLISHED" else "DRAFT"
                await session.execute(
                    text(
                        "INSERT INTO content_versions (id, tenant_id, content_item_id, version_no, title, status) "
                        "VALUES (:v, :t, :i, 1, :s, :st)"
                    ),
                    {"v": version_id, "t": SYSTEM, "i": item_id, "s": slug, "st": status},
                )

            await item("public-ok", "PUBLISHED", "OWNED")
            await item("still-draft", "DRAFT", "OWNED")
            await item("taken-down", "TAKEN_DOWN", "TAKEDOWN")
            await item("private-scope", "PUBLISHED", "OWNED", scope="TENANT_PRIVATE")

    run(seed())

    async def as_a():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            items = sorted((await session.execute(text("SELECT slug FROM content_items"))).scalars())
            versions = sorted((await session.execute(text("SELECT title FROM content_versions"))).scalars())
            return items, versions

    items, versions = run(as_a())
    assert items == ["public-ok"] and versions == ["public-ok"]  # versions follow their (visible) parent and freeze


def test_system_published_content_is_read_only_for_tenants(world):
    async def seed():
        async with tenant_transaction(SYSTEM) as session:
            await session.execute(
                text("INSERT INTO content_items (tenant_id, content_type, slug) VALUES (:t, 'LESSON', 'sys')"),
                {"t": SYSTEM},
            )

    run(seed())

    async def as_a():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            return (await session.execute(text("UPDATE content_items SET slug = 'x' WHERE slug = 'sys'"))).rowcount

    assert run(as_a()) == 0
    with pytest.raises(DBAPIError, match="row-level security"):

        async def write_as_system_row():
            async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
                await session.execute(
                    text("INSERT INTO content_items (tenant_id, content_type, slug) VALUES (:t, 'LESSON', 'forged')"),
                    {"t": SYSTEM},
                )

        run(write_as_system_row())
