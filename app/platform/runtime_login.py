"""Creates the runtime database login, next to the migrations: `python -m app.platform.runtime_login`.

Run it with the owner's DATABASE_URL (the one migrations use). The application itself then connects
as `ulp_api`, which owns nothing:

- it inherits `ulp_app_rw`, the API role;
- it may switch to the other runtime roles for one transaction with SET LOCAL ROLE
  (see app.platform.tenancy), and holds none of their privileges otherwise.

The password comes from RUNTIME_DB_PASSWORD, so it never lands in a migration.
"""

import asyncio
import sys

import asyncpg
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.platform.tenancy import DbRole

RUNTIME_LOGIN = "ulp_api"


async def ensure_runtime_login(database_url: str, password: str, login: str = RUNTIME_LOGIN) -> None:
    url = make_url(database_url)
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port or 5432, database=url.database
    )
    try:
        async with conn.transaction():
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", login)
            verb = "ALTER" if exists else "CREATE"
            # Role DDL takes no bind parameters; format('%L') quotes the password as a literal.
            statement = await conn.fetchval(
                "SELECT format('%s ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', "
                "$1::text, $2::text, $3::text)",
                verb,
                login,
                password,
            )
            await conn.execute(statement)
            await conn.execute(f"GRANT CONNECT ON DATABASE {_quote(url.database)} TO {_quote(login)}")
            await conn.execute(f"GRANT {DbRole.APP_RW.value} TO {_quote(login)} WITH INHERIT TRUE, SET TRUE")
            for role in DbRole:
                if role is not DbRole.APP_RW:
                    await conn.execute(f"GRANT {role.value} TO {_quote(login)} WITH INHERIT FALSE, SET TRUE")
    finally:
        await conn.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def main() -> int:
    settings = get_settings()
    if not settings.runtime_db_password:
        print("RUNTIME_DB_PASSWORD is empty: runtime login not managed", file=sys.stderr)
        return 1
    asyncio.run(ensure_runtime_login(settings.database_url, settings.runtime_db_password))
    print(f"runtime login {RUNTIME_LOGIN} is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
