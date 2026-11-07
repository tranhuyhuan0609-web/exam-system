"""Spend as little Gemini quota as possible on an AI import.

Reading the pages needs a model that sees images (Gemini). Solving does not, except for questions with a figure:
1. a question already in the bank — typically the same question in another code of an official paper, where
   only the order of questions and options changes — takes its answer and solution from the bank, no AI call;
2. other questions without a figure are solved by text models on free tiers (Groq, OpenRouter), within their
   daily budgets (ai_usage table, as for the crawler);
3. only questions with a figure, or that the text models could not solve, are sent to Gemini.
"""

import asyncio
import base64
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.cache import digest
from app.ai.provider import EXTRACT_PROMPT, SOLVE_PROMPT, AIProvider, EssayItem, FigureImage, PageImage, _json_block
from app.ai.schemas import EssayGrade, ExtractedQuestion, ExtractionResult, Solution, SolveResult
from app.domain.latex import repair_latex
from app.domain.question_hash import normalize_text
from app.models import Question, QuestionType

log = logging.getLogger(__name__)
LABELS = "ABCDEF"
Lookup = Callable[[list[ExtractedQuestion]], Awaitable[dict[int, Solution]]]  # position in the batch -> solution


def _key(kind: str, content: str) -> tuple[str, str]:
    return str(kind), normalize_text(content)


def _from_bank(q: ExtractedQuestion, bank: Question) -> Solution | None:
    """The bank's answer, mapped onto this copy's order of options; None if the options are not the same set."""
    mine = [normalize_text(c.content) for c in q.choices]
    theirs = [normalize_text(c.get("content", "")) for c in bank.choices or []]
    if sorted(mine) != sorted(theirs):
        return None
    common = {
        "number": q.number,
        "step_by_step_solution": bank.explanation or "",
        "final_value": "",
        "matches_an_option": True,
        "difficulty": bank.difficulty.value,
    }
    truth = {normalize_text(c.get("content", "")): bool(c.get("is_correct")) for c in bank.choices or []}
    if q.type == QuestionType.MULTIPLE_CHOICE:
        index = next((i for i, text in enumerate(mine) if truth.get(text)), None)
        if index is None:
            return None
        return Solution(**common, correct_label=LABELS[index], short_answer=None)
    if q.type == QuestionType.TRUE_FALSE:
        return Solution(**common, correct_label=None, statement_truths=[truth[t] for t in mine], short_answer=None)
    if q.type == QuestionType.SHORT_ANSWER and bank.accepted_answers:
        return Solution(**common, correct_label=None, short_answer=bank.accepted_answers[0])
    return None


async def bank_lookup(session: AsyncSession, root_id: int) -> Lookup:
    """Answers from the bank of this subject, matched on the question text and its set of options."""
    rows = await session.scalars(select(Question).where(Question.root_id == root_id, Question.is_verified))
    bank: dict[tuple[str, str], list[Question]] = {}
    for question in rows:
        bank.setdefault(_key(question.question_type.value, question.content), []).append(question)

    async def lookup(questions: list[ExtractedQuestion]) -> dict[int, Solution]:
        found = {}
        for position, q in enumerate(questions):
            for candidate in bank.get(_key(q.type, q.content), []):
                if solution := _from_bank(q, candidate):
                    found[position] = solution
                    break
        return found

    return lookup


