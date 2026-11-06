"""Gemini quota saving in the AI import: answers from the bank first, free text models second, Gemini last."""

import asyncio

import httpx

from app.ai.frugal import FrugalProvider, TextSolver, bank_lookup
from app.ai.provider import FigureImage
from app.ai.schemas import ExtractedChoice, ExtractedQuestion, Solution
from app.application.categories import create_category
from app.models import Difficulty, Question, QuestionType


def _q(number: int, content: str, choices: list[str], kind: str = "MULTIPLE_CHOICE") -> ExtractedQuestion:
    return ExtractedQuestion(
        number=number,
        page=1,
        type=kind,
        content=content,
        choices=[ExtractedChoice(label="ABCD"[i], content=c) for i, c in enumerate(choices)],
    )


def _solution(number: int, label: str = "A") -> Solution:
    return Solution(
        number=number,
        step_by_step_solution=f"giải câu {number}",
        final_value="",
        matches_an_option=True,
        correct_label=label,
        short_answer=None,
        difficulty="UNDERSTANDING",
    )


class Inner:
    models_used = {"gemini-x"}

    def __init__(self):
        self.solved: list[list[int]] = []
        self.figures: list[list[int]] = []

    async def solve(self, questions, figures):
        self.solved.append([q.number for q in questions])
        self.figures.append([f.question_number for f in figures])
        return [_solution(q.number, "D") for q in questions]


class FakeText(TextSolver):
    def __init__(self, fail: bool = False):
        super().__init__("groq", "http://x", "k", "gpt-oss")
        self.fail, self.solved = fail, []

    async def solve(self, questions, figures=()):
        self.solved.append([q.number for q in questions])
        if self.fail:
            raise httpx.HTTPError("429")
        return [_solution(q.number, "B") for q in questions]


def _bank(db):
    async def make(s):
        subject = await create_category(s, "Toán", slug="toan")
        s.add(
            Question(
                root_id=subject.id,
                category_id=subject.id,
                difficulty=Difficulty.APPLICATION,
                question_type=QuestionType.MULTIPLE_CHOICE,
                content="Giá trị cực đại của hàm số $y = x^3 - 3x$ bằng",
                choices=[
                    {"id": "a", "content": "$-2$", "is_correct": False},
                    {"id": "b", "content": "$2$", "is_correct": True},
                    {"id": "c", "content": "$0$", "is_correct": False},
                    {"id": "d", "content": "$1$", "is_correct": False},
                ],
                explanation="Lời giải trong ngân hàng",
                is_verified=True,
            )
        )
        await s.flush()
        return subject.id

    return db(make)


def test_another_code_of_the_same_paper_is_answered_from_the_bank_text_models_then_gemini(db):
    root_id = _bank(db)
    lookup = db(lambda s: bank_lookup(s, root_id))
    inner, text = Inner(), FakeText()
    provider = FrugalProvider(inner, lookup, [text])
    questions = [
        # the bank's question in another code: options in another order, so the answer is now C
        _q(1, "Giá trị cực đại của hàm số  $y = x^3 - 3x$ bằng.", ["$1$", "$0$", "$2$", "$-2$"]),
        _q(2, "Tính $1 + 1$.", ["$1$", "$2$", "$3$", "$4$"]),  # no figure: a text model
        _q(3, "Dựa vào hình vẽ, tìm $x$.", ["$1$", "$2$", "$3$", "$4$"]),  # a figure: Gemini
    ]
    answers = asyncio.run(provider.solve(questions, [FigureImage(3, b"png")]))
    assert [(a.number, a.correct_label) for a in answers] == [(1, "C"), (2, "B"), (3, "D")]
    assert answers[0].step_by_step_solution == "Lời giải trong ngân hàng" and answers[0].difficulty == "APPLICATION"
    assert text.solved == [[2]] and inner.solved == [[3]] and inner.figures == [[3]]
    assert provider.reused == 1 and "groq:gpt-oss" in provider.models_used


