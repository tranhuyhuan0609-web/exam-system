import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings

_settings = get_settings()

if _settings.app_env == "test":
    # Tests drive the app from several event loops; pooled asyncpg connections cannot cross loops.
    engine = create_async_engine(_settings.database_url, poolclass=NullPool)
elif _settings.db_pgbouncer:
    # PgBouncer (transaction mode) owns the pool. Each transaction may land on a different server
    # connection, so nothing is cached on the client and every prepared statement gets a unique name.
    engine = create_async_engine(
        _settings.database_url,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
        },
    )
else:
    # A small pool is enough for <100 concurrent users; increase it as we scale.
    engine = create_async_engine(_settings.database_url, pool_size=10, max_overflow=10, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
