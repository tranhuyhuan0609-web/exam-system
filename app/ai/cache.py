"""Cache of AI answers, keyed by the SHA-256 of the request (inputs + prompt), in PostgreSQL (ai_cache)."""

import hashlib
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import AICache


def digest(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        data = part.encode("utf-8") if isinstance(part, str) else part
        h.update(len(data).to_bytes(8, "big"))  # unambiguous boundaries between parts
        h.update(data)
    return h.hexdigest()


class DbCache:
    async def get(self, key: str) -> Any | None:
        async with SessionLocal() as session:
            row = await session.get(AICache, key)
            return row.value if row else None

    async def put(self, key: str, kind: str, value: Any) -> None:
        async with SessionLocal() as session:
            stmt = insert(AICache).values(key=key, kind=kind, value=value)
            await session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value}))
            await session.commit()


class MemoryCache:
    def __init__(self):
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any | None:
        return self.data.get(key)

    async def put(self, key: str, kind: str, value: Any) -> None:
        self.data[key] = value
