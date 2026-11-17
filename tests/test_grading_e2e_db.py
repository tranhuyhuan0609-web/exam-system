"""C07 grading (TASK-22) and the Track 1 end-to-end run on PostgreSQL.

Questions are authored through C01, cleared (the A09 decision), assembled and published (C04), sat (C05), autosaved
(C06), submitted, graded, released, appealed and regraded after an answer-key erratum (C07).

DoD: THPTQG 2025+ scores are exact (Part I 0.25 per item through the stored A..D permutation; Part II 0.10 / 0.25 /
0.50 / 1.00 for 1..4 right sub-items; Part III with the numeric parse policy, `2,5` = `2.5`, `1.000` refused); a regrade
adds `revision_no + 1` and turns the previous grade `is_current = FALSE`, keeping the whole history.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError

from app.contracts.autosave import compute_payload_hash
from app.modules.questions.public import publish_question_version
from app.platform.tenancy import DbRole, tenant_transaction
from tests.test_assembly_delivery_db import (  # the TASK-20 world: tenants, authors, students, MATH taxonomy
    _all,
    _assemble,
    _create_assessment,
    _execute,
    _headers,
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
from tests.test_assembly_delivery_db import (
    _author as _bank_author,
)

TF_KEY = {"a": True, "b": False, "c": True, "d": False}


def _author(world, who="a"):  # noqa: F811
    """The teacher of the paper: grades, releases, overrides (an author of the bank writes the questions)."""
    return _headers(world[who], world[f"author_{who}"])


# --- Track 1 helpers -----------------------------------------------------------------------------------------------


def _author_question(client, world, item_type: str, n: int) -> dict:  # noqa: F811
    """C01: a teacher writes a question (the right option is always the one whose text says `right`)."""
    body: dict = {"stable_code": f"E2E-{n:03d}", "item_type": item_type, "stem_text": f"Câu {n}",
                  "primary_taxonomy_node_id": str(world["topic"]), "rights_status": "OWNED"}  # fmt: skip
    if item_type == "SINGLE_CHOICE":
        body["options"] = [{"key": k, "text": f"{'right' if k == 'B' else 'wrong'} {k} {n}"} for k in "ABCD"]
        body["answer_schema"] = {"correct_option": "B"}
    elif item_type == "CLUSTER_TRUE_FALSE":
        body["options"] = [{"key": k, "text": f"Mệnh đề {k} {n}"} for k in "abcd"]
        body["answer_schema"] = {"statements": TF_KEY}
    else:
        body["answer_schema"] = {"value": "2,5"}
    response = client.post("/api/v1/questions", headers=_bank_author(world), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _clear_and_publish(world, question: dict) -> None:  # noqa: F811
    """A09: the reviewer verifies the question and its rights; C01 publishes (freezes) version 1."""
    run(_execute("UPDATE questions SET verification_status = 'VERIFIED' WHERE id = :q", q=question["question"]["id"]))

    async def publish():
        async with tenant_transaction(world["a"], role=DbRole.APP_RW) as session:
            await publish_question_version(session, uuid.UUID(question["version"]["id"]), approved_by=world["author_a"])

    run(publish())


def _put_answer(client, world, attempt_id, qv_id, payload, *, seq=1, expected=0, key="a1"):  # noqa: F811
    body = {
        "client_sequence": seq,
        "expected_response_version": expected,
        "idempotency_key": f"k-{uuid.uuid4().hex}",
        "payload_hash": compute_payload_hash(payload),
        "response_payload": payload,
        "is_flagged": False,
    }
    response = client.put(
        f"/api/v1/attempts/{attempt_id}/responses/{qv_id}",
        headers={**_student(world, key), "Idempotency-Key": body["idempotency_key"]},
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _grade(client, world, attempt_id, **body):  # noqa: F811
    return client.post(f"/api/v1/attempts/{attempt_id}/grade", headers=_author(world), json=body)


def _graded_attempt(client, world, *, answer=None):  # noqa: F811
    """A seeded paper, an attempt answered by `answer(paper_question) -> payload | None`, submitted, graded."""
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    questions = client.get(f"/api/v1/attempts/{attempt}/paper", headers=_student(world)).json()["questions"]
    for q in questions:
        payload = answer(q) if answer else None
        if payload is not None:
            _put_answer(client, world, attempt, q["question_version_id"], payload)
    assert client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world)).status_code == 200
    graded = _grade(client, world, attempt)
    assert graded.status_code == 201, graded.text
    return attempt, graded.json()


# --- the Track 1 end-to-end run --------------------------------------------------------------------------------------


def test_track_1_end_to_end_author_assemble_sit_autosave_submit_grade_release_appeal_regrade(client, world):  # noqa: F811
    # C01 + A09: 12 single-choice, 4 true/false clusters, 6 short numeric questions, cleared and published
    authored = [
        _author_question(client, world, item_type, n)
        for n, item_type in enumerate(["SINGLE_CHOICE"] * 12 + ["CLUSTER_TRUE_FALSE"] * 4 + ["SHORT_NUMERIC"] * 6)
    ]
    for question in authored:
        _clear_and_publish(world, question)

    # C04: assemble the THPTQG 2025 Math paper and publish it (key stored apart)
    paper = _create_assessment(client, world)
    assembled = _assemble(client, world, paper["id"], seed=2025)
    assert assembled.status_code == 200, assembled.text
    published = client.post(f"/api/v1/assessments/{paper['id']}/publish", headers=_author(world))
    assert published.status_code == 200, published.text

    # C05: the student starts and reads the paper
    attempt = _start(client, world, paper["id"]).json()["attempt_id"]
    paper_view = client.get(f"/api/v1/attempts/{attempt}/paper", headers=_student(world)).json()
    by_type: dict[str, list[dict]] = {}
    for q in paper_view["questions"]:
        by_type.setdefault(q["item_type"], []).append(q)

    # C06: the student answers through the autosave queue (Dexie contract)
    for i, q in enumerate(by_type["SINGLE_CHOICE"]):  # 10 right, 2 wrong: 2.50
        right = next(o["display_label"] for o in q["options"] if o["content_json"]["text"].startswith("right"))
        wrong = next(o["display_label"] for o in q["options"] if o["display_label"] != right)
        _put_answer(client, world, attempt, q["question_version_id"], {"selected_key": right if i < 10 else wrong})
    flips = ["", "d", "cd", "bcd"]  # 4, 3, 2, 1 right sub-items: 1.00 + 0.50 + 0.25 + 0.10 = 1.85
    for q, flip in zip(by_type["CLUSTER_TRUE_FALSE"], flips, strict=True):
        answers = {k: (not v if k in flip else v) for k, v in TF_KEY.items()}
        _put_answer(client, world, attempt, q["question_version_id"], {"sub_answers": answers})
    numeric = ["2,5", "2.5", " 2,5", "2,50", "3", "1.000"]  # 0.50 each when right: 4 right = 2.00
    numeric_question = {}
    for q, value in zip(by_type["SHORT_NUMERIC"], numeric, strict=True):
        first = _put_answer(client, world, attempt, q["question_version_id"], {"text_value": "9"})
        _put_answer(client, world, attempt, q["question_version_id"], {"text_value": value}, seq=2,
                    expected=first["saved_response_version"])  # fmt: skip
        numeric_question[value] = q["question_version_id"]
    assert client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world)).status_code == 200

    # C07: grade
    graded = _grade(client, world, attempt)
    assert graded.status_code == 201, graded.text
    grade = graded.json()
    assert (grade["revision_no"], grade["is_current"], grade["grade_reason"]) == (1, True, "INITIAL")
    assert Decimal(grade["total_score"]) == Decimal("6.35")
    assert grade["section_breakdown"]["PART_I_MCQ"] == {"earned": "2.50", "max": "3.00", "items": "12"}
    assert grade["section_breakdown"]["PART_II_TF4"] == {"earned": "1.85", "max": "4.00", "items": "4"}
    assert grade["section_breakdown"]["PART_III_SHORT"] == {"earned": "2.00", "max": "3.00", "items": "6"}
    details = {d["question_version_id"]: d for d in grade["items"]}
    # "1.000" is refused as the answer sheet would: five characters do not fit the four boxes
    assert details[numeric_question["1.000"]]["parse_error"] == "MAX_CHARS_EXCEEDED_LIMIT_4"
    assert details[numeric_question["2,50"]]["is_correct"] is True, "comma decimal, trailing zero"
    assert run(_scalar("SELECT status FROM attempts WHERE id = :a", a=attempt)) == "GRADED"
    assert run(_scalar("SELECT count(*) FROM grading_details")) == 22
    assert (
        run(_scalar("SELECT payload_json->>'total_score' FROM outbox_events WHERE event_type LIKE 'attempt.graded'"))
        == "6.35"
    )

    # release, then the student appeals the question answered "3"
    released = client.post(f"/api/v1/grades/{grade['id']}/release", headers=_author(world))
    assert released.status_code == 200 and released.json()["released_at"] is not None
    appeal = client.post(
        f"/api/v1/grades/{grade['id']}/appeals",
        headers=_student(world),
        json={"reason_text": "Đáp án đúng của câu này phải là 3", "question_version_id": numeric_question["3"]},
    )
    assert appeal.status_code == 201, appeal.text
    assert appeal.json()["status"] == "OPEN"

    # errata: the key of that question is corrected (key revision 2), then regraded for the appeal
    version_id = run(_scalar("SELECT assessment_version_id FROM attempts WHERE id = :a", a=attempt))
    key = run(_scalar("SELECT answer_key_payload_json FROM assessment_answer_keys WHERE is_current"))
    key["items"][numeric_question["3"]]["answer_schema"] = {"value": "3"}
    run(_execute("UPDATE assessment_answer_keys SET is_current = FALSE WHERE assessment_version_id = :v", v=version_id))
    run(
        _execute(
            "INSERT INTO assessment_answer_keys (assessment_version_id, key_revision_no, tenant_id, is_current, "
            "answer_key_payload_json, key_checksum_sha256, change_reason, created_by) "
            "VALUES (:v, 2, :t, TRUE, CAST(:p AS jsonb), :c, 'ERRATA: câu trả lời ngắn số 5', :u)",
            v=version_id, t=world["a"], p=json.dumps(key), c="e" * 64, u=world["author_a"],
        )
    )  # fmt: skip
    regraded = _grade(client, world, attempt, grade_reason="APPEAL_REGRADE", appeal_id=appeal.json()["id"])
    assert regraded.status_code == 201, regraded.text
    second = regraded.json()
    assert (second["revision_no"], second["is_current"], second["grade_reason"]) == (2, True, "APPEAL_REGRADE")
    assert Decimal(second["total_score"]) == Decimal("6.85")
    history = run(_all("SELECT revision_no, is_current, total_score FROM grades ORDER BY revision_no"))
    assert [(h.revision_no, h.is_current, h.total_score) for h in history] == [
        (1, False, Decimal("6.35")), (2, True, Decimal("6.85")),
    ]  # fmt: skip
    resolved = run(_all("SELECT status, resolved_by FROM appeals"))[0]
    assert (resolved.status, resolved.resolved_by) == ("APPROVED_REGRADED", world["author_a"])
    assert second["released_at"] is None, "a regrade is released on its own"

    third = _grade(client, world, attempt, grade_reason="MANUAL_OVERRIDE").json()
    assert third["revision_no"] == 3
    assert run(_scalar("SELECT count(*) FROM grades")) == 3
    assert run(_scalar("SELECT count(*) FROM grades WHERE is_current")) == 1
    assert run(_scalar("SELECT count(*) FROM grading_details")) == 66, "every revision keeps its own details"


# --- scoring rules ---------------------------------------------------------------------------------------------------


def test_part_i_is_graded_through_the_stored_option_permutation(client, world):  # noqa: F811
    """The paper shows option texts in a shuffled order; picking the displayed label of the right text is right."""

    def right_label(q):
        if q["item_type"] != "SINGLE_CHOICE":
            return None
        # TASK-20 seeding: canonical B is right, its text is "Phương án B của câu n"
        return {"selected_key": next(o["display_label"] for o in q["options"]
                                     if o["content_json"]["text"].startswith("Phương án B "))}  # fmt: skip

    attempt, grade = _graded_attempt(client, world, answer=right_label)
    assert grade["section_breakdown"]["PART_I_MCQ"]["earned"] == "3.00"
    shown = {d["question_version_id"]: d for d in grade["items"] if d["item_type"] == "SINGLE_CHOICE"}
    assert all(d["canonical_key"] == "B" for d in shown.values())
    assert any(d["displayed_key"] != "B" for d in shown.values()), "at least one question was really permuted"


def test_an_unanswered_paper_scores_zero_and_every_item_is_detailed(client, world):  # noqa: F811
    attempt, grade = _graded_attempt(client, world)
    assert Decimal(grade["total_score"]) == Decimal("0.00")
    assert len(grade["items"]) == 22 and all(not d["is_correct"] for d in grade["items"])
    assert {d["parse_error"] for d in grade["items"] if d["item_type"] == "SHORT_NUMERIC"} == {"EMPTY"}


def test_a_paper_built_on_system_questions_is_graded_from_their_answers(client, world):  # noqa: F811
    _seed(world, tenant="system", visibility="SYSTEM_PUBLIC")
    paper = _published_paper(client, world, who="a")
    entries = run(_scalar("SELECT answer_key_payload_json FROM assessment_answer_keys"))["items"].values()
    assert {e["answer_source"] for e in entries} == {"SYSTEM_QUESTION_VERSION"}
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    questions = client.get(f"/api/v1/attempts/{attempt}/paper", headers=_student(world)).json()["questions"]
    for q in questions:
        if q["item_type"] == "SHORT_NUMERIC":
            _put_answer(client, world, attempt, q["question_version_id"], {"text_value": "2,5"})
    client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world))
    graded = _grade(client, world, attempt)
    assert graded.status_code == 201, graded.text
    assert graded.json()["section_breakdown"]["PART_III_SHORT"]["earned"] == "3.00"


# --- state rules and who may do what -------------------------------------------------------------------------------


def test_grading_follows_the_attempt_and_grade_states(client, world):  # noqa: F811
    _seed(world)
    paper = _published_paper(client, world)
    attempt = _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]
    in_progress = _grade(client, world, attempt)
    assert in_progress.status_code == 409 and in_progress.json()["reason"] == "ATTEMPT_NOT_SUBMITTED"
    client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world))
    early = _grade(client, world, attempt, grade_reason="ERRATA_REGRADE")
    assert early.status_code == 409 and early.json()["reason"] == "NOT_GRADED_YET"
    assert _grade(client, world, attempt).status_code == 201
    twice = _grade(client, world, attempt)
    assert twice.status_code == 409 and twice.json()["reason"] == "ALREADY_GRADED"
    assert _grade(client, world, uuid.uuid4()).status_code == 404
    student = client.post(f"/api/v1/attempts/{attempt}/grade", headers=_student(world), json={})
    assert student.status_code == 403
    other_tenant = client.post(f"/api/v1/attempts/{attempt}/grade", headers=_author(world, "b"), json={})
    assert other_tenant.status_code == 404
    mismatch = _grade(client, world, attempt, grade_reason="MANUAL_OVERRIDE", appeal_id=str(uuid.uuid4()))
    assert mismatch.status_code == 422


def test_release_and_appeal_rules(client, world):  # noqa: F811
    attempt, grade = _graded_attempt(client, world)
    url = f"/api/v1/grades/{grade['id']}"
    early = client.post(f"{url}/appeals", headers=_student(world), json={"reason_text": "Xin phúc khảo bài thi"})
    assert early.status_code == 409 and early.json()["reason"] == "GRADE_NOT_RELEASED"
    assert client.post(f"{url}/release", headers=_student(world)).status_code == 403
    first = client.post(f"{url}/release", headers=_author(world)).json()
    again = client.post(f"{url}/release", headers=_author(world)).json()
    assert first["released_at"] == again["released_at"], "releasing twice changes nothing"
    assert run(_scalar("SELECT count(*) FROM outbox_events WHERE event_type = 'grade.released'")) == 1
    event = run(_scalar("SELECT payload_json FROM outbox_events WHERE event_type = 'grade.released'"))
    assert event["membership_id"] == str(world["member_a1"])
    other = client.post(f"{url}/appeals", headers=_student(world, "a2"), json={"reason_text": "Không phải bài của tôi"})
    assert other.status_code == 404
    off_paper = client.post(
        f"{url}/appeals",
        headers=_student(world),
        json={"reason_text": "Câu này không có trong đề", "question_version_id": str(uuid.uuid4())},
    )
    assert off_paper.status_code == 409 and off_paper.json()["reason"] == "QUESTION_NOT_ON_PAPER"
    assert client.post(f"{url}/appeals", headers=_student(world), json={"reason_text": "ngắn"}).status_code == 422
    filed = client.post(f"{url}/appeals", headers=_student(world), json={"reason_text": "Xin phúc khảo toàn bài"})
    assert filed.status_code == 201
    regraded = _grade(client, world, attempt, grade_reason="APPEAL_REGRADE", appeal_id=filed.json()["id"]).json()
    superseded = client.post(f"{url}/release", headers=_author(world))
    assert superseded.status_code == 409 and superseded.json()["reason"] == "GRADE_NOT_CURRENT"
    stale = client.post(f"{url}/appeals", headers=_student(world), json={"reason_text": "Phúc khảo lần nữa nhé"})
    assert stale.status_code == 409 and stale.json()["reason"] == "GRADE_NOT_CURRENT"
    closed = _grade(client, world, attempt, grade_reason="APPEAL_REGRADE", appeal_id=filed.json()["id"])
    assert closed.status_code == 409 and closed.json()["reason"] == "APPEAL_NOT_OPEN"
    assert regraded["revision_no"] == 2


# --- schema and privileges -------------------------------------------------------------------------------------------


def test_tables_are_tenant_isolated_and_one_grade_is_current_per_attempt(client, world):  # noqa: F811
    for table in ("grades", "grading_details", "rubric_versions", "appeals"):
        forced = run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
        assert forced is True, table
    attempt, grade = _graded_attempt(client, world)
    with pytest.raises(DBAPIError, match="uq_grades_current_per_attempt"):
        run(
            _execute(
                "INSERT INTO grades (tenant_id, attempt_id, revision_no, scoring_scheme_id, total_score, "
                "section_breakdown_json, item_details_json) "
                "SELECT tenant_id, attempt_id, 9, scoring_scheme_id, 0, '{}', '[]' FROM grades WHERE id = :g",
                g=grade["id"],
            )
        )

    async def as_b():
        async with tenant_transaction(world["b"], role=DbRole.APP_RW) as session:
            from sqlalchemy import text

            return (await session.execute(text("SELECT count(*) FROM grades"))).scalar()

    assert run(as_b()) == 0


def test_the_api_role_releases_but_never_changes_a_score(client, world):  # noqa: F811
    attempt, grade = _graded_attempt(client, world)

    async def as_role(role, sql):
        async with tenant_transaction(world["a"], role=role) as session:
            from sqlalchemy import text

            await session.execute(text(sql), {"g": grade["id"]})

    with pytest.raises(DBAPIError) as caught:
        run(as_role(DbRole.APP_RW, "UPDATE grades SET total_score = 10 WHERE id = :g"))
    assert _sqlstate(caught.value) == "42501"
    run(as_role(DbRole.APP_RW, "UPDATE grades SET released_at = now() WHERE id = :g"))
    for role in (DbRole.EXAM_SESSION_RW, DbRole.WORKER_INGESTION, DbRole.ANALYTICS_RO):
        with pytest.raises(DBAPIError) as caught:
            run(as_role(role, "SELECT total_score FROM grades WHERE id = :g"))
        assert _sqlstate(caught.value) == "42501", role
    with pytest.raises(DBAPIError) as caught:
        run(as_role(DbRole.WORKER_GRADING, "SELECT * FROM appeals WHERE grade_id = :g"))
    assert _sqlstate(caught.value) == "42501"


# --- WAVE-8: manual override of a grade ---------------------------------------------------------------------------


def _override(client, world, grade_id, headers=None, **body):  # noqa: F811
    return client.post(f"/api/v1/grades/{grade_id}/override", headers=headers or _author(world), json=body)


def test_a_teacher_overrides_the_total_with_a_reason_and_the_change_is_audited(client, world):  # noqa: F811
    attempt, first = _graded_attempt(client, world)
    done = _override(client, world, first["id"], total_score="7.25", reason="Verified the working on paper, +1 mark")
    assert done.status_code == 201, done.text
    body = done.json()
    assert (body["revision_no"], body["is_current"], body["grade_reason"]) == (2, True, "MANUAL_OVERRIDE")
    assert Decimal(body["total_score"]) == Decimal("7.25") and body["graded_by"] == str(world["author_a"])

    history = run(_all("SELECT revision_no, is_current, total_score FROM grades ORDER BY revision_no"))
    assert [(h.revision_no, h.is_current) for h in history] == [(1, False), (2, True)]
    assert run(_scalar("SELECT count(*) FROM grading_details")) == 2 * run(
        _scalar("SELECT count(*) FROM grading_details WHERE grade_id = :g", g=uuid.UUID(first["id"]))
    ), "the machine's per-item results are copied, not lost"

    audit = run(_all("SELECT action, target_type, reason, before_json, after_json FROM audit_events"))
    assert len(audit) == 1 and audit[0].action == "OVERRIDE_GRADE" and audit[0].target_type == "grades"
    assert audit[0].reason == "Verified the working on paper, +1 mark"
    assert audit[0].before_json["total_score"] == first["total_score"]
    assert audit[0].after_json["total_score"] == "7.25"
    events = run(_all("SELECT payload_json FROM outbox_events WHERE event_type = 'attempt.graded' ORDER BY created_at"))
    assert events[-1].payload_json["total_score"] == "7.25" and events[-1].payload_json["revision_no"] == 2


def test_an_override_needs_a_real_reason_a_possible_score_and_a_current_grade(client, world):  # noqa: F811
    attempt, first = _graded_attempt(client, world)
    assert _override(client, world, first["id"], total_score="7").status_code == 422, "the reason is mandatory"
    assert _override(client, world, first["id"], total_score="7", reason="too short").status_code == 422
    assert _override(client, world, first["id"], total_score="7", reason="   " + "x" * 5 + "   ").status_code == 422
    above = _override(client, world, first["id"], total_score="10.01", reason="more than the paper is worth")
    assert above.status_code == 409 and above.json()["reason"] == "SCORE_OUT_OF_RANGE"
    negative = _override(client, world, first["id"], total_score="-1", reason="a negative score is not possible")
    assert negative.status_code == 422
    unknown = _override(client, world, uuid.uuid4(), total_score="5", reason="there is no such grade here")
    assert unknown.status_code == 404
    assert run(_scalar("SELECT count(*) FROM audit_events")) == 0, "a refused override leaves no trace"

    first_override = _override(client, world, first["id"], total_score="8", reason="first override of the grade")
    assert first_override.status_code == 201
    stale = _override(client, world, first["id"], total_score="9", reason="the first revision is not current")
    assert stale.status_code == 409 and stale.json()["reason"] == "GRADE_NOT_CURRENT"


def test_only_graders_override_and_never_across_tenants(client, world):  # noqa: F811
    attempt, first = _graded_attempt(client, world)
    refused = _override(
        client, world, first["id"], headers=_student(world), total_score="9", reason="a student may not do this"
    )
    assert refused.status_code == 403
    other = _author(world, who="b")
    cross = _override(client, world, first["id"], headers=other, total_score="9", reason="another tenant's teacher")
    assert cross.status_code == 404
    anonymous = client.post(
        f"/api/v1/grades/{first['id']}/override", json={"total_score": "9", "reason": "nobody is signed in here"}
    )
    assert anonymous.status_code == 401
    assert run(_scalar("SELECT count(*) FROM grades")) == 1


def test_override_reason_counts_characters_not_padding(client, world):  # noqa: F811
    attempt, first = _graded_attempt(client, world)
    padded = _override(client, world, first["id"], total_score="7", reason="   " + "x" * 8 + "          ")
    assert padded.status_code == 422, "8 real characters wrapped in blanks are not a reason"
    assert run(_scalar("SELECT count(*) FROM grades")) == 1
