"""Ports of the crawler pipeline (hexagonal): the use cases only see these protocols, so Kafka, MongoDB and
Gemini can be swapped (in-memory fakes in tests, another broker later) without touching the logic."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.pipeline.domain.models import RawDocument

RAW_TOPIC = "raw.exam.ingestion"
DLQ_TOPIC = "raw.exam.ingestion.dlq"
LIBRARY_TOPIC = "raw.library.ingestion"  # Drive files fetched into Bronze, for the library consumer


class RawStore(Protocol):
    """Bronze: raw documents (MongoDB raw_documents) and dead letters (dlq_events)."""

    async def save(self, doc: RawDocument) -> bool: ...  # True when the document is new

    async def get(self, doc_id: str) -> RawDocument | None: ...

    async def fetched_since(self, site_id: str, since: datetime, with_context: bool = False) -> set[str]: ...

    def iter_site(self, site_id: str) -> AsyncIterator[RawDocument]: ...

    async def save_dead_letter(self, event: dict[str, Any], stage: str, error: str) -> str: ...

    async def dead_letters(self, limit: int = 100, stage: str | None = None) -> list[dict[str, Any]]: ...

    async def mark_replayed(self, dead_letter_id: str) -> None: ...

    async def count_dead_letters(self) -> int: ...


class EventBus(Protocol):
    async def publish(self, topic: str, value: dict[str, Any], key: str | None = None) -> None: ...


class AIUnavailable(Exception):
    """No AI provider can take requests now (quota, overload): waiting questions stay as they are."""


@dataclass
class EnrichItem:
    """One crawled question sent to the AI: texts use [[img:N]] for the images attached in `images`."""

    ref: int
    question_type: str
    content: str
    choices: list[str]
    answer: str  # correct letter(s), true/false pattern or numeric answer, for writing the solution
    explanation: str
    images: list[tuple[int, bytes, str]] = field(default_factory=list)  # (placeholder number, data, mime)


@dataclass
class EnrichResult:
    ref: int
    content: str
    choices: list[str]
    explanation: str
    explanation_written: bool  # the AI wrote the solution (the source had none)
    difficulty: str | None = None
    provider: str = ""  # which AI answered (gemini, groq, openrouter)
    answer_ok: bool | None = None  # False: the AI thinks the source's answer key is wrong


class Enricher(Protocol):
    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class Downloader(Protocol):
    async def download(self, url: str) -> tuple[bytes, str]: ...  # (data, content type)
