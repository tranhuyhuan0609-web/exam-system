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


def test_the_author_of_an_assignment_reads_it_even_without_teaching_the_class(client):
    """A teacher who handed out an assignment (e.g. as a substitute) keeps reading what they created."""
    w = _world(client)
    version = client.get(f"/api/v1/assignments/{w['assignment_id']}", headers=w["teacher"]).json()[
        "assessment_version_id"
    ]
    made = client.post(
        "/api/v1/assignments",
        headers=w["outsider_teacher"],
        json={"assessment_version_id": version, "class_id": w["class_id"]},
    )
    assert made.status_code == 201, made.text
    assert client.get(f"/api/v1/assignments/{made.json()['id']}", headers=w["outsider_teacher"]).status_code == 200
    assert client.get(f"/api/v1/assignments/{w['assignment_id']}", headers=w["outsider_teacher"]).status_code == 404
