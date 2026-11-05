"""In-memory adapters: tests, and `--inline` runs without Kafka/MongoDB."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.pipeline.domain.models import RawDocument
from app.pipeline.ports import EnrichItem, EnrichResult


class MemoryRawStore:
    def __init__(self):
        self.docs: dict[str, RawDocument] = {}
        self.dlq: list[dict[str, Any]] = []

    async def save(self, doc: RawDocument) -> bool:
        old = self.docs.get(doc.id)
        if old is not None:
            changed = bool(doc.context) and old.context != doc.context
            old.context = doc.context or old.context
            return changed
        self.docs[doc.id] = doc
        return True

    async def get(self, doc_id: str) -> RawDocument | None:
        return self.docs.get(doc_id)

    async def fetched_since(self, site_id: str, since: datetime, with_context: bool = False) -> set[str]:
        return {
            d.url
            for d in self.docs.values()
            if d.site_id == site_id and d.fetched_at >= since and (not with_context or d.context.get("chapter"))
        }

    async def iter_site(self, site_id: str) -> AsyncIterator[RawDocument]:
        for doc in list(self.docs.values()):
            if doc.site_id == site_id:
                yield doc

    async def save_dead_letter(self, event: dict[str, Any], stage: str, error: str) -> str:
        self.dlq.append(
            {
                "id": str(len(self.dlq)),
                "event": event,
                "stage": stage,
                "error": error,
                "created_at": datetime.now(UTC),
                "replayed": False,
            }
        )
        return self.dlq[-1]["id"]

    async def dead_letters(self, limit: int = 100, stage: str | None = None) -> list[dict[str, Any]]:
        rows = [r for r in self.dlq if not r["replayed"] and (stage is None or r["stage"] == stage)]
        return rows[:limit]

    async def mark_replayed(self, dead_letter_id: str) -> None:
        self.dlq[int(dead_letter_id)]["replayed"] = True

    async def count_dead_letters(self) -> int:
        return sum(1 for r in self.dlq if not r["replayed"])


class MemoryBus:
    def __init__(self):
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        self.messages.append((topic, value))


class EchoEnricher:
    """Pretends to be the AI: wraps nothing, writes a placeholder solution when none exists."""

    def __init__(self):
        self.calls: list[list[EnrichItem]] = []

    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]:
        self.calls.append(items)
        return [
            EnrichResult(
                ref=i.ref,
                content=i.content.replace("[[img:0]]", "$x^2$"),
                choices=i.choices,
                explanation=i.explanation or f"Lời giải AI cho câu {i.ref}.",
                explanation_written=not i.explanation,
                difficulty="APPLICATION",
            )
            for i in items
        ]


class HashEmbedder:
    """Deterministic fake embeddings: identical texts give identical vectors; others are near-orthogonal."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        out = []
        for text in texts:
            seed = hashlib.sha256(" ".join(text.lower().split()).encode()).digest()
            vec = [((seed[i % 32] + i * 31) % 255) / 255 - 0.5 for i in range(self.dim)]
            norm = sum(v * v for v in vec) ** 0.5
            out.append([v / norm for v in vec])
        return out


class MemoryDownloader:
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = files or {}

    async def download(self, url: str) -> tuple[bytes, str]:
        if url not in self.files:
            raise OSError(f"404 {url}")
        return self.files[url], "image/png"
