"""M0.3: the application connects as a login that owns nothing and inherits only the API role."""

import asyncio
import os

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db import SessionLocal
from app.platform.rls import LEGACY_TABLES
from app.platform.runtime_login import ensure_runtime_login
from app.platform.tenancy import DbRole

TEST_LOGIN = "ulp_api_pytest"
TEST_PASSWORD = "p'ss \"quoted\" $1"  # quoting must survive the role DDL


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


def test_the_api_role_reads_and_writes_every_legacy_table():
    for table in sorted(LEGACY_TABLES):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', :t, :p)", t=table, p=privilege)), (
                f"ulp_app_rw lacks {privilege} on {table}"
            )
        assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', :t, 'TRUNCATE')", t=table))


def test_the_api_role_can_use_the_serial_sequences():
    async def sequences_without_usage():
        async with SessionLocal() as session:
            rows = await session.execute(
                text(
                    "SELECT c.oid::regclass::text AS seq FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'S'"
                )
            )
            sequences = [r.seq for r in rows]
            missing = []
            for seq in sequences:
                ok = (
                    await session.execute(text("SELECT has_sequence_privilege('ulp_app_rw', :s, 'USAGE')"), {"s": seq})
                ).scalar()
                if not ok:
                    missing.append(seq)
            return sequences, missing

    sequences, missing = run(sequences_without_usage())
    assert sequences, "legacy tables use serial keys"
    assert not missing


def test_other_runtime_roles_get_nothing_on_legacy_tables():
    for role in DbRole:
        if role is DbRole.APP_RW:
            continue
        for table in ("users", "questions", "answer_keys", "exam_attempts"):
            assert not run(_scalar("SELECT has_table_privilege(:r, :t, 'SELECT')", r=role.value, t=table))


@pytest.fixture
def runtime_login():
    url = os.environ["DATABASE_URL"]
    run(ensure_runtime_login(url, TEST_PASSWORD, login=TEST_LOGIN))
    yield make_url(url)
    run(_drop_login(url))


async def _drop_login(url: str) -> None:
    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username, password=parsed.password, host=parsed.host, port=parsed.port or 5432,
        database=parsed.database,
    )
    try:
        await conn.execute(f"DROP OWNED BY {TEST_LOGIN}")
        await conn.execute(f"DROP ROLE {TEST_LOGIN}")
    finally:
        await conn.close()


def test_runtime_login_is_unprivileged_and_inherits_only_the_api_role(runtime_login):
    row = run(_attributes())
    assert (row["rolcanlogin"], row["rolsuper"], row["rolbypassrls"], row["rolcreaterole"], row["rolcreatedb"]) == (
        True, False, False, False, False,
    )
    memberships = run(_memberships())
    assert memberships == {role.value: (role is DbRole.APP_RW) for role in DbRole}, "inherit ulp_app_rw only"


async def _attributes():
    async with SessionLocal() as session:
        result = await session.execute(text("SELECT * FROM pg_roles WHERE rolname = :n"), {"n": TEST_LOGIN})
        return result.mappings().one()


async def _memberships() -> dict[str, bool]:
    async with SessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT r.rolname, m.inherit_option FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = :n"
            ),
            {"n": TEST_LOGIN},
        )
        return {r.rolname: r.inherit_option for r in rows}


def test_runtime_login_connects_and_is_confined(runtime_login):
    async def scenario():
        conn = await asyncpg.connect(
            user=TEST_LOGIN, password=TEST_PASSWORD, host=runtime_login.host, port=runtime_login.port or 5432,
            database=runtime_login.database,
        )
        try:
            can_read_legacy = await conn.fetchval("SELECT count(*) FROM users")
            outcomes = {}
            for name, sql in {
                "create table": "CREATE TABLE hack (id int)",
                "delete tenants": "DELETE FROM tenants",
                "become migrator": "SET ROLE ulp_migrator",
            }.items():
                try:
                    await conn.execute(sql)
                    outcomes[name] = "allowed"
                except asyncpg.InsufficientPrivilegeError:
                    outcomes[name] = "denied"
            async with conn.transaction():
                await conn.execute("SET LOCAL ROLE ulp_exam_session_rw")
                inside = await conn.fetchval("SELECT current_user")
                try:
                    await conn.fetchval("SELECT count(*) FROM users")
                    exam_role_reads_users = True
                except asyncpg.InsufficientPrivilegeError:
                    exam_role_reads_users = False
            after = await conn.fetchval("SELECT current_user")
            return can_read_legacy, outcomes, inside, exam_role_reads_users, after
        finally:
            await conn.close()

    can_read_legacy, outcomes, inside, exam_role_reads_users, after = run(scenario())
    assert can_read_legacy == 0
    assert outcomes == {"create table": "denied", "delete tenants": "denied", "become migrator": "denied"}
    assert inside == "ulp_exam_session_rw"
    assert exam_role_reads_users is False, "inside the exam-session role the API role's privileges are gone"
    assert after == TEST_LOGIN
