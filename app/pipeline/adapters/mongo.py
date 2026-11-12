"""Bronze store on MongoDB: raw_documents (exact HTML of each page / file metadata) and dlq_events."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.pipeline.domain.models import RawDocument


def _to_doc(row: dict[str, Any]) -> RawDocument:
    return RawDocument(
        id=row["_id"],
        site_id=row["site_id"],
        url=row["url"],
        kind=row["kind"],
        fetched_at=row["fetched_at"].replace(tzinfo=UTC) if row["fetched_at"].tzinfo is None else row["fetched_at"],
        content_hash=row["content_hash"],
        run_id=row.get("run_id"),
        html=row.get("html"),
        storage_key=row.get("storage_key"),
        title=row.get("title", ""),
        seed_label=row.get("seed_label", ""),
        context=row.get("context") or {},
        published=row.get("published", False),
    )


class MongoRawStore:
    def __init__(self, url: str | None = None, database: str | None = None):
        settings = get_settings()
        self._client = AsyncMongoClient(url or settings.mongo_url, tz_aware=True)
        self._db = self._client[database or settings.mongo_db]
        self.docs = self._db["raw_documents"]
        self.dlq = self._db["dlq_events"]
        self._indexed = False

    async def _ensure_indexes(self) -> None:
        if not self._indexed:
            await self.docs.create_index([("site_id", ASCENDING), ("url", ASCENDING), ("fetched_at", DESCENDING)])
            await self.docs.create_index([("published", ASCENDING), ("fetched_at", DESCENDING)])
            await self.dlq.create_index([("replayed", ASCENDING), ("created_at", DESCENDING)])
            self._indexed = True

    async def save(self, doc: RawDocument) -> bool:
        await self._ensure_indexes()
        row = {
            "_id": doc.id,
            "site_id": doc.site_id,
            "url": doc.url,
            "kind": doc.kind,
            "fetched_at": doc.fetched_at,
            "content_hash": doc.content_hash,
            "run_id": doc.run_id,
            "html": doc.html,
            "storage_key": doc.storage_key,
            "title": doc.title,
            "seed_label": doc.seed_label,
            "context": doc.context,
            "published": getattr(doc, "published", False),
        }
        try:
            await self.docs.insert_one(row)
            return True
        except DuplicateKeyError:  # same URL and same bytes: just remember we saw it again
            old = await self.docs.find_one_and_update(
                {"_id": doc.id}, {"$set": {"last_seen_at": doc.fetched_at, "context": doc.context or {}}}
            )
            # A page first crawled without table-of-contents context counts as new once it has one.
            return bool(doc.context) and (old or {}).get("context") != doc.context

    async def get(self, doc_id: str) -> RawDocument | None:
        row = await self.docs.find_one({"_id": doc_id})
        return _to_doc(row) if row else None

    async def mark_published(self, doc_id: str, published: bool = True) -> None:
        await self._ensure_indexes()
        await self.docs.update_one({"_id": doc_id}, {"$set": {"published": published}})

    async def get_unpublished(self, limit: int = 100) -> list[RawDocument]:
        await self._ensure_indexes()
        cursor = self.docs.find({"published": {"$ne": True}}).sort("fetched_at", DESCENDING).limit(limit)
        return [_to_doc(row) async for row in cursor]

    async def fetched_since(self, site_id: str, since: datetime, with_context: bool = False) -> set[str]:
        await self._ensure_indexes()
        query: dict[str, Any] = {
            "site_id": site_id,
            "$or": [{"fetched_at": {"$gte": since}}, {"last_seen_at": {"$gte": since}}],
        }
        if with_context:  # pages stored before auto_toc must be fetched again to learn their chapter
            query["context.chapter"] = {"$exists": True}
        cursor = self.docs.find(query, {"url": 1})
        return {row["url"] async for row in cursor}

    async def iter_site(self, site_id: str) -> AsyncIterator[RawDocument]:
        async for row in self.docs.find({"site_id": site_id}).sort("fetched_at", DESCENDING):
            yield _to_doc(row)

    async def save_dead_letter(self, event: dict[str, Any], stage: str, error: str) -> str:
        await self._ensure_indexes()
        result = await self.dlq.insert_one(
            {"event": event, "stage": stage, "error": error[:20000], "created_at": datetime.now(UTC), "replayed": False}
        )
        return str(result.inserted_id)

    async def dead_letters(self, limit: int = 100, stage: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"replayed": False}
        if stage:
            query["stage"] = stage
        rows = await self.dlq.find(query).sort("created_at", DESCENDING).limit(limit).to_list()
        for row in rows:
            row["id"] = str(row.pop("_id"))
        return rows

    async def mark_replayed(self, dead_letter_id: str) -> None:
        await self.dlq.update_one({"_id": ObjectId(dead_letter_id)}, {"$set": {"replayed": True}})

    async def count_dead_letters(self) -> int:
        return await self.dlq.count_documents({"replayed": False})

    async def close(self) -> None:
        await self._client.close()
