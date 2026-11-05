from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

app = FastAPI(title="Exam System", version="0.0.1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db")
async def health_db(session: SessionDep) -> dict:
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "up"}


@app.get("/api/time")
async def server_time() -> dict:
    """Authoritative server clock. Clients use it to compute their offset; client time is never trusted."""
    return {"server_time": datetime.now(UTC).isoformat()}
