"""Same tenant is not enough: a class and its assignment are read only by the people who teach or sit in the class.

Everyone below belongs to the one tenant `h2edu-demo`; the two outsiders are a learner and a teacher with no link to
the class. They get the 404 of a class that does not exist (the module's convention for a row the caller may not see),
so the ids of other classes are not confirmed either.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.config import get_settings
from app.ops.seed_demo import DEFAULT_PASSWORD, email_blind_index, seed_demo
from app.platform.tenancy import tenant_transaction
from app.security import hash_password

OUTSIDERS = (
    ("outsider_learner@h2edu.demo", "Học sinh lớp khác", ["LEARNER"], "STUDENT_PRO"),
    ("outsider_teacher@h2edu.demo", "Giáo viên lớp khác", ["TEACHER"], "TEACHER_PRO"),
)


async def _add_outsiders(tenant_id: uuid.UUID) -> None:
    settings = get_settings()
    async with tenant_transaction(tenant_id) as session:
        for email, pseudonym, roles, tier in OUTSIDERS:
            blind = email_blind_index(email, settings.secret_key, settings.blind_index_pepper)
            row = (
                await session.execute(
                    text("SELECT user_id FROM f04_register_user(:b, :h, NULL, :p, NULL, 'ACTIVE')"),
                    {"b": blind, "h": hash_password(DEFAULT_PASSWORD), "p": pseudonym},
                )
            ).one()
            await session.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, roles, entitlement_tier) "
                    "VALUES (:t, :u, CAST(:r AS varchar[]), :tier)"
                ),
                {"t": tenant_id, "u": row.user_id, "r": roles, "tier": tier},
            )


def _login(client, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD, "tenant_slug": "h2edu-demo"}
    )
    assert response.status_code == 200, (email, response.text)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _world(client) -> dict:
    seeded = asyncio.run(seed_demo())
    asyncio.run(_add_outsiders(uuid.UUID(seeded["tenant"]["id"])))
    ids = seeded["entities"]
    teacher = _login(client, "teacher@h2edu.demo")
    assignment_id = client.get("/api/v1/assignments", params={"class_id": ids["class_id"]}, headers=teacher).json()[0][
        "id"
    ]
    return {
        "class_id": ids["class_id"],
        "assignment_id": assignment_id,
        "teacher": teacher,
        "learner": _login(client, "learner@h2edu.demo"),
        "tenant_admin": _login(client, "tenant_admin@h2edu.demo"),
        "outsider_learner": _login(client, OUTSIDERS[0][0]),
        "outsider_teacher": _login(client, OUTSIDERS[1][0]),
    }


def test_a_class_is_read_by_its_teacher_its_students_and_tenant_administrators_only(client):
    w = _world(client)
    url = f"/api/v1/classes/{w['class_id']}"
    for who in ("teacher", "learner", "tenant_admin"):
        response = client.get(url, headers=w[who])
        assert response.status_code == 200, (who, response.text)
        assert response.json()["classroom"]["id"] == w["class_id"]
    for who in ("outsider_learner", "outsider_teacher"):
        response = client.get(url, headers=w[who])
        assert response.status_code == 404, (who, response.text)
    # a class that does not exist answers the same, so the two cases cannot be told apart
    missing = client.get(f"/api/v1/classes/{uuid.uuid4()}", headers=w["outsider_learner"])
    assert missing.status_code == 404 and missing.json()["detail"].split(" ", 2)[2] == "not found in tenant"
    assert client.get(url).status_code == 401


def test_an_assignment_is_read_by_its_class_and_not_by_another_class_in_the_tenant(client):
    w = _world(client)
    url = f"/api/v1/assignments/{w['assignment_id']}"
    for who in ("teacher", "learner", "tenant_admin"):
        response = client.get(url, headers=w[who])
        assert response.status_code == 200, (who, response.text)
        assert response.json()["class_id"] == w["class_id"]
    for who in ("outsider_learner", "outsider_teacher"):
        assert client.get(url, headers=w[who]).status_code == 404, who
    assert client.get(f"/api/v1/assignments/{uuid.uuid4()}", headers=w["outsider_learner"]).status_code == 404
    assert client.get(url).status_code == 401


def test_a_student_who_left_the_class_no_longer_reads_it_nor_its_assignment(client):
    w = _world(client)
    detail = client.get(f"/api/v1/classes/{w['class_id']}", headers=w["teacher"]).json()
    learner_membership = next(m for m in detail["members"] if m["role_in_class"] == "STUDENT")
    left = client.delete(
        f"/api/v1/classes/{w['class_id']}/members/{learner_membership['membership_id']}", headers=w["teacher"]
    )
    assert left.status_code == 200, left.text
    assert client.get(f"/api/v1/classes/{w['class_id']}", headers=w["learner"]).status_code == 404
    assert client.get(f"/api/v1/assignments/{w['assignment_id']}", headers=w["learner"]).status_code == 404
    # the teacher still sees the roster with the left member (history is kept)
    kept = client.get(f"/api/v1/classes/{w['class_id']}", headers=w["teacher"]).json()["members"]
    assert any(m["status"] == "INACTIVE" for m in kept)


async def _membership_id(tenant_id: uuid.UUID, email: str) -> str:
    """The membership of a demo user (found by the pseudonym the outsiders are registered with)."""
    pseudonym = next(p for e, p, _, _ in OUTSIDERS if e == email)
    async with tenant_transaction(tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT m.id FROM memberships m JOIN users u ON u.id = m.user_id "
                    "WHERE m.tenant_id = :t AND u.pseudonym_handle = :p"
                ),
                {"t": tenant_id, "p": pseudonym},
            )
        ).one()
    return str(row.id)


def _tenant_id(client, w: dict) -> uuid.UUID:
    return uuid.UUID(
        client.get(f"/api/v1/classes/{w['class_id']}", headers=w["teacher"]).json()["classroom"]["tenant_id"]
    )


def test_a_teacher_hands_out_work_only_to_the_classes_they_run(client):
    """POST /assignments: same tenant and a teacher role are not enough. A class they do not run does not exist."""
    w = _world(client)
    version = client.get(f"/api/v1/assignments/{w['assignment_id']}", headers=w["teacher"]).json()[
        "assessment_version_id"
    ]
    body = {"assessment_version_id": version, "class_id": w["class_id"]}
    refused = client.post("/api/v1/assignments", headers=w["outsider_teacher"], json=body)
    assert refused.status_code == 404, refused.text
    assert refused.json()["detail"] == f"class {w['class_id']} not found in tenant"
    # nothing was written: the class still has its one seeded assignment
    listed = client.get("/api/v1/assignments", params={"class_id": w["class_id"]}, headers=w["teacher"]).json()
    assert [a["id"] for a in listed] == [w["assignment_id"]]
    # a group the teacher has no class in is refused the same way (the id of the group is not confirmed)
    group = client.post(
        "/api/v1/assignments",
        headers=w["outsider_teacher"],
        json={**body, "class_id": None, "group_id": str(uuid.uuid4())},
    )
    assert group.status_code == 404, group.text
    # the teacher of the class and a tenant administrator both can
    for who in ("teacher", "tenant_admin"):
        made = client.post("/api/v1/assignments", headers=w[who], json=body)
        assert made.status_code == 201, (who, made.text)
    # a learner is refused by role, before the class is looked at
    assert client.post("/api/v1/assignments", headers=w["learner"], json=body).status_code == 403
    assert client.post("/api/v1/assignments", json=body).status_code == 401


def test_a_teacher_changes_only_the_assignments_of_classes_they_run(client):
    w = _world(client)
    url = f"/api/v1/assignments/{w['assignment_id']}"
    patch = {"max_attempts": 2}
    for who in ("outsider_teacher",):
        denied = client.patch(url, headers=w[who], json=patch)
        assert denied.status_code == 404, (who, denied.text)
    assert client.get(url, headers=w["teacher"]).json()["max_attempts"] != 2, "the refused patch changed nothing"
    for who in ("teacher", "tenant_admin"):
        done = client.patch(url, headers=w[who], json=patch)
        assert done.status_code == 200 and done.json()["max_attempts"] == 2, (who, done.text)
    assert client.patch(url, headers=w["learner"], json=patch).status_code == 403
    assert (
        client.patch(f"/api/v1/assignments/{uuid.uuid4()}", headers=w["outsider_teacher"], json=patch).status_code
        == 404
    )


def test_the_gradebook_of_a_class_is_read_by_who_runs_it(client):
    """Gradebook, item analysis and CSV export: a teacher of another class gets the 404 of a missing class."""
    w = _world(client)
    base = f"/api/v1/classes/{w['class_id']}"
    query = {"assignment_id": w["assignment_id"]}
    for path in ("gradebook", "item-analysis", "gradebook/export"):
        for who in ("teacher", "tenant_admin"):
            allowed = client.get(f"{base}/{path}", params=query, headers=w[who])
            assert allowed.status_code == 200, (path, who, allowed.text)
        denied = client.get(f"{base}/{path}", params=query, headers=w["outsider_teacher"])
        assert denied.status_code == 404, (path, denied.text)
        assert denied.json()["detail"] == f"class {w['class_id']} not found in tenant"
        assert client.get(f"{base}/{path}", params=query, headers=w["learner"]).status_code == 403, path
        assert client.get(f"{base}/{path}", params=query).status_code == 401, path
    # a class that does not exist answers the same as a class the teacher does not run
    missing = client.get(f"/api/v1/classes/{uuid.uuid4()}/gradebook", params=query, headers=w["outsider_teacher"])
    assert missing.status_code == 404


def test_a_teaching_assistant_of_the_class_runs_it_with_the_teacher(client):
    """Being enrolled as TEACHING_ASSISTANT gives the same class access; being enrolled as STUDENT does not."""
    w = _world(client)
    tenant_id = _tenant_id(client, w)
    membership = asyncio.run(_membership_id(tenant_id, OUTSIDERS[1][0]))
    query = {"assignment_id": w["assignment_id"]}
    url = f"/api/v1/classes/{w['class_id']}/gradebook"
    version = client.get(f"/api/v1/assignments/{w['assignment_id']}", headers=w["teacher"]).json()[
        "assessment_version_id"
    ]
    body = {"assessment_version_id": version, "class_id": w["class_id"]}

    student = client.post(
        f"/api/v1/classes/{w['class_id']}/members",
        headers=w["teacher"],
        json={"membership_id": membership, "role_in_class": "STUDENT"},
    )
    assert student.status_code == 201, student.text
    assert client.get(url, params=query, headers=w["outsider_teacher"]).status_code == 404, "a student does not run it"
    assert client.post("/api/v1/assignments", headers=w["outsider_teacher"], json=body).status_code == 404

    assistant = client.post(
        f"/api/v1/classes/{w['class_id']}/members",
        headers=w["teacher"],
        json={"membership_id": membership, "role_in_class": "TEACHING_ASSISTANT"},
    )
    assert assistant.status_code in (200, 201), assistant.text
    assert client.get(url, params=query, headers=w["outsider_teacher"]).status_code == 200
    made = client.post("/api/v1/assignments", headers=w["outsider_teacher"], json=body)
    assert made.status_code == 201, made.text
    authored = f"/api/v1/assignments/{made.json()['id']}"

    left = client.delete(f"/api/v1/classes/{w['class_id']}/members/{membership}", headers=w["teacher"])
    assert left.status_code == 200, left.text
    assert client.get(url, params=query, headers=w["outsider_teacher"]).status_code == 404, "a left assistant is out"
    # what they wrote stays readable to them, but they no longer steer it
    assert client.get(authored, headers=w["outsider_teacher"]).status_code == 200
    assert client.patch(authored, headers=w["outsider_teacher"], json={"max_attempts": 3}).status_code == 404