def test_gemini_takes_over_when_text_models_are_out_of_quota(db):
    inner, text = Inner(), FakeText(fail=True)
    provider = FrugalProvider(inner, None, [text])
    answers = asyncio.run(provider.solve([_q(1, "Tính $2 \\cdot 3$.", ["$5$", "$6$", "$7$", "$8$"])], []))
    assert [a.correct_label for a in answers] == ["D"] and text.solved == [[1]] and inner.solved == [[1]]


def test_different_options_are_not_taken_from_the_bank(db):
    root_id = _bank(db)
    lookup = db(lambda s: bank_lookup(s, root_id))
    same_text_other_options = _q(1, "Giá trị cực đại của hàm số $y = x^3 - 3x$ bằng", ["$3$", "$4$", "$5$", "$6$"])
    assert asyncio.run(lookup([same_text_other_options])) == {}


class FakeReader(FakeText):
    """A free vision model on OpenRouter."""

    def __init__(self, found):
        super().__init__()
        self.name, self.model, self.vision, self.found = "openrouter", "gemma", True, found
        self.figures: list[list[int]] = []

    async def extract(self, pages):
        return self.found

    async def solve(self, questions, figures=()):
        self.figures.append([f.question_number for f in figures])
        return await super().solve(questions, figures)


class InnerReader(Inner):
    def __init__(self):
        super().__init__()
        self.read = 0

    async def extract(self, pages):
        self.read += 1
        return [_q(9, "Gemini đọc", ["$1$", "$2$", "$3$", "$4$"])]


def test_free_vision_models_read_pages_and_figures_before_gemini():
    inner = InnerReader()
    reader = FakeReader([_q(1, "Gemma đọc", ["$1$", "$2$", "$3$", "$4$"])])
    provider = FrugalProvider(inner, None, [FakeText(fail=True)], [reader])
    assert [q.content for q in asyncio.run(provider.extract([]))] == ["Gemma đọc"] and inner.read == 0
    figure_question = _q(3, "Dựa vào hình vẽ, tìm $x$.", ["$1$", "$2$", "$3$", "$4$"])
    answers = asyncio.run(provider.solve([figure_question], [FigureImage(3, b"png")]))
    assert [a.correct_label for a in answers] == ["B"] and reader.figures == [[3]] and inner.solved == []

    nothing_found = FrugalProvider(inner, None, [], [FakeReader([])])  # it could not read: Gemini tries
    assert [q.content for q in asyncio.run(nothing_found.extract([]))] == ["Gemini đọc"] and inner.read == 1


