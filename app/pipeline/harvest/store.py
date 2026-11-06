"""Where harvested records go: library_documents (one row per work), and the cursors in MongoDB (harvest_state)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pymongo import AsyncMongoClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import LibraryDocument, LibraryStatus
from app.pipeline.harvest.sources import OpenRecord

PLAN_FILE = Path("configs/open_sources.yaml")
SOURCE_NAMES = {
    "openalex": "OpenAlex",
    "semanticscholar": "Semantic Scholar",
    "crossref": "Crossref",
    "openlibrary": "Open Library",
    "gutendex": "Project Gutenberg",
}


class PlanItem(BaseModel):
    name: str
    folder: list[str]
    search: str = ""
    filter: str = ""
    sort: str = ""
    by_field: bool = False  # OpenAlex: one subfolder per research field
    pages: int | None = None  # pages per run, instead of the plan's pages_per_run


class TriviaPlan(BaseModel):
    root: str = "Ngân hàng trắc nghiệm quốc tế"
    categories: dict[int, str] = {}
    refresh_days: int = 30


class Plan(BaseModel):
    schedule_times: list[str] = ["02:00"]
    pages_per_run: int = 10
    files_per_run: int = 200  # open PDFs fetched per batch; batches run all day, between the harvest runs
    max_file_mb: int = 40
    file_concurrency: int = 6
    max_storage_gb: float = 100  # open files stop here (disk space)
    sources: dict[str, list[PlanItem]] = Field(default_factory=dict)
    opentrivia: TriviaPlan = TriviaPlan()


def load_plan(path: Path = PLAN_FILE) -> Plan:
    return Plan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class StateStore:
    """One document per (source, plan item): the cursor of the next page, done, how many records so far."""

    def __init__(self, url: str | None = None, database: str | None = None):
        settings = get_settings()
        self._client = AsyncMongoClient(url or settings.mongo_url, tz_aware=True)
        self.states = self._client[database or settings.mongo_db]["harvest_state"]

    async def get(self, key: str) -> dict[str, Any]:
        return await self.states.find_one({"_id": key}) or {"_id": key, "cursor": None, "done": False, "records": 0}

    async def put(self, key: str, **values: Any) -> None:
        await self.states.update_one({"_id": key}, {"$set": {**values, "updated_at": datetime.now(UTC)}}, upsert=True)

    async def all(self) -> list[dict[str, Any]]:
        return [row async for row in self.states.find().sort("_id")]

    async def close(self) -> None:
        await self._client.close()


def _clip(value: str | None, size: int) -> str | None:
    return value[:size] if value else None


async def save_records(session: AsyncSession, records: list[OpenRecord]) -> int:
    """New works only (by key: a DOI found by two sources is kept once); published at once, they are open access."""
    unique = {r.key: r for r in records}
    if not unique:
        return 0
    known = set(
        await session.scalars(select(LibraryDocument.source_file_id).where(LibraryDocument.source_file_id.in_(unique)))
    )
    new = [r for key, r in unique.items() if key not in known]
    for r in new:
        note = " · ".join(filter(None, (r.license or "Open access", SOURCE_NAMES.get(r.source, r.source))))
        session.add(
            LibraryDocument(
                title=r.title[:500],
                kind=r.kind,
                status=LibraryStatus.PUBLISHED,
                folder_path=r.folder,
                source=r.source,
                source_file_id=r.key,
                source_url=_clip(r.url, 1000),
                file_url=_clip(r.file_url, 1000),
                storage_key="",  # listed with a link to the source until files.py stores the open PDF
                mime="text/html",
                authors=_clip(r.authors, 500),
                year=r.year if isinstance(r.year, int) and 0 < r.year < 3000 else None,
                language=_clip(r.language, 10),
                license_note=note[:300],
            )
        )
    await session.commit()
    return len(new)
