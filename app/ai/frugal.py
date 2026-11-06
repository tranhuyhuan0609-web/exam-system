"""Spend as little Gemini quota as possible on an AI import.

Reading the pages needs a model that sees images (Gemini). Solving does not, except for questions with a figure:
1. a question already in the bank — typically the same question in another code of an official paper, where
   only the order of questions and options changes — takes its answer and solution from the bank, no AI call;
2. other questions without a figure are solved by text models on free tiers (Groq, OpenRouter), within their
   daily budgets (ai_usage table, as for the crawler);
3. only questions with a figure, or that the text models could not solve, are sent to Gemini.
"""

import json
import logging
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import SOLVE_PROMPT, AIProvider, EssayItem, FigureImage, PageImage
from app.ai.schemas import EssayGrade, ExtractedQuestion, Solution, SolveResult
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


class TextSolver:
    """Solving on an OpenAI-compatible chat API (Groq, OpenRouter): text only, JSON as in Gemini's schema."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str, usage=None, daily_limit: int = 0):
        self.name, self.model, self.daily_limit, self.usage = name, model, daily_limit, usage
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def available(self) -> bool:
        return not (self.daily_limit and self.usage and await self.usage.used_today(self.name) >= self.daily_limit)

    async def solve(self, questions: list[ExtractedQuestion]) -> list[Solution]:
        payload = [q.model_dump(include={"number", "type", "content", "choices"}, mode="json") for q in questions]
        schema = json.dumps(SolveResult.model_json_schema(), ensure_ascii=False)
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SOLVE_PROMPT + "\nTrả về DUY NHẤT một JSON đúng schema: " + schema},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(self._url, headers=self._headers, json=body)
        if self.usage:
            await self.usage.record(self.name, len(questions))
        response.raise_for_status()
        result = SolveResult.model_validate_json(response.json()["choices"][0]["message"]["content"] or "{}")
        for sol in result.solutions:
            sol.step_by_step_solution = repair_latex(sol.step_by_step_solution)
            sol.final_value = repair_latex(sol.final_value)
            sol.short_answer = repair_latex(sol.short_answer)
        return result.solutions


class FrugalProvider:
    """An AIProvider that reads pages with `inner` (Gemini) and solves bank first, text models second."""

    def __init__(self, inner: AIProvider, lookup: Lookup | None = None, text_solvers: list[TextSolver] = ()):
        self.inner, self.lookup, self.text_solvers = inner, lookup, list(text_solvers)
        self._used: set[str] = set()
        self.reused = 0  # questions answered from the bank (for the job's stage line)

    @property
    def models_used(self) -> set[str]:
        return self.inner.models_used | self._used

    async def extract(self, pages: list[PageImage]) -> list[ExtractedQuestion]:
        return await self.inner.extract(pages)

    async def grade_essays(self, items: list[EssayItem]) -> list[EssayGrade]:
        return await self.inner.grade_essays(items)

    async def solve(self, questions: list[ExtractedQuestion], figures: list[FigureImage]) -> list[Solution]:
        solved: dict[int, Solution] = await self.lookup(questions) if self.lookup else {}
        if solved:
            self.reused += len(solved)
            self._used.add("ngân hàng câu hỏi")
        with_figure = {f.question_number for f in figures}
        text_only = [i for i, q in enumerate(questions) if i not in solved and q.number not in with_figure]
        for solver in self.text_solvers:
            if not text_only or not await solver.available():
                continue
            try:
                answers = await solver.solve([questions[i] for i in text_only])
            except (httpx.HTTPError, ValueError, KeyError) as exc:  # out of quota, bad JSON: next one
                log.warning("%s could not solve: %s", solver.name, exc)
                continue
            by_number = {a.number: a for a in answers}
            for i in text_only:
                if (answer := by_number.get(questions[i].number)) is not None:
                    solved[i] = answer
            self._used.add(f"{solver.name}:{solver.model}")
            text_only = [i for i in text_only if i not in solved]
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
        return [solved[i] for i in range(len(questions)) if i in solved]
