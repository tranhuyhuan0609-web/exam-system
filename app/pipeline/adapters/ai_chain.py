"""AI fallback chain for Silver: Gemini -> Groq -> OpenRouter (free tiers), with daily budgets and cool-downs.

Groq and OpenRouter speak the OpenAI chat-completions API, so one small adapter (plain httpx) serves both.
A provider that answers 429 / 5xx rests for a while and the next one is tried; items with formula images only
go to providers that read images. When every provider is out, AIUnavailable is raised and the questions keep
waiting (see app/pipeline/use_cases/silver.py).
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.domain.latex import repair_latex
from app.pipeline.adapters.gemini import ENRICH_PROMPT, _EnrichResult
from app.pipeline.ports import AIUnavailable, EnrichItem, EnrichResult

log = logging.getLogger(__name__)
COOLDOWN_SECONDS = 10 * 60


class Usage(Protocol):
    async def used_today(self, provider: str) -> int: ...

    async def record(self, provider: str, items: int) -> None: ...


class MemoryUsage:
    def __init__(self):
        self.calls: dict[str, int] = {}

    async def used_today(self, provider: str) -> int:
        return self.calls.get(provider, 0)

    async def record(self, provider: str, items: int) -> None:
        self.calls[provider] = self.calls.get(provider, 0) + 1


class DbUsage:
    """Calls per provider and day in ai_usage (shown on the crawler stats page)."""

    async def used_today(self, provider: str) -> int:
        from app.db import SessionLocal
        from app.models import AIUsage

        async with SessionLocal() as session:
            row = await session.get(AIUsage, (datetime.now(UTC).date(), provider))
            return row.calls if row else 0

    async def record(self, provider: str, items: int) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from app.db import SessionLocal
        from app.models import AIUsage

        stmt = insert(AIUsage).values(day=datetime.now(UTC).date(), provider=provider, calls=1, items=items)
        stmt = stmt.on_conflict_do_update(
            index_elements=["day", "provider"],
            set_={"calls": AIUsage.calls + 1, "items": AIUsage.items + items},
        )
        async with SessionLocal() as session:
            await session.execute(stmt)
            await session.commit()


def _parse(items: list[EnrichItem], raw: str) -> list[EnrichResult]:
    start, end = raw.find("{"), raw.rfind("}")
    result = _EnrichResult.model_validate_json(raw[start : end + 1] if start != -1 else raw)
    by_ref = {q.ref: q for q in result.questions}
    out = []
    for item in items:
        q = by_ref.get(item.ref)
        if q is None or len(q.choices) != len(item.choices):
            continue
        out.append(
            EnrichResult(
                ref=item.ref,
                content=repair_latex(q.content),
                choices=[repair_latex(c) for c in q.choices],
                explanation=repair_latex(q.explanation),
                explanation_written=q.explanation_written and not item.explanation,
                difficulty=q.difficulty,
                answer_ok=q.answer_ok,
            )
        )
    return out


class OpenAICompatEnricher:
    """Chat-completions providers (Groq, OpenRouter): text only, JSON answer validated like Gemini's."""

    def __init__(
        self, name: str, base_url: str, api_key: str, model: str, timeout: float = 120, headers: dict | None = None
    ):
        self.name, self.model, self.vision = name, model, False
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = headers or {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._timeout = timeout

    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]:
        payload = [
            {
                "ref": i.ref,
                "type": i.question_type,
                "content": i.content,
                "choices": i.choices,
                "answer": i.answer,
                "explanation": i.explanation,
            }
            for i in items
        ]
        schema = json.dumps(_EnrichResult.model_json_schema(), ensure_ascii=False)
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": ENRICH_PROMPT + "\nTrả về DUY NHẤT một JSON đúng schema: " + schema},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(self._url, headers=self._headers, json=body)
            except httpx.HTTPError as exc:
                raise AIUnavailable(f"{self.name}: {exc}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise AIUnavailable(f"{self.name}: HTTP {response.status_code}")
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"] or ""
        try:
            return _parse(items, content)
        except ValueError as exc:  # malformed JSON: let the next provider try
            raise AIUnavailable(f"{self.name}: unusable answer ({exc})") from exc


class GeminiChainMember:
    """The Gemini enricher with fast fail-over (one round of model fallbacks, no long sleeps)."""

    def __init__(self):
        from app.ai.provider import GeminiProvider
        from app.config import get_settings
        from app.pipeline.adapters.gemini import GeminiEnricher

        settings = get_settings()
        provider = GeminiProvider(settings.gemini_api_key, settings.gemini_model_list, max_rounds=1, base_delay=1)
        self._inner = GeminiEnricher(provider)
        self.name, self.vision = "gemini", True

    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]:
        from app.ai.provider import AIError

        try:
            return await self._inner.enrich(items)
        except AIError as exc:
            raise AIUnavailable(f"gemini: {exc}") from exc


