import asyncio
import os

# Must be set before any app module is imported (settings and engine are created at import time).
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://exam:exam@localhost:5432/exam_test"
)

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app import models  # noqa: E402, F401
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402


async def _ensure_database() -> None:
    url = make_url(os.environ["DATABASE_URL"])
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port or 5432, database="postgres"
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database):
            await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


async def _reset_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _truncate_all() -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session", autouse=True)
def _database():
    asyncio.run(_ensure_database())
    asyncio.run(_reset_schema())


@pytest.fixture(autouse=True)
def _clean_tables():
    asyncio.run(_truncate_all())


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user():
    def _make(email: str, password: str = "password123", role: UserRole = UserRole.STUDENT, name: str = "Test User"):
        async def _create():
            async with SessionLocal() as session:
                session.add(User(email=email, full_name=name, password_hash=hash_password(password), role=role))
                await session.commit()

        asyncio.run(_create())

    return _make
