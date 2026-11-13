"""G03: transactional outbox, idempotent consumer inbox and the relay (ADR-001, ADR-011, ADR-012).

Everything runs through the runtime roles of app.platform.tenancy. The schema owner (a superuser in
the test database) only creates tenants and the probe table, and reads the privilege catalog.
"""

import asyncio
import csv
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.contracts.events import EventEnvelopeContract
from app.db import SessionLocal, engine
from app.modules.outbox.public import (
    FAILED,
    PENDING,
    PUBLISHED,
    ConsumerRegistry,
    RetryPolicy,
    consume_once,
    enqueue,
    outbox_stats,
    relay_once,
)
from app.platform.models import Tenant
from app.platform.rls import strict_tenant_rls
from app.platform.tenancy import DbRole, SystemJob, set_role, set_system_job, system_job_transaction, tenant_transaction

MANIFEST = Path(__file__).resolve().parent / "security" / "grants_manifest.csv"
TABLES = ("outbox_events", "inbox_events")
NO_WAIT = RetryPolicy(base_delay_seconds=0, max_delay_seconds=0)
EVENT_TYPE = "attempt.submitted"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tenants():
    async def create():
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug="outbox-a", name="A"), Tenant(slug="outbox-b", name="B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


@pytest.fixture
def effects():
    """A tenant table standing in for a consumer's side effect (and for a producer's business row)."""

    async def create():
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS outbox_probe_effects"))
            await conn.execute(
                text(
                    "CREATE TABLE outbox_probe_effects (id UUID PRIMARY KEY DEFAULT uuid_generate_v7(), "
                    "tenant_id UUID NOT NULL REFERENCES tenants(id), event_id UUID, note TEXT NOT NULL)"
                )
            )
            for statement in strict_tenant_rls("outbox_probe_effects"):
                await conn.execute(text(statement))
            await conn.execute(
                text(
                    "GRANT SELECT, INSERT ON outbox_probe_effects "
                    "TO ulp_app_rw, ulp_worker_ingestion, ulp_worker_grading"
                )
            )

    async def drop():
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS outbox_probe_effects"))

    run(create())
    yield
    run(drop())


def envelope(tenant_id: uuid.UUID, **overrides) -> EventEnvelopeContract:
    fields = {
        "event_type": EVENT_TYPE,
        "tenant_id": tenant_id,
        "aggregate_type": "attempt",
        "aggregate_id": uuid.uuid4(),
        "correlation_id": "req-1",
        "payload": {"attempt_id": "x", "score": 7.5},
    }
    return EventEnvelopeContract(**(fields | overrides))


async def publish(tenant_id: uuid.UUID, count: int = 1, role: DbRole = DbRole.APP_RW) -> list[EventEnvelopeContract]:
    events = [envelope(tenant_id) for _ in range(count)]
    async with tenant_transaction(tenant_id, role=role) as session:
        for event in events:
            await enqueue(session, event)
    return events


async def apply_effect(session, event: EventEnvelopeContract, note: str) -> None:
    await session.execute(
        text("INSERT INTO outbox_probe_effects (tenant_id, event_id, note) VALUES (:t, :e, :n)"),
        {"t": event.tenant_id, "e": event.event_id, "n": note},
    )


async def effect_notes(tenant_id: uuid.UUID) -> list[str]:
    async with tenant_transaction(tenant_id, role=DbRole.WORKER_INGESTION) as session:
        return sorted((await session.execute(text("SELECT note FROM outbox_probe_effects"))).scalars().all())


async def outbox_rows(job: SystemJob | None = SystemJob.OUTBOX_RELAY) -> dict[uuid.UUID, tuple]:
    """The outbox as the relay sees it (every tenant), or as the scheduler sees it under `job`."""
    async with SessionLocal() as session, session.begin():
        await set_role(session, DbRole.SYSTEM_SCHEDULER)
        if job is not None:
            await set_system_job(session, job)
        rows = await session.execute(text("SELECT event_id, tenant_id, status, published_at FROM outbox_events"))
        return {r.event_id: (r.tenant_id, r.status, r.published_at) for r in rows}


# --- contract ---------------------------------------------------------------------------------


def test_envelope_rejects_unknown_fields_and_a_missing_tenant():
    tenant_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        envelope(tenant_id, priority="high")
    fields = envelope(tenant_id).model_dump()
    del fields["tenant_id"]
    with pytest.raises(ValidationError, match="tenant_id"):
        EventEnvelopeContract(**fields)


def test_envelope_is_frozen_and_gets_a_uuid_v7_and_an_aware_time():
    first, second = envelope(uuid.uuid4()), envelope(uuid.uuid4())
    assert first.event_id.version == 7 and first.event_id.variant == uuid.RFC_4122
    assert first.event_id != second.event_id
    assert first.occurred_at.tzinfo is not None
    assert first.schema_version == 1
    with pytest.raises(ValidationError):
        first.event_type = "attempt.graded"
    with pytest.raises(ValidationError, match="event_type"):
        envelope(uuid.uuid4(), event_type="Attempt Submitted")


# --- producer ---------------------------------------------------------------------------------


def test_enqueue_commits_and_rolls_back_with_the_business_change(tenants, effects):
    tenant_a, _ = tenants
    kept, lost = envelope(tenant_a), envelope(tenant_a)

    async def scenario():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await apply_effect(session, kept, "kept business row")
            await enqueue(session, kept)
        with pytest.raises(RuntimeError):
            async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
                await apply_effect(session, lost, "lost business row")
                await enqueue(session, lost)
                raise RuntimeError("the use case fails after writing")
        return await outbox_rows(), await effect_notes(tenant_a)

    rows, notes = run(scenario())
    assert set(rows) == {kept.event_id}
    assert rows[kept.event_id][1:] == (PENDING, None)
    assert notes == ["kept business row"]


def test_enqueue_cannot_publish_for_another_tenant(tenants):
    tenant_a, tenant_b = tenants

    async def scenario():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await enqueue(session, envelope(tenant_b))

    with pytest.raises(DBAPIError, match="row-level security"):
        run(scenario())


@pytest.mark.parametrize("role", [DbRole.EXAM_SESSION_RW, DbRole.WORKER_GRADING, DbRole.WORKER_INGESTION])
def test_insert_only_roles_can_enqueue(tenants, role):
    tenant_a, _ = tenants
    events = run(publish(tenant_a, role=role))
    assert set(run(outbox_rows())) == {events[0].event_id}


# --- consumer inbox ---------------------------------------------------------------------------


def test_the_same_event_delivered_five_times_has_one_side_effect(tenants, effects):
    tenant_a, _ = tenants
    event = envelope(tenant_a)

    async def deliver(note: str) -> bool:
        async with tenant_transaction(tenant_a, role=DbRole.WORKER_INGESTION) as session:
            first = await consume_once(session, "probe.consumer", event.event_id, tenant_a)
            if first:
                await apply_effect(session, event, note)
            return first

    async def sequential():
        return [await deliver("sequential") for _ in range(5)]

    async def concurrent():
        return await asyncio.gather(*(deliver("concurrent") for _ in range(5)))

    assert run(sequential()) == [True, False, False, False, False]
    assert run(effect_notes(tenant_a)) == ["sequential"]
    assert run(concurrent()) == [False] * 5, "already consumed, whatever the interleaving"
    assert run(effect_notes(tenant_a)) == ["sequential"]


def test_concurrent_first_deliveries_still_have_one_side_effect(tenants, effects):
    tenant_a, _ = tenants
    event = envelope(tenant_a)

    async def deliver() -> bool:
        async with tenant_transaction(tenant_a, role=DbRole.WORKER_GRADING) as session:
            first = await consume_once(session, "probe.consumer", event.event_id, tenant_a)
            if first:
                await apply_effect(session, event, "once")
                await asyncio.sleep(0.05)  # hold the inbox row while the others arrive
            return first

    async def scenario():
        return await asyncio.gather(*(deliver() for _ in range(5)))

    assert sorted(run(scenario())) == [False, False, False, False, True]
    assert run(effect_notes(tenant_a)) == ["once"]


def test_a_rolled_back_delivery_releases_the_event(tenants, effects):
    tenant_a, _ = tenants
    event = envelope(tenant_a)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with tenant_transaction(tenant_a, role=DbRole.WORKER_INGESTION) as session:
                assert await consume_once(session, "probe.consumer", event.event_id, tenant_a)
                raise RuntimeError("side effect failed")
        async with tenant_transaction(tenant_a, role=DbRole.WORKER_INGESTION) as session:
            return await consume_once(session, "probe.consumer", event.event_id, tenant_a)

    assert run(scenario()) is True


# --- relay ------------------------------------------------------------------------------------


def test_relay_delivers_to_every_consumer_and_marks_published(tenants, effects):
    tenant_a, tenant_b = tenants
    received: list[EventEnvelopeContract] = []
    registry = ConsumerRegistry()

    async def record(session, event):
        received.append(event)
        await apply_effect(session, event, f"recorded {event.payload['score']}")

    async def grade(session, event):
        assert (await session.execute(text("SELECT current_user, current_app_tenant_id()"))).one() == (
            "ulp_worker_grading",
            event.tenant_id,
        )
        await apply_effect(session, event, "graded")

    registry.subscribe(EVENT_TYPE, "probe.record", record)
    registry.subscribe(EVENT_TYPE, "probe.grade", grade, role=DbRole.WORKER_GRADING)

    async def scenario():
        [event_a] = await publish(tenant_a)
        [event_b] = await publish(tenant_b)
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await enqueue(session, envelope(tenant_a, event_type="content.published"))  # nobody listens
        stats = await relay_once(registry=registry, retry=NO_WAIT)
        again = await relay_once(registry=registry, retry=NO_WAIT)
        return event_a, event_b, stats, again, await outbox_rows()

    event_a, event_b, stats, again, rows = run(scenario())
    assert (stats.claimed, stats.published, stats.failed, stats.deliveries) == (3, 3, 0, 4)
    assert again.claimed == 0
    assert all(status == PUBLISHED and published_at is not None for _, status, published_at in rows.values())
    by_id = {event.event_id: event for event in received}
    assert by_id[event_a.event_id] == event_a, "the consumer receives the envelope the producer wrote"
    assert by_id[event_b.event_id] == event_b
    assert run(effect_notes(tenant_a)) == ["graded", "recorded 7.5"]
    assert run(effect_notes(tenant_b)) == ["graded", "recorded 7.5"]


def test_concurrent_relays_never_deliver_the_same_event_twice(tenants, effects):
    tenant_a, tenant_b = tenants
    calls: list[uuid.UUID] = []
    registry = ConsumerRegistry()

    async def slow(session, event):
        calls.append(event.event_id)
        await asyncio.sleep(0.02)  # keep the batch locked while the other relay claims
        await apply_effect(session, event, "x")

    registry.subscribe(EVENT_TYPE, "probe.slow", slow)

    async def scenario():
        events = await publish(tenant_a, 6) + await publish(tenant_b, 6)
        first, second = await asyncio.gather(
            relay_once(5, registry=registry, retry=NO_WAIT), relay_once(5, registry=registry, retry=NO_WAIT)
        )
        rest = await relay_once(5, registry=registry, retry=NO_WAIT)
        return events, (first, second, rest)

    events, runs = run(scenario())
    assert [r.claimed for r in runs] == [5, 5, 2]
    assert sum(r.duplicates for r in runs) == 0
    assert sorted(calls) == sorted(e.event_id for e in events), "every event handled exactly once"


def test_a_failing_consumer_is_retried_then_failed_without_blocking_the_others(tenants, effects):
    tenant_a, _ = tenants
    registry = ConsumerRegistry()
    attempts = {"broken": 0, "flaky": 0}

    async def healthy(session, event):
        await apply_effect(session, event, "healthy")

    async def broken(session, event):
        attempts["broken"] += 1
        await apply_effect(session, event, "broken (rolled back)")
        raise RuntimeError("downstream unavailable")

    async def flaky(session, event):
        attempts["flaky"] += 1
        if attempts["flaky"] < 3:
            raise RuntimeError("transient")
        await apply_effect(session, event, "flaky")

    registry.subscribe(EVENT_TYPE, "probe.broken", broken)
    registry.subscribe(EVENT_TYPE, "probe.healthy", healthy)
    registry.subscribe(EVENT_TYPE, "probe.flaky", flaky, role=DbRole.WORKER_GRADING)

    async def scenario():
        [event] = await publish(tenant_a)
        [other] = await publish(tenant_a)
        stats = await relay_once(1, registry=registry, retry=NO_WAIT)
        broken_attempts = attempts["broken"]
        later = await relay_once(registry=registry, retry=NO_WAIT)  # the next event is not held back
        return event, other, stats, broken_attempts, later, await outbox_rows()

    event, other, stats, broken_attempts, later, rows = run(scenario())
    assert broken_attempts == 5, "at most 5 attempts (ADR-012)"
    assert (stats.claimed, stats.published, stats.failed, stats.failed_event_ids) == (1, 0, 1, [event.event_id])
    assert stats.handler_errors == 5 + 2 and stats.deliveries == 2
    assert rows[event.event_id][1:] == (FAILED, None)
    assert rows[other.event_id][1] == FAILED and later.deliveries == 2  # broken fails it too, others delivered
    assert run(effect_notes(tenant_a)) == ["flaky", "flaky", "healthy", "healthy"]

    # Replay after the fix (what the A01 dead-letter replay will do): only the missing effect is applied.
    async def replay():
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            await session.execute(
                text("UPDATE outbox_events SET status = 'PENDING' WHERE event_id = :e"), {"e": event.event_id}
            )
        fixed = ConsumerRegistry()
        fixed.subscribe(EVENT_TYPE, "probe.broken", healthy)
        fixed.subscribe(EVENT_TYPE, "probe.healthy", healthy)
        fixed.subscribe(EVENT_TYPE, "probe.flaky", flaky, role=DbRole.WORKER_GRADING)
        return await relay_once(registry=fixed, retry=NO_WAIT), await outbox_rows()

    stats, rows = run(replay())
    assert (stats.claimed, stats.published, stats.deliveries, stats.duplicates) == (1, 1, 1, 2)
    assert rows[event.event_id][1] == PUBLISHED
    assert run(effect_notes(tenant_a)) == ["flaky", "flaky", "healthy", "healthy", "healthy"]


def test_retry_backoff_grows_exponentially_with_jitter_and_a_cap():
    policy = RetryPolicy(base_delay_seconds=1, max_delay_seconds=8)
    for failed, step in ((1, 1), (2, 2), (3, 4), (4, 8), (5, 8)):
        delays = {policy.delay(failed) for _ in range(20)}
        assert all(step / 2 <= d <= step for d in delays)
        assert len(delays) > 1, "jitter"


def test_only_inbox_writers_can_be_subscribed():
    registry = ConsumerRegistry()

    async def handler(session, event):
        return None

    with pytest.raises(ValueError, match="may not write inbox_events"):
        registry.subscribe(EVENT_TYPE, "api.consumer", handler, role=DbRole.APP_RW)
    with pytest.raises(ValueError, match="consumer_name"):
        registry.subscribe(EVENT_TYPE, "x" * 81, handler)
    registry.subscribe(EVENT_TYPE, "probe.once", handler)
    with pytest.raises(ValueError, match="already consumes"):
        registry.subscribe(EVENT_TYPE, "probe.once", handler)


def test_outbox_stats_count_by_status_within_the_callers_scope(tenants):
    tenant_a, tenant_b = tenants

    async def scenario():
        await publish(tenant_a, 2)
        await publish(tenant_b, 1)
        await relay_once(1, registry=ConsumerRegistry(), retry=NO_WAIT)
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            everyone = await outbox_stats(session)
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            only_b = await outbox_stats(session)
        return everyone, only_b

    everyone, only_b = run(scenario())
    assert (everyone.pending, everyone.published, everyone.failed) == (2, 1, 0)
    assert everyone.oldest_pending_age_seconds is not None and everyone.oldest_pending_age_seconds >= 0
    assert only_b.pending + only_b.published == 1 and only_b.failed == 0


# --- row-level security and grants ------------------------------------------------------------


def test_outbox_rows_are_confined_to_their_tenant_and_to_the_relay_job(tenants):
    tenant_a, tenant_b = tenants

    async def scenario():
        [event_a] = await publish(tenant_a)
        await publish(tenant_b)
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            seen_by_b = set((await session.execute(text("SELECT event_id FROM outbox_events"))).scalars())
            changed = (
                await session.execute(
                    text("UPDATE outbox_events SET status = 'FAILED' WHERE event_id = :e"), {"e": event_a.event_id}
                )
            ).rowcount
            deleted = (
                await session.execute(text("DELETE FROM outbox_events WHERE event_id = :e"), {"e": event_a.event_id})
            ).rowcount
        return (
            event_a,
            seen_by_b,
            changed,
            deleted,
            await outbox_rows(job=None),
            await outbox_rows(),
            await outbox_rows(job=SystemJob.METRIC_ROLLUP),
        )

    event_a, seen_by_b, changed, deleted, no_job, relay, other_job = run(scenario())
    assert event_a.event_id not in seen_by_b and len(seen_by_b) == 1
    assert (changed, deleted) == (0, 0)
    assert no_job == {}, "the scheduler sees nothing without naming its job"
    assert {tenant for tenant, _, _ in relay.values()} == {tenant_a, tenant_b}
    assert relay[event_a.event_id][1] == PENDING
    assert other_job == {}, "another job's name opens nothing here"


def test_the_inbox_is_confined_to_its_tenant(tenants):
    tenant_a, tenant_b = tenants

    async def count(tenant_id):
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            return (await session.execute(text("SELECT count(*) FROM inbox_events"))).scalar()

    async def consume(tenant_id, claimed_tenant_id):
        async with tenant_transaction(tenant_id, role=DbRole.WORKER_INGESTION) as session:
            return await consume_once(session, "probe.consumer", uuid.uuid4(), claimed_tenant_id)

    assert run(consume(tenant_a, tenant_a)) is True
    assert (run(count(tenant_a)), run(count(tenant_b))) == (1, 0)
    with pytest.raises(DBAPIError, match="row-level security"):
        run(consume(tenant_a, tenant_b))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE outbox_events SET payload_json = '{\"tampered\": true}'",
        "UPDATE outbox_events SET tenant_id = gen_random_uuid()",
        "DELETE FROM outbox_events",
        "INSERT INTO outbox_events (tenant_id, event_type, aggregate_type, aggregate_id, payload_json) "
        "VALUES (gen_random_uuid(), 'a.b', 'x', gen_random_uuid(), '{}')",
    ],
)
def test_the_relay_may_change_only_the_delivery_status(tenants, statement):
    tenant_a, _ = tenants

    async def scenario():
        await publish(tenant_a)
        async with system_job_transaction(SystemJob.OUTBOX_RELAY) as session:
            await session.execute(text(statement))

    with pytest.raises(DBAPIError, match="permission denied"):
        run(scenario())


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        (
            DbRole.APP_RW,
            "INSERT INTO inbox_events (consumer_name, event_id, tenant_id) VALUES ('c', gen_random_uuid(), :t)",
        ),
        (DbRole.APP_RW, "DELETE FROM inbox_events"),
        (DbRole.EXAM_SESSION_RW, "UPDATE outbox_events SET status = 'PUBLISHED'"),
        (DbRole.EXAM_SESSION_RW, "DELETE FROM outbox_events"),
        (DbRole.EXAM_SESSION_RW, "SELECT 1 FROM inbox_events"),
        (DbRole.WORKER_GRADING, "SELECT 1 FROM outbox_events"),
        (DbRole.WORKER_INGESTION, "UPDATE outbox_events SET status = 'PUBLISHED'"),
        (DbRole.ANALYTICS_RO, "SELECT 1 FROM outbox_events"),
        (DbRole.ANALYTICS_RO, "SELECT 1 FROM inbox_events"),
    ],
)
def test_roles_without_the_privilege_are_refused(tenants, role, statement):
    tenant_a, _ = tenants

    async def scenario():
        async with tenant_transaction(tenant_a, role=role) as session:
            await session.execute(text(statement), {"t": tenant_a} if ":t" in statement else {})

    with pytest.raises(DBAPIError, match="permission denied"):
        run(scenario())


