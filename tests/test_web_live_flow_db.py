"""The web screens' core flow on real PostgreSQL rows: seeded UUIDs end to end, no sample ids.

`seed_demo` builds the tenant, class, published paper, class assignment, error notes and learning plan; every step
below is what the pages do with the UUIDs they list: the teacher opens the class, the learner starts the assignment,
autosaves and submits, the teacher grades and reads the gradebook, the learner closes an error note and recalculates
the plan.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.contracts.autosave import compute_payload_hash
from app.ops.seed_demo import DEFAULT_PASSWORD, PAPER_TITLE, seed_demo


def _login(client, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD, "tenant_slug": "h2edu-demo"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _answer(client, headers, attempt_id: str, question: dict) -> None:
    kind = question["item_type"]
    if kind == "SINGLE_CHOICE":
        payload = {"selected_key": question["options"][0]["display_label"]}
    elif kind == "CLUSTER_TRUE_FALSE":
        payload = {"sub_answers": {"a": True, "b": False, "c": True, "d": False}}
    else:
        payload = {"text_value": "0,5"}
    key = f"k-{uuid.uuid4().hex}"
    response = client.put(
        f"/api/v1/attempts/{attempt_id}/responses/{question['question_version_id']}",
        headers={**headers, "Idempotency-Key": key},
        json={
            "client_sequence": 1,
            "expected_response_version": 0,
            "idempotency_key": key,
            "payload_hash": compute_payload_hash(payload),
            "response_payload": payload,
            "is_flagged": False,
        },
    )
    assert response.status_code == 200, response.text


def test_the_listings_show_real_ids_to_each_role_and_only_their_own_classes(client):
    seeded = asyncio.run(seed_demo())
    ids = seeded["entities"]
    teacher, learner = _login(client, "teacher@h2edu.demo"), _login(client, "learner@h2edu.demo")
    tenant_admin = _login(client, "tenant_admin@h2edu.demo")

    for url in ("/api/v1/classes", "/api/v1/assignments", "/api/v1/assessments", "/api/v1/attempts"):
        assert client.get(url).status_code == 401, url

    for headers in (teacher, learner, tenant_admin):  # teaches / belongs to / tenant-wide
        classes = client.get("/api/v1/classes", headers=headers).json()
        assert [c["id"] for c in classes] == [ids["class_id"]]
        assert classes[0]["name"] == "Lớp 12A1" and classes[0]["invite_code"] == "class-12a1"
        assert classes[0]["active_student_count"] == 1

    for headers in (teacher, learner):
        assignments = client.get("/api/v1/assignments", params={"class_id": ids["class_id"]}, headers=headers).json()
        assert len(assignments) == 1
        assert assignments[0]["class_id"] == ids["class_id"]
        assert assignments[0]["assessment_version_id"] == ids["assessment_version_id"]
        assert assignments[0]["assessment_id"] == ids["assessment_id"]
        assert assignments[0]["assessment_title"] == PAPER_TITLE
    # a class the caller does not belong to yields nothing, not an error
    other = client.get("/api/v1/assignments", params={"class_id": str(uuid.uuid4())}, headers=learner)
    assert other.status_code == 200 and other.json() == []

    papers = client.get("/api/v1/assessments", headers=learner).json()
    assert [p["id"] for p in papers] == [ids["assessment_id"]]
    assert papers[0]["current_version_id"] == ids["assessment_version_id"]
    assert client.get("/api/v1/assessments", params={"publication_status": "DRAFT"}, headers=learner).status_code == 403
    assert client.get("/api/v1/assessments", params={"publication_status": "DRAFT"}, headers=teacher).json() == []


def test_the_learner_sits_the_class_assignment_and_the_teacher_reads_the_gradebook(client):
    ids = asyncio.run(seed_demo())["entities"]
    teacher, learner = _login(client, "teacher@h2edu.demo"), _login(client, "learner@h2edu.demo")
    tenant_admin = _login(client, "tenant_admin@h2edu.demo")
    assignment = client.get("/api/v1/assignments", params={"class_id": ids["class_id"]}, headers=learner).json()[0]

    # the gate: only a class member starts, only inside the window, only the version the assignment hands out
    start = {"assessment_id": ids["assessment_id"], "assignment_id": assignment["id"]}
    outsider = client.post("/api/v1/exam-sessions/start", headers=tenant_admin, json=start)
    assert outsider.status_code == 403, outsider.text
    unknown = client.post(
        "/api/v1/exam-sessions/start",
        headers=learner,
        json={"assessment_id": ids["assessment_id"], "assignment_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 404, unknown.text
    closed = client.post(
        "/api/v1/assignments",
        headers=teacher,
        json={
            "assessment_version_id": ids["assessment_version_id"],
            "class_id": ids["class_id"],
            "available_from": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
            "due_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert closed.status_code == 201, closed.text
    late = client.post(
        "/api/v1/exam-sessions/start",
        headers=learner,
        json={"assessment_id": ids["assessment_id"], "assignment_id": closed.json()["id"]},
    )
    assert late.status_code == 403 and late.json()["reason"] == "ASSIGNMENT_PAST_DUE", late.text

    # start, then start again: the same sitting comes back (200), the max_attempts gate never blocks a resume
    started = client.post("/api/v1/exam-sessions/start", headers=learner, json=start)
    assert started.status_code == 201, started.text
    attempt_id = started.json()["attempt_id"]
    resumed = client.post("/api/v1/exam-sessions/start", headers=learner, json=start)
    assert resumed.status_code == 200 and resumed.json()["attempt_id"] == attempt_id and resumed.json()["resumed"]
    # open practice (no assignment) never silently resumes a sitting that belongs to an assignment
    practice = client.post("/api/v1/exam-sessions/start", headers=learner, json={"assessment_id": ids["assessment_id"]})
    assert practice.status_code == 409 and practice.json()["reason"] == "OPEN_ATTEMPT_OTHER_ASSIGNMENT", practice.text
    assert "another assignment" in practice.json()["detail"]
    # but another assignment cannot borrow a sitting that belongs to this one
    borrowed = client.post(
        "/api/v1/exam-sessions/start",
        headers=learner,
        json={"assessment_id": ids["assessment_id"], "assignment_id": closed.json()["id"]},
    )
    assert borrowed.status_code == 409 and borrowed.json()["reason"] == "OPEN_ATTEMPT_OTHER_ASSIGNMENT"

    mine = client.get("/api/v1/attempts", headers=learner).json()
    assert [(a["attempt_id"], a["status"], a["assignment_id"]) for a in mine] == [
        (attempt_id, "IN_PROGRESS", assignment["id"])
    ]
    assert mine[0]["assessment_title"] == PAPER_TITLE
    assert client.get("/api/v1/attempts", headers=teacher).json() == [], "an attempt list is the caller's own"

    paper = client.get(f"/api/v1/attempts/{attempt_id}/paper", headers=learner).json()
    for question in paper["questions"][:5]:
        _answer(client, learner, attempt_id, question)
    submitted = client.post(f"/api/v1/attempts/{attempt_id}/submit", headers=learner)
    assert submitted.status_code == 200 and submitted.json()["status"] == "SUBMITTED", submitted.text
    assert client.get("/api/v1/attempts", params={"status": "SUBMITTED"}, headers=learner).json()[0]["status"] == (
        "SUBMITTED"
    )

    graded = client.post(f"/api/v1/attempts/{attempt_id}/grade", headers=teacher, json={})
    assert graded.status_code == 201, graded.text
    book = client.get(
        f"/api/v1/classes/{ids['class_id']}/gradebook", params={"assignment_id": assignment["id"]}, headers=teacher
    )
    assert book.status_code == 200, book.text
    rows = book.json()["rows"]
    assert len(rows) == 1 and rows[0]["submitted_at"] is not None
    assert float(rows[0]["score"]) >= 0
    exported = client.get(
        f"/api/v1/classes/{ids['class_id']}/gradebook/export",
        params={"assignment_id": assignment["id"]},
        headers=teacher,
    )
    assert exported.status_code == 200 and "text/csv" in exported.headers["content-type"]


def test_the_learner_closes_an_error_note_and_recalculates_the_plan(client):
    asyncio.run(seed_demo())
    learner, tenant_admin = _login(client, "learner@h2edu.demo"), _login(client, "tenant_admin@h2edu.demo")

    notes = client.get("/api/v1/error-notes", headers=learner).json()
    assert len(notes) == 4 and {n["status"] for n in notes} == {"OPEN", "IN_REMEDIATION", "RESOLVED"}
    assert client.get("/api/v1/error-notes", headers=tenant_admin).json() == [], "notes belong to their learner"

    note = next(n for n in notes if n["status"] == "IN_REMEDIATION")
    assert note["consecutive_correct_count"] == 1
    too_early = client.post(f"/api/v1/error-notes/{note['id']}/resolve", headers=learner, json={})
    assert too_early.status_code == 422, "closing needs two correct answers in a row"
    wrong = client.post(f"/api/v1/error-notes/{note['id']}/resolve", headers=learner, json={"is_correct": False})
    assert wrong.json()["consecutive_correct_count"] == 0 and wrong.json()["status"] != "RESOLVED"
    for expected in (1, 2):
        right = client.post(f"/api/v1/error-notes/{note['id']}/resolve", headers=learner, json={"is_correct": True})
        assert right.status_code == 200 and right.json()["consecutive_correct_count"] == expected
    assert right.json()["status"] == "RESOLVED"
    assert client.post(f"/api/v1/error-notes/{note['id']}/resolve", headers=tenant_admin, json={}).status_code == 404

    plans = client.get("/api/v1/learning-plans", headers=learner).json()
    assert len(plans) == 1 and plans[0]["tasks"]
    recalculated = client.post(f"/api/v1/learning-plans/{plans[0]['id']}/recalculate", headers=learner, json={})
    assert recalculated.status_code == 200, recalculated.text
    assert recalculated.json()["id"] == plans[0]["id"]


def test_an_open_practice_sitting_is_not_resumed_under_an_assignment_and_vice_versa(client):
    ids = asyncio.run(seed_demo())["entities"]
    learner = _login(client, "learner@h2edu.demo")
    assignment = client.get("/api/v1/assignments", params={"class_id": ids["class_id"]}, headers=learner).json()[0]
    practice_body = {"assessment_id": ids["assessment_id"]}
    assigned_body = {**practice_body, "assignment_id": assignment["id"]}

    practice = client.post("/api/v1/exam-sessions/start", headers=learner, json=practice_body)
    assert practice.status_code == 201, practice.text
    again = client.post("/api/v1/exam-sessions/start", headers=learner, json=practice_body)
    assert again.status_code == 200 and again.json()["attempt_id"] == practice.json()["attempt_id"]
    refused = client.post("/api/v1/exam-sessions/start", headers=learner, json=assigned_body)
    assert refused.status_code == 409 and refused.json()["reason"] == "OPEN_ATTEMPT_OTHER_ASSIGNMENT"
    assert "open practice" in refused.json()["detail"]

    # once the practice sitting is submitted the assignment can start its own, stored against the assignment
    done = client.post(f"/api/v1/attempts/{practice.json()['attempt_id']}/submit", headers=learner)
    assert done.status_code == 200
    started = client.post("/api/v1/exam-sessions/start", headers=learner, json=assigned_body)
    assert started.status_code == 201 and started.json()["attempt_id"] != practice.json()["attempt_id"]
    mine = {a["attempt_id"]: a["assignment_id"] for a in client.get("/api/v1/attempts", headers=learner).json()}
    assert mine == {practice.json()["attempt_id"]: None, started.json()["attempt_id"]: assignment["id"]}

