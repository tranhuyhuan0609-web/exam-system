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
        calls = 0

        async def embed(self, texts):
            self.calls += 1
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
    with pytest.raises(AIUnavailable):  # still down: the sweep is told to back off
        db(lambda s: retry_ready(s, deps))
    embedder.down = False
    embedder.calls = 0
    assert db(lambda s: retry_ready(s, deps)) == 2
    assert embedder.calls == 1  # one embedding request for the whole batch
    assert set(db(lambda s: s.scalars(select(CrawledQuestion.status)))) == {CrawlItemStatus.PUBLISHED}


def test_chain_splits_work_into_each_providers_batch_size():
    small = Fake("groq")
    chain = ChainEnricher([(small, 0, 2)])
    items = [EnrichItem(n, "MULTIPLE_CHOICE", "x", ["a", "b"], "A", "") for n in range(5)]
    results = asyncio.run(chain.enrich(items))
    assert [r.ref for r in results] == [0, 1, 2, 3, 4] and small.calls == 3  # 2 + 2 + 1


def test_partial_results_when_providers_run_out_midway():
    class TwoCalls(Fake):
        async def enrich(self, items):
            if self.calls >= 1:
                self.calls += 1
                raise AIUnavailable("groq: 429")
            return await super().enrich(items)

    chain = ChainEnricher([(TwoCalls("groq"), 0, 2)])
    items = [EnrichItem(n, "MULTIPLE_CHOICE", "x", ["a", "b"], "A", "") for n in range(5)]
    assert [r.ref for r in asyncio.run(chain.enrich(items))] == [0, 1]  # the rest waits for the next sweep


def test_answer_checks_are_sampled_stably():
    from app.pipeline.use_cases.silver import sampled_for_check

    base = {
        "site_id": "hoc247",
        "name": "h",
        "base_url": "https://hoc247.net",
        "parser": "hoc247_question",
        "verify_answers": True,
        "seeds": [{"url": "https://hoc247.net/x"}],
        "follow": [{"pattern": "x"}],
    }
    every = SiteConfig.model_validate(base)
    fifth = SiteConfig.model_validate({**base, "verify_ratio": 0.2})
    ids = [str(n) for n in range(1000)]
    assert all(sampled_for_check(every, i) for i in ids)
    picked = [i for i in ids if sampled_for_check(fifth, i)]
    assert 150 < len(picked) < 250 and picked == [i for i in ids if sampled_for_check(fifth, i)]
    assert not sampled_for_check(SiteConfig.model_validate({**base, "verify_answers": False}), "1")


def test_renormalize_releases_questions_queued_before_the_rule_based_conversion(db):
    from app.pipeline.use_cases.silver import renormalize_waiting

    async def setup(s):
        await make_tree(s, {"Vật lí 11": {"Chương 1: Dao động": {"Bài 1: Dao động điều hoà": {}}}})
        config = SiteConfig.model_validate(
            {
                "site_id": "vietjack",
                "name": "v",
                "base_url": "https://vietjack.com",
                "parser": "vietjack_quiz",
                "seeds": [{"url": "https://vietjack.com/x"}],
                "follow": [{"pattern": "x"}],
                "categories": [{"pattern": "vietjack", "root": "Vật lí 11", "lesson": r"bài\s*(\d+)"}],
            }
        )
        s.add(CrawlerSite(site_id="vietjack", name="v", config=config.model_dump(mode="json")))
        await s.flush()
        s.add(
            CrawledQuestion(
                site_id="vietjack",
                external_id="q1",
                doc_id="d",
                source_url="https://vietjack.com/bai-1.jsp",
                source_title="Trắc nghiệm Bài 1",
                question_type="MULTIPLE_CHOICE",
                content="Vật dao động theo x = 4cos(3πt) cm. Biên độ là",
                explanation="A = 4 cm.",
                choices=[
                    {"id": "a", "content": "4 cm", "is_correct": True},
                    {"id": "b", "content": "3 cm", "is_correct": False},
                ],
                difficulty="UNDERSTANDING",
                source_hash="h",
                content_hash="h",
                status=CrawlItemStatus.PENDING_AI,
                issues=[],
            )
        )

    db(setup)
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
        enricher=Fake("groq"),
    )
    assert db(lambda s: renormalize_waiting(s, deps)) == {"PUBLISHED": 1}
    item = db(lambda s: s.scalar(select(CrawledQuestion)))
    assert item.content == r"Vật dao động theo $x = 4\cos(3\pi t)$ cm. Biên độ là"


def test_idle_backfill_embeds_questions_published_without_one(db):
    from app.models import Difficulty, Question
    from app.pipeline.use_cases.silver import backfill_embeddings

    class Counting:
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            return [[1.0] + [0.0] * 767 for _ in texts]

    async def setup(s):
        tree = await make_tree(s, {"Toán 11": {}})
        for n in range(3):
            s.add(
                Question(
                    root_id=tree["Toán 11"].id,
                    category_id=tree["Toán 11"].id,
                    difficulty=Difficulty.RECOGNITION,
                    content=f"Câu {n}",
                    choices=[{"id": "a", "content": "x", "is_correct": True}],
                )
            )

    db(setup)
    embedder = Counting()
    deps = SilverDeps(
        store=MemoryRawStore(),
        storage=get_storage(),
        bronze_storage=get_bronze_storage(),
        downloader=MemoryDownloader(),
        embedder=embedder,
    )
    assert db(lambda s: backfill_embeddings(s, deps)) == 3 and embedder.calls == 1
    assert db(lambda s: backfill_embeddings(s, deps)) == 0