def test_granted_privileges_equal_the_manifest():
    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        expected = {
            (r["role"], r["object"], r["privilege"], r["column"])
            for r in csv.DictReader(handle)
            if r["object"] in TABLES
        }
    # ulp_app_rw is granted ALL TABLES in the spec, hence absent from the manifest; inbox writes revoked.
    expected |= {("ulp_app_rw", "outbox_events", p, "") for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
    expected.add(("ulp_app_rw", "inbox_events", "SELECT", ""))

    async def granted():
        async with SessionLocal() as session:
            rows = await session.execute(
                text(
                    """
                    SELECT grantee, table_name, privilege_type, '' FROM information_schema.role_table_grants
                     WHERE table_schema = 'public' AND table_name = ANY(:tables) AND grantee = ANY(:roles)
                    UNION ALL
                    SELECT cp.grantee, cp.table_name, cp.privilege_type, cp.column_name
                      FROM information_schema.column_privileges cp
                     WHERE cp.table_schema = 'public' AND cp.table_name = ANY(:tables) AND cp.grantee = ANY(:roles)
                       AND NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants t
                                        WHERE t.table_schema = 'public' AND t.grantee = cp.grantee
                                          AND t.table_name = cp.table_name AND t.privilege_type = cp.privilege_type)
                    """
                ),
                {"tables": list(TABLES), "roles": [role.value for role in DbRole]},
            )
            return {tuple(row) for row in rows}

    actual = run(granted())
    assert actual - expected == set(), "granted but not in the manifest"
    assert expected - actual == set(), "in the manifest but not granted"


def test_both_tables_force_row_level_security_with_the_spec_policies():
    async def policies():
        async with SessionLocal() as session:
            forced = (
                await session.execute(
                    text(
                        "SELECT relname FROM pg_class "
                        "WHERE relname = ANY(:t) AND relrowsecurity AND relforcerowsecurity"
                    ),
                    {"t": list(TABLES)},
                )
            ).scalars()
            rows = await session.execute(
                text(
                    "SELECT tablename, policyname, cmd, roles::text[] AS roles "
                    "FROM pg_policies WHERE tablename = ANY(:t)"
                ),
                {"t": list(TABLES)},
            )
            return set(forced), {(r.tablename, r.policyname, r.cmd, tuple(r.roles)) for r in rows}

    forced, found = run(policies())
    assert forced == set(TABLES)
    assert found == {
        ("outbox_events", "tenant_isolation_policy", "ALL", ("public",)),
        ("inbox_events", "tenant_isolation_policy", "ALL", ("public",)),
        ("outbox_events", "sys_outbox_relay", "ALL", ("ulp_system_scheduler",)),
    }
