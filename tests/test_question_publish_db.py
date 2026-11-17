"""POST /api/v1/questions/{id}/publish: a C01 question goes DRAFT -> reviewed -> PUBLISHED -> into a real paper.

The route that publishes is only the last step. The gate is the A09 one (the same as the database CHECK
`chk_question_publish_gate`): the question must be VERIFIED by a human review decision on its version, its rights
cleared, and the version valid. Publishing freezes the version and its answer, and from then on
`POST /api/v1/assessments/{id}/assemble` may draw the question.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from tests.test_question_bank_api_db import _login, _make_tenant_with_author

SLUG = "c01-school"
MATH_COUNTS = {"SINGLE_CHOICE": 12, "CLUSTER_TRUE_FALSE": 4, "SHORT_NUMERIC": 6}

_BODIES: dict[str, dict[str, Any]] = {
    "SINGLE_CHOICE": {
        "stem_text": "Công thức Bayes cho P(A|B) là gì?",
        "options": [{"key": k, "text": f"Phương án {k}"} for k in "ABCD"],
        "answer_schema": {"correct_option": "B"},
    },
    "CLUSTER_TRUE_FALSE": {
        "stem_text": "Cho P(A) = 0,4 và P(B|A) = 0,5. Xét các mệnh đề sau.",
        "options": [{"key": k, "text": f"Mệnh đề {k}"} for k in "abcd"],
        "answer_schema": {"statements": {"a": True, "b": False, "c": True, "d": False}},
    },
    "SHORT_NUMERIC": {
        "stem_text": "Tính P(A|B) biết P(B|A) = 0,9; P(B|Ā) = 0,1 và P(A) = 0,5.",
        "options": [],
        "answer_schema": {"value": "0,9"},
    },
}


async def _taxonomy() -> uuid.UUID:
    """MATH > BAYES under the system tenant: what a paper's subject and a question's topic point at."""
    async with SessionLocal() as session, session.begin():
        math = (
            await session.execute(
                text(
                    "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path) "
                    "VALUES (:t, 'SUBJECT', 'MATH', 'MATH') RETURNING id"
                ),
                {"t": SYSTEM_PUBLIC_TENANT_ID},
            )
        ).scalar_one()
        return (
            await session.execute(
                text(
                    "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path, parent_id) "
                    "VALUES (:t, 'TOPIC', 'BAYES', 'MATH.BAYES', :p) RETURNING id"
                ),
                {"t": SYSTEM_PUBLIC_TENANT_ID, "p": math},
            )
        ).scalar_one()


def _world(client) -> dict[str, Any]:
    topic = asyncio.run(_taxonomy())
    tenant_id = asyncio.run(_make_tenant_with_author(SLUG))
    teacher = _login(client, f"author@{SLUG}.test", SLUG)  # the bank is run by an author (C01 + A09)
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=uuid.uuid4(),
        roles=("LEARNER",),
        entitlement_tier="FREE",
        issued_at=int(time.time()),
    )
    return {
        "tenant_id": tenant_id,
        "topic": str(topic),
        "teacher": teacher,
        "learner": {"Authorization": f"Bearer {token}"},
        "serial": iter(range(1, 1000)),
    }


def _create(client, w: dict[str, Any], item_type: str = "SINGLE_CHOICE", *, rights: str = "OWNED") -> dict[str, Any]:
    body = {
        "stable_code": f"C01-{next(w['serial']):04d}",
        "item_type": item_type,
        "difficulty_level": "TH",
        "primary_taxonomy_node_id": w["topic"],
        "rights_status": rights,
        **_BODIES[item_type],
    }
    response = client.post("/api/v1/questions", headers=w["teacher"], json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _review(client, w: dict[str, Any], version_id: str, decision: str = "APPROVE", headers: Any = None):
    """Queue the version for review and decide it; returns the decision response."""
    headers = headers or w["teacher"]
    task = client.post(
        "/api/v1/review-tasks",
        headers=headers,
        json={"entity_type": "QUESTION_VERSION", "entity_id": version_id, "queue_name": "c01"},
    )
    assert task.status_code == 201, task.text
    return client.post(
        f"/api/v1/review-tasks/{task.json()['id']}/decisions",
        headers=headers,
        json={"decision": decision, "reason": "Đã rà soát đề và đáp án"},
    )


def _publish(client, w: dict[str, Any], question_id: str, body: dict[str, Any] | None = None, headers: Any = None):
    return client.post(
        f"/api/v1/questions/{question_id}/publish", headers=headers or w["teacher"], json=body if body else None
    )


def _approved_and_published(client, w: dict[str, Any], item_type: str = "SINGLE_CHOICE") -> dict[str, Any]:
    created = _create(client, w, item_type)
    decided = _review(client, w, created["version"]["id"])
    assert decided.status_code == 200, decided.text
    published = _publish(client, w, created["question"]["id"])
    assert published.status_code == 200, published.text
    return published.json()


def _status(client, w: dict[str, Any], question_id: str) -> dict[str, Any]:
    return client.get(f"/api/v1/questions/{question_id}", headers=w["teacher"]).json()["question"]


def test_a_reviewed_question_is_published_frozen_and_drawn_into_a_real_paper(client):
    w = _world(client)
    published: list[dict[str, Any]] = []
    for item_type, count in MATH_COUNTS.items():
        for _ in range(count):
            published.append(_approved_and_published(client, w, item_type))

    # a question published by this route is on the bank screen as PUBLISHED, VERIFIED, with its frozen version
    first = published[0]
    assert first["question"]["publication_status"] == "PUBLISHED"
    assert first["question"]["verification_status"] == "VERIFIED"
    assert first["question"]["current_version_id"] == first["version"]["id"]
    assert first["version"]["is_immutable"] is True and first["version"]["approved_by"] is not None
    listed = client.get(
        "/api/v1/questions", params={"publication_status": "PUBLISHED", "limit": 100}, headers=w["teacher"]
    ).json()
    assert {i["id"] for i in listed["items"]} == {p["question"]["id"] for p in published}

    # two more questions the paper must not draw: verified but never published, and never reviewed
    unpublished = _create(client, w)
    assert _review(client, w, unpublished["version"]["id"]).status_code == 200
    unreviewed = _create(client, w)

    assessment = client.post(
        "/api/v1/assessments",
        headers=w["teacher"],
        json={
            "title": "Đề thi thử Toán THPT 2025",
            "subject_code": "math",
            "grade_band": "GRADE_12",
            "exam_type_code": "THPTQG",
            "rights_status": "OWNED",
        },
    )
    assert assessment.status_code == 201, assessment.text
    assembled = client.post(
        f"/api/v1/assessments/{assessment.json()['id']}/assemble", headers=w["teacher"], json={"random_seed": 7}
    )
    assert assembled.status_code == 200, assembled.text
    drawn = {item["question_version_id"] for item in assembled.json()["items"]}
    mine = {p["version"]["id"] for p in published}
    assert len(drawn) == 22 and drawn == mine, "the paper holds exactly the published C01 questions"
    assert unpublished["version"]["id"] not in drawn and unreviewed["version"]["id"] not in drawn

    paper = client.post(f"/api/v1/assessments/{assessment.json()['id']}/publish", headers=w["teacher"])
    assert paper.status_code == 200, paper.text


def test_the_gate_refuses_a_question_no_human_has_verified_and_names_why(client):
    w = _world(client)
    created = _create(client, w)
    question_id = created["question"]["id"]
    refused = _publish(client, w, question_id)
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["error_code"] == "PUBLISH_GATE_BLOCKED" and body["blocking_codes"] == ["PUBLISH_NOT_VERIFIED"], body
    assert _status(client, w, question_id)["publication_status"] == "DRAFT", "a refused publish changes nothing"

    # a rejection is not a verification either
    rejected = _review(client, w, created["version"]["id"], "REJECT")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["decision"]["resulting_verification_status"] == "REJECTED"
    assert _status(client, w, question_id)["verification_status"] == "REJECTED"
    again = _publish(client, w, question_id)
    assert again.status_code == 422 and again.json()["blocking_codes"] == ["PUBLISH_NOT_VERIFIED"]


def test_verified_is_not_enough_when_the_rights_are_not_cleared(client):
    w = _world(client)
    created = _create(client, w, rights="PENDING_REVIEW")
    assert _review(client, w, created["version"]["id"]).status_code == 200
    question_id = created["question"]["id"]
    assert _status(client, w, question_id)["verification_status"] == "VERIFIED"
    blocked = _publish(client, w, question_id)
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["blocking_codes"] == ["PUBLISH_RIGHTS_BLOCKED"], blocked.json()
    assert _status(client, w, question_id)["publication_status"] == "DRAFT"


def test_a_published_version_is_frozen_has_nothing_to_review_and_cannot_be_published_twice(client):
    w = _world(client)
    done = _approved_and_published(client, w)
    question_id, version_id = done["question"]["id"], done["version"]["id"]

    again = _publish(client, w, question_id)
    assert again.status_code == 409 and again.json()["reason"] == "VERSION_ALREADY_PUBLISHED", again.text
    task = client.post(
        "/api/v1/review-tasks",
        headers=w["teacher"],
        json={"entity_type": "QUESTION_VERSION", "entity_id": version_id, "queue_name": "c01"},
    )
    assert task.status_code == 409 and task.json()["reason"] == "VERSION_ALREADY_PUBLISHED", task.text

    # a published question is changed by a new version, which has to be reviewed and published in turn
    forked = client.post(
        f"/api/v1/questions/{question_id}/versions",
        headers=w["teacher"],
        json={"change_reason": "Sửa số liệu", "stem_text": "Công thức Bayes (đã sửa)?"},
    )
    assert forked.status_code == 201, forked.text
    new_version = forked.json()["id"]
    pending = _publish(client, w, question_id)
    # the question is still VERIFIED from v1, but the new draft has not been reviewed: it does not inherit that
    assert pending.status_code == 422, pending.text
    assert pending.json()["blocking_codes"] == ["PUBLISH_VERSION_NOT_REVIEWED"], pending.json()
    assert _review(client, w, new_version).status_code == 200
    second = _publish(client, w, question_id)
    assert second.status_code == 200, second.text
    assert (
        second.json()["version"]["version_no"] == 2 and second.json()["question"]["current_version_id"] == new_version
    )

    wrong = _publish(client, w, question_id, {"version_id": str(uuid.uuid4())})
    assert wrong.status_code == 404, "a version that is not the question's is not found"


def test_publishing_is_per_tenant_role_gated_and_a_decision_needs_a_reviewable_version(client):
    w = _world(client)
    created = _create(client, w)
    question_id, version_id = created["question"]["id"], created["version"]["id"]
    assert _review(client, w, version_id).status_code == 200

    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    assert _publish(client, w, question_id, headers=other).status_code == 404, "another school cannot publish ours"
    task = client.post(
        "/api/v1/review-tasks",
        headers=other,
        json={"entity_type": "QUESTION_VERSION", "entity_id": version_id, "queue_name": "c01"},
    )
    assert task.status_code == 404, "another school cannot review our version either"
    assert _publish(client, w, str(uuid.uuid4())).status_code == 404

    assert _publish(client, w, question_id, headers=w["learner"]).status_code == 403
    assert client.post(f"/api/v1/questions/{question_id}/publish").status_code == 401
    assert _status(client, w, question_id)["publication_status"] == "DRAFT", "nothing above published it"
    assert _publish(client, w, question_id).status_code == 200

    # a decision on a task whose question was published meanwhile rolls back whole: the task stays open
    late = _create(client, w)
    opened = client.post(
        "/api/v1/review-tasks",
        headers=w["teacher"],
        json={"entity_type": "QUESTION_VERSION", "entity_id": late["version"]["id"], "queue_name": "c01"},
    )
    assert opened.status_code == 201
    asyncio.run(_force_publish(w["tenant_id"], late["question"]["id"], late["version"]["id"]))
    blocked = client.post(
        f"/api/v1/review-tasks/{opened.json()['id']}/decisions",
        headers=w["teacher"],
        json={"decision": "APPROVE", "reason": "Đã rà soát"},
    )
    assert blocked.status_code == 409 and blocked.json()["reason"] == "QUESTION_NOT_REVIEWABLE", blocked.text


async def _force_publish(tenant_id: uuid.UUID, question_id: str, version_id: str) -> None:
    """Publish a question behind the API's back (what a concurrent publisher would do)."""
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("UPDATE questions SET verification_status = 'VERIFIED' WHERE id = :q"), {"q": question_id}
        )
        await session.execute(
            text("UPDATE questions SET publication_status = 'PUBLISHED', current_version_id = :v WHERE id = :q"),
            {"q": question_id, "v": version_id},
        )
