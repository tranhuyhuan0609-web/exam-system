"""AI fallback chain for crawled questions: Gemini -> Groq -> OpenRouter, budgets, cool-downs, vision routing."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.infrastructure.storage import get_bronze_storage, get_storage
from app.models import CrawledQuestion, CrawlerSite, CrawlItemStatus
from app.pipeline.adapters.ai_chain import ChainEnricher, MemoryUsage, OpenAICompatEnricher
from app.pipeline.adapters.memory import MemoryDownloader, MemoryRawStore
from app.pipeline.domain.models import RawDocument, SiteConfig, doc_id
from app.pipeline.ports import AIUnavailable, EnrichItem, EnrichResult
from app.pipeline.use_cases.silver import SilverDeps, ingest_document, retry_ready
from tests.factories import make_tree
from tests.test_pipeline import LGH_PAGE, LGH_URL

ITEM = EnrichItem(
    ref=1, question_type="MULTIPLE_CHOICE", content="x = 2", choices=["a", "b"], answer="A", explanation=""
)


class Fake:
    def __init__(self, name, vision=False, fail=False):
        self.name, self.vision, self.fail, self.calls = name, vision, fail, 0

    async def enrich(self, items):
        self.calls += 1
        if self.fail:
            raise AIUnavailable(f"{self.name}: 429")
        return [EnrichResult(i.ref, i.content, i.choices, "Lời giải", True) for i in items]


def test_chain_falls_back_rests_and_respects_budgets():
    gemini, groq, openrouter = Fake("gemini", vision=True, fail=True), Fake("groq"), Fake("openrouter")
    now = [0.0]
    usage = MemoryUsage()
    chain = ChainEnricher([(gemini, 0), (groq, 2), (openrouter, 0)], usage, clock=lambda: now[0])

    results = asyncio.run(chain.enrich([ITEM]))
    assert results[0].provider == "groq" and gemini.calls == 1
    asyncio.run(chain.enrich([ITEM]))
    assert gemini.calls == 1  # resting after its 429: not asked again
    assert asyncio.run(chain.enrich([ITEM]))[0].provider == "openrouter"  # groq's daily budget (2) is used
    assert usage.calls == {"groq": 2, "openrouter": 1}

    now[0] += 3600  # cool-down over: gemini is tried first again
    gemini.fail = False
    assert asyncio.run(chain.enrich([ITEM]))[0].provider == "gemini"


def test_formula_images_only_go_to_vision_providers():
    groq = Fake("groq")
    chain = ChainEnricher([(groq, 0)])
    with_image = EnrichItem(
        2, "MULTIPLE_CHOICE", "[[img:0]] = ?", ["a", "b"], "A", "", images=[(0, b"png", "image/png")]
    )
    with pytest.raises(AIUnavailable):
        asyncio.run(chain.enrich([with_image]))
    assert groq.calls == 0


def test_openai_compatible_adapter_parses_json_and_maps_errors(monkeypatch):
    answer = {
        "questions": [
            {
                "ref": 1,
                "content": "$x = 2$",
                "choices": ["a", "b"],
                "explanation": "Vì…",
                "explanation_written": True,
                "difficulty": "RECOGNITION",
            }
        ]
    }
    responses = [
        httpx.Response(429, json={}),
        httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]}),
    ]
    seen = []

    async def fake_post(self, url, headers=None, json=None):
        seen.append((url, json["model"], json["response_format"]))
        response = responses.pop(0)
        response.request = httpx.Request("POST", url)
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    groq = OpenAICompatEnricher("groq", "https://api.groq.com/openai/v1/", "key", "some-model")
    with pytest.raises(AIUnavailable):
        asyncio.run(groq.enrich([ITEM]))
    result = asyncio.run(groq.enrich([ITEM]))[0]
    assert (result.content, result.explanation_written, result.difficulty) == ("$x = 2$", True, "RECOGNITION")
    assert seen[0] == ("https://api.groq.com/openai/v1/chat/completions", "some-model", {"type": "json_object"})


def test_embedding_outage_keeps_questions_ready_until_it_returns(db):
    class FlakyEmbedder:
        down = True

        async def embed(self, texts):
            if self.down:
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return [[1.0] + [0.0] * 767 for _ in texts]

    async def setup(s):
        await make_tree(s, {"Vật lí 11": {"Chương 1: Dao động": {"Bài 1: Dao động điều hoà": {}}}})
        config = SiteConfig.model_validate(
            {
                "site_id": "loigiaihay",
                "name": "l",
                "base_url": "https://loigiaihay.com",
                "parser": "loigiaihay_quiz",
                "seeds": [{"url": "https://loigiaihay.com/x"}],
                "follow": [{"pattern": "x"}],
                "categories": [{"pattern": "trac-nghiem", "root": "Vật lí 11", "lesson": r"bài\s*(\d+)"}],
            }
        )
        s.add(CrawlerSite(site_id="loigiaihay", name="l", config=config.model_dump(mode="json")))

    db(setup)
    embedder = FlakyEmbedder()
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
        embedder=embedder,
    )
    doc = RawDocument(
        doc_id("loigiaihay", LGH_URL, "h"), "loigiaihay", LGH_URL, "html", datetime.now(UTC), "h", html=LGH_PAGE
    )
    db(lambda s: ingest_document(s, deps, doc))  # must not fail the whole page
    statuses = db(lambda s: s.scalars(select(CrawledQuestion.status)))
    assert set(statuses) == {CrawlItemStatus.READY}
    embedder.down = False
    assert db(lambda s: retry_ready(s, deps)) == 2
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.PUBLISHED}
