"""Automatic grading: the outbox consumer grades an attempt once it is submitted or auto-submitted (PostgreSQL)."""

from __future__ import annotations

import uuid

import pytest

from app.contracts.events import EventEnvelopeContract
from app.modules.grading.public import (
    GRADED_ON_EVENTS,
    GRADING_CONSUMER_NAME,
    GradingConflictError,
    grade_closed_attempt,
    register_grading_consumers,
)
from app.modules.outbox.public import ConsumerRegistry, RetryPolicy, relay_once
from app.ops.attempt_expiry import expire_overdue_attempts
from tests.test_assembly_delivery_db import (
    _all,
    _execute,
    _published_paper,
    _seed,
    _start,
    _student,
    client,  # noqa: F401  (fixture)
    run,
    world,  # noqa: F401  (fixture)
)
from tests.test_grading_e2e_db import _grade

NO_WAIT = RetryPolicy(max_attempts=3, base_delay_seconds=0, max_delay_seconds=0)


def _registry() -> ConsumerRegistry:
    registry = ConsumerRegistry()
    register_grading_consumers(registry)
    return registry


def _started(client, world) -> str:  # noqa: F811
    _seed(world)
    paper = _published_paper(client, world)
    return _start(client, world, paper["assessment"]["id"]).json()["attempt_id"]


def _grades(attempt: str):
    return run(
        _all(
            "SELECT revision_no, is_current, grade_reason, graded_by FROM grades WHERE attempt_id = :a ORDER BY 1",
            a=uuid.UUID(attempt),
        )
    )


def _events(attempt: str, event_type: str):
    return run(
        _all(
            "SELECT status FROM outbox_events WHERE aggregate_id = :a AND event_type = :t",
            a=uuid.UUID(attempt),
            t=event_type,
        )
    )


def test_a_submitted_attempt_is_graded_by_the_relay_without_a_teacher(client, world):  # noqa: F811
    attempt = _started(client, world)
    assert client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world)).status_code == 200
    assert _grades(attempt) == [], "nothing graded before the relay runs"

    stats = run(relay_once(registry=_registry(), retry=NO_WAIT))

    assert stats.failed == 0 and stats.handler_errors == 0
    (grade,) = _grades(attempt)
    assert (grade.revision_no, grade.is_current, grade.grade_reason, grade.graded_by) == (1, True, "INITIAL", None)
    assert run(_all("SELECT status FROM attempts WHERE id = :a", a=uuid.UUID(attempt)))[0].status == "GRADED"
    assert [e.status for e in _events(attempt, "attempt.submitted")] == ["PUBLISHED"]
    assert len(_events(attempt, "attempt.graded")) == 1, "the grade is announced for the gradebook and analytics"
    assert run(relay_once(registry=_registry(), retry=NO_WAIT)).claimed == 1, "only the new attempt.graded event"
    assert len(_grades(attempt)) == 1, "a second pass never grades again"


def test_an_attempt_closed_by_the_expiry_sweep_is_graded_from_its_saved_answers(client, world):  # noqa: F811
    attempt = _started(client, world)
    run(_execute("UPDATE attempts SET deadline_at = now() - interval '2 minutes' WHERE id = :i", i=uuid.UUID(attempt)))
    assert run(expire_overdue_attempts()) == 1

    run(relay_once(registry=_registry(), retry=NO_WAIT))

    (grade,) = _grades(attempt)
    assert grade.is_current and grade.graded_by is None
    assert [e.status for e in _events(attempt, "attempt.expired")] == ["PUBLISHED"]


def test_an_attempt_the_teacher_graded_first_is_left_alone(client, world):  # noqa: F811
    attempt = _started(client, world)
    client.post(f"/api/v1/attempts/{attempt}/submit", headers=_student(world))
    assert _grade(client, world, attempt).status_code == 201

    stats = run(relay_once(registry=_registry(), retry=NO_WAIT))

    assert stats.failed == 0 and stats.handler_errors == 0
    (grade,) = _grades(attempt)
    assert grade.revision_no == 1 and grade.graded_by is not None, "the teacher's grade is untouched"
    assert [e.status for e in _events(attempt, "attempt.submitted")] == ["PUBLISHED"]


def test_a_grading_problem_is_not_swallowed(monkeypatch):
    async def no_key(*args, **kwargs):
        raise GradingConflictError("NO_ANSWER_KEY", "the paper version has no current answer key")

    monkeypatch.setattr("app.modules.grading.internal.consumer.grade_attempt", no_key)
    envelope = EventEnvelopeContract(
        event_type="attempt.submitted",
        tenant_id=uuid.uuid4(),
        aggregate_type="attempt",
        aggregate_id=uuid.uuid4(),
        payload={},
    )
    with pytest.raises(GradingConflictError):
        run(grade_closed_attempt(None, envelope))  # type: ignore[arg-type]


def test_registering_twice_leaves_one_consumer_per_event():
    registry = ConsumerRegistry()
    register_grading_consumers(registry)
    register_grading_consumers(registry)
    for event_type in GRADED_ON_EVENTS:
        assert [s.consumer_name for s in registry.subscribers(event_type)] == [GRADING_CONSUMER_NAME]
