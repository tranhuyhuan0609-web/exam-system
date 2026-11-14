import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app import model_registry  # noqa: F401  (registers all tables on Base.metadata)
from app.config import get_settings
from app.db import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# Indexes that migrations create with raw SQL or options the legacy models do not declare.
# They are wanted in the database; autogenerate must not propose dropping them.
DB_ONLY_INDEXES = {
    "ix_answer_keys_source_document_id",
    "ix_library_documents_folder_path",
    "ix_library_documents_kind",
    "ix_library_documents_origin_id",
    "ix_library_documents_pending_file",
    "ix_library_documents_title_trgm",
    "ix_library_views_document_id",
}


def include_object(obj, name, type_, reflected, compare_to, partitions: frozenset[str] = frozenset()) -> bool:
    if type_ == "table" and reflected and compare_to is None and name in partitions:
        return False  # a partition (audit_events_2026_11, ...): created by migrations / ensure_monthly_partitions
    if (
        type_ == "index"
        and reflected
        and compare_to is None
        and (name in DB_ONLY_INDEXES or getattr(getattr(obj, "table", None), "name", None) in partitions)
    ):
        return False
    return True


def _partition_names(connection: Connection) -> frozenset[str]:
    rows = connection.exec_driver_sql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relispartition"
    )
    names = frozenset(r[0] for r in rows)
    # The SELECT opened a transaction (autobegin); end it, or Alembic's own transaction would be nested
    # inside it and the migration would never be committed.
    connection.commit()
    return names


def do_run_migrations(connection: Connection) -> None:
    partitions = _partition_names(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=lambda *args: include_object(*args, partitions=partitions),
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
