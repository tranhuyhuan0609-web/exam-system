"""Gate 2b (columns): every table the migrations build has the columns Chapter 4 specifies.

`test_alembic_matches_chapter4.py` audits tables, RLS, grants and triggers. This suite goes one level down: it
applies the verbatim Chapter 4 DDL to a scratch database and compares, table by table, with the migrated
application database:

* columns: name, type (`format_type`), NOT NULL, default expression;
* constraints: PRIMARY KEY, UNIQUE, FOREIGN KEY (target, actions) and CHECK, by definition, not by name;
* indexes, by definition, not by name.

Names are ignored on purpose (an ORM names its constraints), the content is not. A table that deviates on purpose
is listed in `KNOWN_DEVIATIONS` with the reason; the list is a snapshot, a new difference fails the test until
someone decides.
Tables still missing from the migrations are skipped, unless `ULP_REQUIRE_FULL_CHAPTER4=1`.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine import make_url

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DDL = ROOT / "docs" / "architecture" / "chapters" / "04_postgresql_ddl_rls_grants.sql"
SCRATCH_DB = "ulp_ch4_column_parity"

# table -> why the migrated table differs from the verbatim DDL on purpose
KNOWN_DEVIATIONS: dict[str, str] = {
    "assets": "is_immutable_bronze is NOT NULL: generated from the NOT NULL asset_role, as the ORM declares it",
    "content_candidates": "UNIQUE (id, tenant_id), the target of composite tenant foreign keys of its children",
    "notification_deliveries": "Decision #7 (IMPLEMENTATION_PLAN_V11_3.md): own tenant_id, composite FK",
    "notifications": "UNIQUE (id, tenant_id), the target of notification_deliveries' composite tenant foreign key",
    "taxonomy_node_versions": "created_by -> users(id) omitted while legacy users was integer-keyed (B04)",
}

_COLUMNS = """
SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS notnull,
       pg_get_expr(d.adbin, d.adrelid) AS dflt
FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = $1::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attname
"""
_CONSTRAINTS = (
    "SELECT contype::text AS t, pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid = $1::regclass"
)
_INDEXES = r"""
SELECT regexp_replace(pg_get_indexdef(indexrelid), 'INDEX \S+ ON', 'INDEX <n> ON') AS def
FROM pg_index WHERE indrelid = $1::regclass
"""
_TABLES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition ORDER BY c.relname
"""


def run(coro):
    return asyncio.run(coro)


async def _connect(database: str) -> asyncpg.Connection:
    url = make_url(os.environ.get("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "")))
    return await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port or 5432, database=database
    )


async def _snapshot(conn: asyncpg.Connection, table: str) -> dict[str, frozenset]:
    quoted = f'public."{table}"'
    columns = frozenset(tuple(r) for r in await conn.fetch(_COLUMNS, quoted))
    constraints = frozenset((r["t"], _normalise(r["def"])) for r in await conn.fetch(_CONSTRAINTS, quoted))
    indexes = frozenset(_normalise(r["def"]) for r in await conn.fetch(_INDEXES, quoted))
    return {"column": columns, "constraint": constraints, "index": indexes}


def _normalise(definition: str) -> str:
    return re.sub(r"\s+", " ", definition).strip()


async def _compare() -> tuple[set[str], set[str], dict[str, list[str]]]:
    app = await _connect(await _current_database())
    admin = await _connect("postgres")
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await admin.close()
    spec = await _connect(SCRATCH_DB)
    try:
        await spec.execute(DDL.read_text(encoding="utf-8"))
        spec_tables = {r["relname"] for r in await spec.fetch(_TABLES)}
        app_tables = {r["relname"] for r in await app.fetch(_TABLES)} - {"alembic_version"}
        differences: dict[str, list[str]] = {}
        for table in sorted(spec_tables & app_tables):
            expected, actual = await _snapshot(spec, table), await _snapshot(app, table)
            notes = []
            for kind in ("column", "constraint", "index"):
                notes += [f"migrated only {kind}: {item}" for item in sorted(actual[kind] - expected[kind], key=str)]
                notes += [f"Chapter 4 only {kind}: {item}" for item in sorted(expected[kind] - actual[kind], key=str)]
            if notes:
                differences[table] = notes
        return spec_tables, app_tables, differences
    finally:
        await spec.close()
        admin = await _connect("postgres")
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)')
        finally:
            await admin.close()
            await app.close()


async def _current_database() -> str:
    return make_url(os.environ.get("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", ""))).database or "postgres"


@pytest.fixture(scope="module")
def parity() -> tuple[set[str], set[str], dict[str, list[str]]]:
    if not (os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")):
        pytest.skip("needs a migrated PostgreSQL database")
    return run(_compare())


def test_every_migrated_table_has_the_columns_constraints_and_indexes_of_chapter4(parity) -> None:
    _, _, differences = parity
    unexpected = {table: notes for table, notes in differences.items() if table not in KNOWN_DEVIATIONS}
    report = "\n".join(f"[{table}]\n  " + "\n  ".join(notes) for table, notes in unexpected.items())
    assert not unexpected, f"tables that deviate from Chapter 4 without a recorded reason:\n{report}"


def test_known_deviations_are_still_deviations(parity) -> None:
    _, _, differences = parity
    stale = sorted(set(KNOWN_DEVIATIONS) - set(differences))
    assert not stale, f"listed in KNOWN_DEVIATIONS but identical to Chapter 4 now: {stale}"


def test_no_chapter4_table_is_missing_from_the_migrations_when_the_full_schema_is_required(parity) -> None:
    spec_tables, app_tables, _ = parity
    missing = sorted(spec_tables - app_tables)
    if os.environ.get("ULP_REQUIRE_FULL_CHAPTER4") == "1":
        assert not missing, f"Chapter 4 tables without a migration: {missing}"
    elif missing:
        pytest.skip(f"{len(missing)} Chapter 4 tables are still without a migration")
