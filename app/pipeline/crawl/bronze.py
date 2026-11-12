"""Scrapy item pipeline = the Bronze writer.

HTML goes to MongoDB (raw_documents), files to the bronze-raw bucket; every NEW detail page or file is
announced on Kafka raw.exam.ingestion with its document id only. Fetch errors go to the DLQ topic.
"""

import hashlib
import mimetypes
from datetime import UTC, datetime

from app.infrastructure.storage import get_bronze_storage
from app.pipeline.domain.models import RawDocument, doc_id
from app.pipeline.ports import DLQ_TOPIC, RAW_TOPIC

EXT = {"pdf": ".pdf", "docx": ".docx"}


class BronzePipeline:
    def __init__(self, store=None, bus=None, storage=None):
        self.store, self.bus, self.storage = store, bus, storage

    async def _ready(self):
        if self.store is None:
            from app.pipeline.adapters.mongo import MongoRawStore

            self.store = MongoRawStore()
        if self.bus is None:
            from app.pipeline.adapters.kafka import KafkaBus

            self.bus = await KafkaBus().start()
        if self.storage is None:
            self.storage = get_bronze_storage()

    async def process_item(self, item: dict, spider):
        await self._ready()
        stats = spider.crawler.stats
        site_id = spider.site.site_id
        if item["kind"] == "error":
            await self.bus.publish(
                DLQ_TOPIC,
                {
                    "stage": "fetch",
                    "site_id": site_id,
                    "run_id": spider.run_id,
                    "url": item["url"],
                    "error": item["error"],
                },
            )
            return item
        data = item["body"].encode("utf-8") if isinstance(item["body"], str) else item["body"]
        digest = hashlib.sha256(data).hexdigest()
        doc = RawDocument(
            id=doc_id(site_id, item["url"], digest),
            site_id=site_id,
            url=item["url"],
            kind=item["kind"],
            fetched_at=datetime.now(UTC),
            content_hash=digest,
            run_id=spider.run_id,
            title=item.get("title", ""),
            seed_label=item.get("label", ""),
            context=item.get("context") or {},
        )
        if item["kind"] == "html":
            doc.html = item["body"]
        else:
            ext = EXT.get(item["kind"]) or mimetypes.guess_extension(item.get("content_type", "")) or ""
            doc.storage_key = f"{site_id}/{digest}{ext}"
            if not await self.storage.exists(doc.storage_key):
                await self.storage.put(doc.storage_key, data, item.get("content_type", "application/octet-stream"))
        is_new = await self.store.save(doc)
        stats.inc_value("bronze/new" if is_new else "bronze/unchanged")
        if is_new and item["role"] != "listing":
            try:
                await self.bus.publish(RAW_TOPIC, doc.event(), key=doc.id)
                if hasattr(self.store, "mark_published"):
                    await self.store.mark_published(doc.id, True)
                stats.inc_value("bronze/published")
            except Exception:
                stats.inc_value("bronze/publish_failed")
                stats.inc_value("log_count/ERROR")
        return {"url": item["url"], "doc_id": doc.id, "new": is_new}  # keep the HTML out of Scrapy's logs

    async def close_spider(self, spider):
        if self.bus is not None and hasattr(self.bus, "stop"):
            await self.bus.stop()
        if self.store is not None and hasattr(self.store, "close"):
            await self.store.close()


async def replay_unpublished_docs(store=None, bus=None, limit: int = 100) -> int:
    """Scan Mongo for raw documents whose Kafka ingestion event was not published and replay them."""
    if store is None:
        from app.pipeline.adapters.mongo import MongoRawStore

        store = MongoRawStore()
    if bus is None:
        from app.pipeline.adapters.kafka import KafkaBus

        bus = await KafkaBus().start()

    docs = await store.get_unpublished(limit=limit)
    replayed = 0
    for doc in docs:
        try:
            await bus.publish(RAW_TOPIC, doc.event(), key=doc.id)
            await store.mark_published(doc.id, True)
            replayed += 1
        except Exception:
            pass
    return replayed