@dataclass
class _Member:
    enricher: object
    daily_limit: int
    batch_size: int = 8


class ChainEnricher:
    """`members`: (enricher, daily limit) or (enricher, daily limit, batch size) tuples, tried in order."""

    def __init__(self, members: list[tuple], usage: Usage | None = None, clock=time.monotonic):
        self.members = [_Member(*m) for m in members]
        self.usage = usage or MemoryUsage()
        self._resting: dict[str, float] = {}
        self._clock = clock

    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]:
        """Split into each provider's batch size; questions left over when every provider is out stay waiting."""
        results: list[EnrichResult] = []
        pending = list(items)
        while pending:
            try:
                done, used = await self._one_batch(pending)
            except AIUnavailable:
                if results:
                    return results  # the rest waits for the next sweep
                raise
            results += done
            pending = pending[used:]
        return results

    async def _one_batch(self, items: list[EnrichItem]) -> tuple[list[EnrichResult], int]:
        errors = []
        for member in self.members:
            chunk = items[: max(1, member.batch_size)]
            needs_vision = any(i.images for i in chunk)
            name = member.enricher.name
            if needs_vision and not member.enricher.vision:
                continue
            if self._resting.get(name, 0) > self._clock():
                continue
            if member.daily_limit and await self.usage.used_today(name) >= member.daily_limit:
                errors.append(f"{name}: daily budget used")
                continue
            try:
                results = await member.enricher.enrich(chunk)
            except AIUnavailable as exc:
                log.warning("%s unavailable, trying the next provider: %s", name, exc)
                self._resting[name] = self._clock() + COOLDOWN_SECONDS
                errors.append(str(exc))
                continue
            await self.usage.record(name, len(chunk))
            for result in results:
                result.provider = name
            return results, len(chunk)
        raise AIUnavailable("; ".join(errors) or "no AI provider configured for these questions")


def build_chain():
    """The chain from settings; None when no provider has a key."""
    from app.config import get_settings

    settings = get_settings()
    limits = settings.ai_daily_limit_map
    sizes = settings.ai_batch_size_map
    members: list[tuple[object, int]] = []
    if settings.gemini_api_key:
        members.append((GeminiChainMember(), limits.get("gemini", 0), sizes.get("gemini", 15)))
    if settings.groq_api_key and settings.groq_model:
        members.append(
            (
                OpenAICompatEnricher("groq", settings.groq_base_url, settings.groq_api_key, settings.groq_model),
                limits.get("groq", 0),
                sizes.get("groq", 6),
            )
        )
    if settings.openrouter_api_key and settings.openrouter_model:
        members.append(
            (
                OpenAICompatEnricher(
                    "openrouter", settings.openrouter_base_url, settings.openrouter_api_key, settings.openrouter_model
                ),
                limits.get("openrouter", 0),
                sizes.get("openrouter", 8),
            )
        )
    for provider in settings.extra_providers:  # AI_PROVIDERS in .env
        for model in provider.models:
            members.append(
                (
                    OpenAICompatEnricher(
                        provider.name, provider.base_url, provider.api_key, model, headers=provider.headers
                    ),
                    limits.get(provider.name, 0),
                    sizes.get(provider.name, 8),
                )
            )
    return ChainEnricher(members, DbUsage()) if members else None
