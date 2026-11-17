"""The background worker runs the V11.3 loops always and the V1 loops only as long as they have work."""

import asyncio

import pytest
from sqlalchemy import update

from app import worker
from app.models import AttemptStatus, ExamAttempt
from app.modules.outbox.public import ConsumerRegistry, RelayStats
from app.platform.tenancy import DbRole
from tests.test_attempts import attempt_id_from, exam_id, start, student  # noqa: F401  (fixtures)


def names(loops) -> list[str]:
    return [loop.__name__ for loop in loops]


def test_the_v113_loops_always_run():
    for enabled, backlog in ((True, False), (False, True), (False, False)):
        loops = names(worker.select_loops(legacy_enabled=enabled, backlog=backlog))
        assert loops[:2] == ["v113_expiry_loop", "outbox_relay_loop"]


def test_all_v1_loops_run_when_the_legacy_flag_is_on():
    loops = names(worker.select_loops(legacy_enabled=True, backlog=False))
    assert loops[2:] == [
        "ai_jobs_loop",
        "ai_jobs_loop",
        "auto_submit_loop",
        "essay_grading_loop",
        "job1_taxonomy_healer_loop",
        "job2_double_blind_loop",
    ]


def test_with_the_flag_off_v1_only_drains_its_backlog_and_never_audits_the_bank():
    drain = names(worker.select_loops(legacy_enabled=False, backlog=True))
    assert drain[2:] == ["ai_jobs_loop", "auto_submit_loop", "essay_grading_loop"]
    assert names(worker.select_loops(legacy_enabled=False, backlog=False)) == ["v113_expiry_loop", "outbox_relay_loop"]


def test_expiry_sweeps_in_batches_until_one_comes_back_short(monkeypatch):
    answers, calls = [2, 2, 1], []

    async def fake(batch_size: int) -> int:
        calls.append(batch_size)
        return answers.pop(0)

    monkeypatch.setattr(worker, "expire_overdue_attempts", fake)
    assert asyncio.run(worker.v113_expire_attempts_once(batch=2)) == 5
    assert calls == [2, 2, 2]


def test_the_relay_stays_idle_without_consumers_so_events_are_not_marked_published(monkeypatch):
    async def must_not_run(batch_size: int) -> RelayStats:
        raise AssertionError("the relay must not run with no consumer: it would publish events nobody handled")

    monkeypatch.setattr(worker, "default_registry", ConsumerRegistry())
    monkeypatch.setattr(worker, "run_outbox_relay", must_not_run)
    assert asyncio.run(worker.outbox_relay_once()) == 0


def test_the_relay_runs_once_a_consumer_is_registered(monkeypatch):
    async def handler(session, envelope) -> None:
        return None

    registry = ConsumerRegistry()
    registry.subscribe("attempt.submitted", "grading.grade_attempt", handler, role=DbRole.WORKER_GRADING)

    async def fake(batch_size: int) -> RelayStats:
        return RelayStats(claimed=3, published=3)

    monkeypatch.setattr(worker, "default_registry", registry)
    monkeypatch.setattr(worker, "run_outbox_relay", fake)
    assert asyncio.run(worker.outbox_relay_once()) == 3


def test_legacy_backlog_follows_the_v1_attempts(student, db, exam_id):  # noqa: F811
    assert asyncio.run(worker.legacy_backlog()) is False
    attempt_id = attempt_id_from(start(student, exam_id))
    assert asyncio.run(worker.legacy_backlog()) is True, "an attempt in progress is V1 work left to finish"
    db(
        lambda s: s.execute(
            update(ExamAttempt).where(ExamAttempt.id == attempt_id).values(status=AttemptStatus.SUBMITTED)
        )
    )
    assert asyncio.run(worker.legacy_backlog()) is False


@pytest.mark.parametrize("flag", [True, False])
def test_run_once_does_not_raise_for_either_flag(monkeypatch, flag):
    """`--once` is what a cron or a smoke test runs: one pass of every loop that applies."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "legacy_html_enabled", flag)
    asyncio.run(worker.run(once=True))