class ChatModel:
    """A model on an OpenAI-compatible chat API (Groq, OpenRouter). Solves questions; with `vision`, also reads
    exam pages and figures. Calls are spaced (`min_interval`) to stay under per-minute limits, counted per
    provider and day (ai_usage), and a model that the provider no longer serves is skipped from then on."""

    _last_call: dict[str, float] = {}  # per provider: free tiers limit requests per minute

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        usage=None,
        daily_limit: int = 0,
        vision: bool = False,
        min_interval: float = 0.0,
        batch_size: int = 25,
        headers: dict[str, str] | None = None,  # Azure: {"api-key": …}; others: a Bearer token
    ):
        self.name, self.model, self.daily_limit, self.usage = name, model, daily_limit, usage
        self.vision, self.min_interval, self.gone = vision, min_interval, False
        self.batch_size = batch_size  # questions per call: free tiers also limit tokens per minute
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = headers or {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def available(self) -> bool:
        if self.gone:
            return False
        return not (self.daily_limit and self.usage and await self.usage.used_today(self.name) >= self.daily_limit)

    async def _ask(self, system: str, user: list[dict] | str, items: int) -> str:
        wait = self._last_call.get(self.name, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 2048,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if not self.vision:
            body["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=240) as client:
            response = await client.post(self._url, headers=self._headers, json=body)
            if response.status_code == 400 and "json_validate_failed" in response.text:
                # JSON mode rejects answers whose LaTeX backslashes are not JSON escapes: ask again without it
                body.pop("response_format", None)
                response = await client.post(self._url, headers=self._headers, json=body)
        self._last_call[self.name] = time.monotonic()
        if self.usage:
            await self.usage.record(self.name, items)
        if response.status_code == 404 or (response.status_code == 400 and "endpoint" in response.text.lower()):
            self.gone = True  # model removed (or renamed), or not allowed by the account's privacy settings
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.name}:{self.model} HTTP {response.status_code}: {response.text[:300]}",
                request=response.request,
                response=response,
            )
        return _loose_json(_json_block(response.json()["choices"][0]["message"]["content"] or "{}"))

    async def ask_json(self, system: str, user: str, items: int = 1) -> str:
        """Any JSON task (the caller validates the answer against its own schema)."""
        return await self._ask(system, user, items)

    async def extract(self, pages: list[PageImage]) -> list[ExtractedQuestion]:
        content: list[dict] = []
        for page in pages:
            label = f"TRANG {page.number}" + (" (chỉ tham khảo)" if page.context_only else "")
            content += [{"type": "text", "text": label}, _image(page.png)]
        schema = json.dumps(ExtractionResult.model_json_schema(), ensure_ascii=False)
        content.append({"type": "text", "text": EXTRACT_PROMPT})
        raw = await self._ask("Trả về DUY NHẤT một JSON đúng schema: " + schema, content, len(pages))
        questions = ExtractionResult.model_validate_json(raw).questions
        for q in questions:
            q.content = repair_latex(q.content)
            for c in q.choices:
                c.content = repair_latex(c.content)
        return questions

    async def solve(self, questions: list[ExtractedQuestion], figures: list[FigureImage] = ()) -> list[Solution]:
        payload = [q.model_dump(include={"number", "type", "content", "choices"}, mode="json") for q in questions]
        schema = json.dumps(SolveResult.model_json_schema(), ensure_ascii=False)
        user: list[dict] | str = json.dumps(payload, ensure_ascii=False)
        if figures:
            user = [{"type": "text", "text": user}]
            for fig in figures:
                user += [{"type": "text", "text": f"HÌNH CÂU {fig.question_number}"}, _image(fig.png)]
        system = SOLVE_PROMPT + "\nTrả về DUY NHẤT một JSON đúng schema: " + schema
        raw = await self._ask(system, user, len(questions))
        result = SolveResult.model_validate_json(raw)
        for sol in result.solutions:
            sol.step_by_step_solution = repair_latex(sol.step_by_step_solution)
            sol.final_value = repair_latex(sol.final_value)
            sol.short_answer = repair_latex(sol.short_answer)
        return result.solutions


TextSolver = ChatModel  # the name used before vision models were added


_ESCAPE = re.compile(r"\\(.)", re.S)  # read pairs left to right, so a correct "\\" is one unit


def _loose_json(raw: str) -> str:
    """Models often write LaTeX with single backslashes inside JSON strings ("\\sqrt"): JSON has no such escape,
    so the backslash is doubled. "\\frac" and the like decode to control characters; repair_latex restores them."""
    return _ESCAPE.sub(lambda m: m.group(0) if m.group(1) in '\\"/bfnrtu' else "\\\\" + m.group(1), raw)


def _image(png: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


class FrugalProvider:
    """An AIProvider that spends Gemini (`inner`) last.

    Pages: free vision models (`readers`) first, Gemini if they fail or find nothing.
    Solving: the bank first; questions without a figure to text models (`text_solvers`); questions with a figure
    to the vision models; whatever is left to Gemini.
    """

    def __init__(
        self,
        inner: AIProvider,
        lookup: Lookup | None = None,
        text_solvers: list[ChatModel] = (),
        readers: list[ChatModel] = (),
        cache=None,
    ):
        self.inner, self.lookup, self.cache = inner, lookup, cache
        self.text_solvers, self.readers = list(text_solvers), list(readers)
        self._used: set[str] = set()
        self.reused = 0  # questions answered from the bank (for the job's stage line)
        self.cached = 0  # pages and questions answered from the cache

    @property
    def models_used(self) -> set[str]:
        return self.inner.models_used | self._used

    async def extract(self, pages: list[PageImage]) -> list[ExtractedQuestion]:
        key = digest("extract", EXTRACT_PROMPT, *(f"{p.number}|{p.context_only}".encode() + p.png for p in pages))
        if self.cache and (hit := await self.cache.get(key)) is not None:
            self.cached += len(pages)
            self._used.add("bộ nhớ đệm")
            return [ExtractedQuestion.model_validate(q) for q in hit]
        questions = await self._extract(pages)
        if self.cache and questions:
            await self.cache.put(key, "extract", [q.model_dump(mode="json") for q in questions])
        return questions

    async def _extract(self, pages: list[PageImage]) -> list[ExtractedQuestion]:
        for reader in self.readers:
            if not await reader.available():
                continue
            try:
                questions = await reader.extract(pages)
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                log.warning("%s:%s could not read the pages: %s", reader.name, reader.model, exc)
                continue
            if questions:  # nothing found may just mean the model could not read it: ask the next one
                self._used.add(f"{reader.name}:{reader.model}")
                return questions
        return await self.inner.extract(pages)

    async def grade_essays(self, items: list[EssayItem]) -> list[EssayGrade]:
        return await self.inner.grade_essays(items)

    async def read_answer_key(self, pages: list[PageImage]):
        return await self.inner.read_answer_key(pages)  # the reference for every check: Gemini only

    async def _try(self, solvers: list[ChatModel], questions, todo: list[int], figures, solved: dict) -> list[int]:
        idx = 0
        while idx < len(solvers) and todo:
            first = solvers[idx]
            # Group consecutive solvers that share the same model (e.g., groq-1, groq-2, groq-3)
            pool = [first]
            idx += 1
            while idx < len(solvers) and solvers[idx].model == first.model:
                pool.append(solvers[idx])
                idx += 1
            active = [s for s in pool if await s.available()]
            if not active:
                continue
            batch_size = active[0].batch_size
            groups = [todo[start : start + batch_size] for start in range(0, len(todo), batch_size)]
            dead_solvers: set[int] = set()

            async def _solve_group(g_idx: int, group: list[int]) -> None:
                for attempt in range(len(active)):
                    s_pos = (g_idx + attempt) % len(active)
                    if s_pos in dead_solvers:
                        continue
                    solver = active[s_pos]
                    numbers = {questions[i].number for i in group}
                    try:
                        answers = await solver.solve(
                            [questions[i] for i in group], [f for f in figures if f.question_number in numbers]
                        )
                    except httpx.HTTPError as exc:  # rate limit / quota / HTTP error on this key: try next key in pool
                        log.warning("%s:%s HTTP error: %s", solver.name, solver.model, exc)
                        dead_solvers.add(s_pos)
                        continue
                    except (ValueError, KeyError, IndexError) as exc:  # model output format issue: don't retry same model
                        log.warning("%s:%s could not solve: %s", solver.name, solver.model, exc)
                        return
                    by_number = {a.number: a for a in answers}
                    for i in group:
                        if (answer := by_number.get(questions[i].number)) is not None:
                            solved[i] = answer
                    self._used.add(f"{solver.name}:{solver.model}")
                    return

            # Run up to len(active) batches in parallel across the key pool
            sem = asyncio.Semaphore(len(active))

            async def _bounded(g_idx: int, group: list[int]) -> None:
                async with sem:
                    await _solve_group(g_idx, group)

            await asyncio.gather(*(_bounded(g_idx, g) for g_idx, g in enumerate(groups)))
            todo = [i for i in todo if i not in solved]
        return todo

    def _solve_key(self, q: ExtractedQuestion, figures: list[FigureImage]) -> str:
        figure = next((f.png for f in figures if f.question_number == q.number), b"")
        payload = q.model_dump(include={"type", "content", "choices"}, mode="json")
        return digest("solve", SOLVE_PROMPT, json.dumps(payload, ensure_ascii=False, sort_keys=True), figure)

    async def solve(self, questions: list[ExtractedQuestion], figures: list[FigureImage]) -> list[Solution]:
        solved: dict[int, Solution] = await self.lookup(questions) if self.lookup else {}
        if solved:
            self.reused += len(solved)
            self._used.add("ngân hàng câu hỏi")
        keys = {i: self._solve_key(q, figures) for i, q in enumerate(questions)} if self.cache else {}
        for i, key in keys.items():
            if i not in solved and (hit := await self.cache.get(key)) is not None:
                solved[i] = Solution.model_validate({**hit, "number": questions[i].number})
                self.cached += 1
                self._used.add("bộ nhớ đệm")
        fresh = set(range(len(questions))) - set(solved)
        with_figure = {f.question_number for f in figures}
        text_only = [i for i, q in enumerate(questions) if i not in solved and q.number not in with_figure]
        await self._try(self.text_solvers, questions, text_only, [], solved)
        drawn = [i for i, q in enumerate(questions) if i not in solved and q.number in with_figure]
        await self._try(self.readers, questions, drawn, figures, solved)
        rest = [i for i in range(len(questions)) if i not in solved]
        if rest:
            numbers = {questions[i].number for i in rest}
            answers = await self.inner.solve(
                [questions[i] for i in rest], [f for f in figures if f.question_number in numbers]
            )
            by_number = {a.number: a for a in answers}
            complete = len(answers) == len(rest)  # by order only when nothing is missing
            for position, i in enumerate(rest):
                answer = by_number.get(questions[i].number) or (answers[position] if complete else None)
                if answer is not None:
                    solved[i] = answer
        for i in fresh & set(solved) if self.cache else ():  # remembered for the next time the same question comes
            await self.cache.put(keys[i], "solve", solved[i].model_dump(mode="json"))
        return [solved[i] for i in range(len(questions)) if i in solved]