def test_gemini_skips_models_out_of_daily_quota_and_then_waits():
    import pytest
    from google.genai import errors

    from app.ai.provider import AIQuotaExhausted, GeminiProvider
    from app.ai.schemas import ExtractionResult

    calls = []

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append((model, str(config.thinking_config.thinking_level.value).lower()))
            raise errors.APIError(
                429, {"error": {"code": 429, "message": "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}
            )

    provider = GeminiProvider("k", ["flash"], base_delay=0, extract_models=["lite", "flash"], extract_thinking="low")
    provider._client = type("C", (), {"aio": type("A", (), {"models": Models()})()})()
    with pytest.raises(AIQuotaExhausted):
        asyncio.run(provider._generate(["x"], ExtractionResult, models=["lite", "flash"], thinking="low"))
    assert calls == [("lite", "low"), ("flash", "low")]  # each model tried once, no waiting rounds


def test_latex_with_single_backslashes_in_a_json_answer_is_repaired():
    import json

    from app.ai.frugal import _loose_json
    from app.domain.latex import repair_latex

    # What models write: single backslashes (\s is no JSON escape, \f is form feed), a correct \\ and a \n.
    raw = r'{"x": "$\sqrt{16} + \frac{1}{2}$, $\Delta$ và \\alpha, xuống dòng\n"}'
    value = json.loads(_loose_json(raw))["x"]
    assert repair_latex(value) == "$\\sqrt{16} + \\frac{1}{2}$, $\\Delta$ và \\alpha, xuống dòng\n"


def test_the_same_pages_and_questions_are_not_sent_twice():
    from app.ai.cache import MemoryCache
    from app.ai.provider import PageImage

    class Reader(InnerReader):
        async def solve(self, questions, figures):
            self.solved.append([q.number for q in questions])
            return [_solution(q.number, "C") for q in questions]

    inner, cache = Reader(), MemoryCache()
    pages = [PageImage(number=1, png=b"page-1"), PageImage(number=2, png=b"page-2")]
    first = FrugalProvider(inner, None, [], [], cache=cache)
    asyncio.run(first.extract(pages))
    question = _q(1, "Tính $2^3$.", ["$6$", "$8$", "$9$", "$10$"])
    asyncio.run(first.solve([question], []))
    assert inner.read == 1 and inner.solved == [[1]]

    again = FrugalProvider(inner, None, [], [], cache=cache)  # the same file imported again, another day
    assert [q.content for q in asyncio.run(again.extract(pages))] == ["Gemini đọc"]
    renumbered = _q(7, "Tính $2^3$.", ["$6$", "$8$", "$9$", "$10$"])  # same question, another number
    answers = asyncio.run(again.solve([renumbered], []))
    assert [(a.number, a.correct_label) for a in answers] == [(7, "C")]
    assert inner.read == 1 and inner.solved == [[1]] and again.cached == 3  # 2 pages + 1 question


def test_any_openai_compatible_provider_is_declared_in_env(monkeypatch):
    from app.config import Settings

    for key, value in {
        "AI_PROVIDERS": "azure,broken",
        "AZURE_BASE_URL": "https://school.openai.azure.com/openai/v1",
        "AZURE_API_KEY": "secret",
        "AZURE_MODELS": "gpt-4o-mini",
        "AZURE_VISION_MODELS": "gpt-4o-mini",
        "AZURE_AUTH": "api-key",
        "BROKEN_BASE_URL": "https://nowhere",  # no key, no models: ignored
    }.items():
        monkeypatch.setenv(key, value)
    providers = Settings().extra_providers
    assert [(p.name, p.models, p.vision_models) for p in providers] == [("azure", ["gpt-4o-mini"], ["gpt-4o-mini"])]
    assert providers[0].headers["api-key"] == "secret" and "Authorization" not in providers[0].headers


def test_known_free_providers_need_only_a_key(monkeypatch):
    from app.config import Settings

    for key, value in {
        "AI_PROVIDERS": "mistral,cerebras,cloudflare,sambanova",
        "MISTRAL_API_KEY": "m",
        "CEREBRAS_API_KEY": "c",
        "CEREBRAS_MODELS": "qwen-3-235b",  # overrides the preset
        "CLOUDFLARE_API_KEY": "cf",
        "CLOUDFLARE_ACCOUNT_ID": "acc1",
        # sambanova: no key, so not used
    }.items():
        monkeypatch.setenv(key, value)
    providers = {p.name: p for p in Settings().extra_providers}
    assert sorted(providers) == ["cerebras", "cloudflare", "mistral"]
    assert providers["mistral"].base_url == "https://api.mistral.ai/v1" and not providers["mistral"].vision_models
    assert providers["cerebras"].models == ["qwen-3-235b"]
    assert "/accounts/acc1/" in providers["cloudflare"].base_url
    assert providers["mistral"].headers["Authorization"] == "Bearer m"


def test_a_model_the_provider_no_longer_serves_is_skipped(monkeypatch):
    from app.ai.frugal import ChatModel

    async def post(self, url, **kwargs):
        return httpx.Response(404, text='{"error": "model not found"}', request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    model = ChatModel("mistral", "http://x", "k", "old-model")
    import pytest

    with pytest.raises(httpx.HTTPError):
        asyncio.run(model.ask_json("s", "u"))
    assert model.gone
