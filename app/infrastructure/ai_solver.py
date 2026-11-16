"""Shared AI solver: answer, worked solution, difficulty and topic of one question (V11.3, used by A02/A03/C04).

It sits in `app.infrastructure` so every module may call it without crossing a module boundary, and it imports no
module itself: the caller hands it the taxonomy nodes it may choose from (`TaxonomyHint`, e.g. from
`app.modules.taxonomy.public.list_taxonomy_hints`) and, optionally, a numeric cross-check (e.g.
`app.modules.quality.public.verify_numeric_answer`, which evaluates an expression in the isolated SymPy sandbox).

Providers come from `app.config.Settings`, tried in order: Gemini (every key of `gemini_api_key(s)`, every model of
`gemini_models`), Groq (every key of `groq_api_key(s)`), OpenRouter. A key that answers 401/403/429/5xx is skipped
for the next one (rotation); the first well-formed JSON answer wins. Every call has a timeout.

Without a key, or when every provider fails, the solver does not crash and does not invent an answer:
* the topic falls back to a keyword match of the stem against the nodes' names, codes and aliases;
* an answer already given (`existing_answer`, e.g. the key printed in the PDF) is kept when it is valid;
* otherwise `answer_schema` is None and `unresolved_reason` says why, so the caller can refuse the question
  (it never becomes a question without an answer by accident).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

ITEM_TYPES_WITH_KEY = frozenset({"SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC"})
DIFFICULTY_LEVELS = ("NB", "TH", "VD", "VDC")
DEFAULT_DIFFICULTY = "TH"
DEFAULT_TIMEOUT_SECONDS = 30.0
_RETRYABLE = frozenset({401, 403, 404, 408, 409, 429, 500, 502, 503, 504})
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?(?:/\d+)?")
_CHOICE = re.compile(r"(?:(?:DAP AN|PHUONG AN|CHON)\s*:?\s*)?([A-H])\s*[.):]?")
_TRUE_MARKS = frozenset({"Đ", "D", "T", "1", "TRUE", "ĐÚNG", "DUNG"})
_FALSE_MARKS = frozenset({"S", "F", "0", "FALSE", "SAI"})


@dataclass(frozen=True, slots=True)
class TaxonomyHint:
    """A topic the solver may choose: its id, code, display name and optional aliases (keyword fallback)."""

    id: uuid.UUID
    code: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SolveRequest:
    stem_text: str
    item_type: str
    options: Sequence[tuple[str, str]] = ()  # (key, text): A..D for SINGLE_CHOICE, a..d for CLUSTER_TRUE_FALSE
    existing_answer: Any = None  # the key of the source (PDF answer table, crawled page), when there is one
    taxonomy_nodes: Sequence[TaxonomyHint] = ()
    max_points: float = 1.0  # for an ESSAY rubric


@dataclass(frozen=True, slots=True)
class SolveResult:
    answer_schema: dict[str, Any] | None  # the C01 answer schema of the item type, None when unresolved
    solved_answer: Any  # "B", {"a": True, ...}, "2.5", or the ESSAY rubric text
    solution_steps: str | None
    difficulty_level: str
    taxonomy_node_id: uuid.UUID | None
    answer_source: str  # EXISTING | AI | NONE
    topic_source: str  # AI | KEYWORD | NONE
    provider: str | None = None
    numeric_check: str | None = None  # MATCH | MISMATCH | <error code> | None (not checked)
    answer_conflict: bool = False  # the AI disagreed with the source key (the source key is kept)
    unresolved_reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.answer_schema is not None


class NumericChecker(Protocol):
    """`quality.public.verify_numeric_answer`'s shape: (expression, source answer) -> object with `.matches`."""

    def __call__(self, expr: str, source_answer: str) -> Any: ...


class AiProviderError(RuntimeError):
    """Every configured provider failed (or none is configured)."""


# --- normalisation (pure) ------------------------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", _strip_accents(text).lower()) if len(w) >= 3}


def normalize_numeric(value: Any) -> str | None:
    """'2,5' / 2.5 / ' -3 ' -> '2.5' / '-3'; None when it is not one number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        text = repr(value) if isinstance(value, float) else str(value)
    else:
        text = str(value).strip()
    match = _NUMBER.fullmatch(text.replace(" ", ""))
    return None if match is None else match.group(0).replace(",", ".")


def normalize_true_false(value: Any) -> dict[str, bool] | None:
    """'ĐSĐS' / 'TFTF' / ['Đ','S',..] / {'a': True, ...} -> {'a': True, 'b': False, ...}; None when not 4 marks."""
    if isinstance(value, Mapping):
        out: dict[str, bool] = {}
        for key in "abcd":
            raw = value.get(key, value.get(key.upper()))
            if isinstance(raw, bool):
                out[key] = raw
            elif isinstance(raw, str) and raw.strip().upper() in _TRUE_MARKS | _FALSE_MARKS:
                out[key] = raw.strip().upper() in _TRUE_MARKS
            else:
                return None
        return out
    if isinstance(value, (list, tuple)):
        marks = [str(v).strip().upper() for v in value]
    elif isinstance(value, str):
        compact = re.sub(r"[\s,;.\-:/|()]+", "", value.strip().upper())
        labelled = re.fullmatch(r"A(.)B(.)C(.)D(.)", compact)  # "a) Đ b) S c) Đ d) S" -> "ĐSĐS"
        marks = list(labelled.groups()) if labelled else list(compact)
    else:
        return None
    if len(marks) != 4 or any(m not in _TRUE_MARKS | _FALSE_MARKS for m in marks):
        return None
    return {key: mark in _TRUE_MARKS for key, mark in zip("abcd", marks, strict=True)}


def answer_schema_for(
    item_type: str, answer: Any, *, options: Sequence[tuple[str, str]] = (), max_points: float = 1.0
) -> dict[str, Any] | None:
    """The C01 answer schema of `answer` for the item type, or None when the answer does not fit it."""
    if answer is None:
        return None
    if item_type == "SINGLE_CHOICE":
        keys = {k.upper() for k, _ in options} or {"A", "B", "C", "D"}
        match = _CHOICE.fullmatch(_strip_accents(str(answer)).strip().upper())
        letter = None if match is None else match.group(1)
        return {"correct_option": letter} if letter in keys else None
    if item_type == "CLUSTER_TRUE_FALSE":
        statements = normalize_true_false(answer)
        return None if statements is None else {"statements": statements}
    if item_type == "SHORT_NUMERIC":
        number = normalize_numeric(answer)
        return None if number is None else {"value": number}
    if item_type == "ESSAY":
        rubric = str(answer).strip()
        return {"rubric": rubric, "max_points": max(0.25, float(max_points))} if rubric else None
    return None


def keyword_topic(stem_text: str, nodes: Sequence[TaxonomyHint]) -> TaxonomyHint | None:
    """The node whose name / code / aliases share the most words with the stem (None when none shares one)."""
    stem = _words(stem_text)
    best: tuple[float, TaxonomyHint] | None = None
    for node in nodes:
        vocab = _words(" ".join((node.name, node.code.replace("_", " "), *node.aliases)))
        if not vocab:
            continue
        overlap = len(stem & vocab)
        if overlap == 0:
            continue
        score = overlap / len(vocab)
        if best is None or score > best[0]:
            best = (score, node)
    return None if best is None else best[1]


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    fence = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if fence is None:
        return None
    try:
        parsed = json.loads(fence.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_prompt(req: SolveRequest) -> str:
    options = "\n".join(f"{k}. {t}" for k, t in req.options) or "(không có phương án)"
    topics = "\n".join(f"- {n.id} | {n.code} | {n.name}" for n in req.taxonomy_nodes[:200]) or "(không có danh sách)"
    shape = {
        "SINGLE_CHOICE": '"answer": "A"|"B"|"C"|"D"',
        "CLUSTER_TRUE_FALSE": '"answer": {"a": true, "b": false, "c": true, "d": false}',
        "SHORT_NUMERIC": (
            '"answer": "<một số, dấu chấm thập phân>", "numeric_expression": "<biểu thức SymPy cho ra số đó>"'
        ),
        "ESSAY": '"answer": "<hướng dẫn chấm ngắn gọn>"',
    }.get(req.item_type, '"answer": null')
    return (
        "Bạn là giáo viên THPT Việt Nam. Giải câu hỏi sau, chỉ trả về MỘT đối tượng JSON, "
        "không giải thích ngoài JSON.\n"
        f"Loại câu: {req.item_type}\nĐề bài:\n{req.stem_text}\nPhương án:\n{options}\n"
        f"Danh sách chủ đề (id | mã | tên):\n{topics}\n"
        "JSON cần có: {"
        f"{shape}, "
        '"solution_steps": "<lời giải chi tiết>", "difficulty_level": "NB"|"TH"|"VD"|"VDC", '
        '"taxonomy_node_id": "<id trong danh sách hoặc null>"}'
    )


# --- the solver ------------------------------------------------------------------------------------------------------


class AiSolver:
    """Solve questions with the configured providers; `transport` lets tests (or a proxy) replace the network."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        numeric_checker: NumericChecker | Callable[[str, str], Any] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport
        self.numeric_checker = numeric_checker
        self.timeout = timeout_seconds

    @property
    def has_provider(self) -> bool:
        s = self.settings
        return bool(s.gemini_api_key_list or s.groq_api_key_list or (s.openrouter_api_key and s.openrouter_model))

    async def _gemini(self, client: httpx.AsyncClient, prompt: str) -> tuple[str, str]:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        last: str = "no Gemini key"
        for model in self.settings.gemini_model_list or ["gemini-3.5-flash-lite"]:
            for key in self.settings.gemini_api_key_list:
                response = await client.post(_GEMINI_URL.format(model=model), params={"key": key}, json=body)
                if response.status_code in _RETRYABLE:
                    last = f"gemini {model}: HTTP {response.status_code}"
                    continue
                response.raise_for_status()
                parts = response.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts), f"gemini:{model}"
        raise AiProviderError(last)

    async def _openai_compatible(
        self, client: httpx.AsyncClient, *, name: str, base_url: str, keys: Sequence[str], model: str, prompt: str
    ) -> tuple[str, str]:
        last = f"no {name} key"
        for key in keys:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "temperature": 0.1, "messages": [{"role": "user", "content": prompt}]},
            )
            if response.status_code in _RETRYABLE:
                last = f"{name}: HTTP {response.status_code}"
                continue
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"]), f"{name}:{model}"
        raise AiProviderError(last)

    async def _ask(self, prompt: str) -> tuple[dict[str, Any], str]:
        s = self.settings
        failures: list[str] = []
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
            attempts: list[Callable[[], Any]] = []
            if s.gemini_api_key_list:
                attempts.append(lambda: self._gemini(client, prompt))
            if s.groq_api_key_list:
                attempts.append(
                    lambda: self._openai_compatible(
                        client, name="groq", base_url=s.groq_base_url, keys=s.groq_api_key_list, model=s.groq_model,
                        prompt=prompt,
                    )
                )  # fmt: skip
            if s.openrouter_api_key and s.openrouter_model:
                attempts.append(
                    lambda: self._openai_compatible(
                        client, name="openrouter", base_url=s.openrouter_base_url, keys=[s.openrouter_api_key],
                        model=s.openrouter_model.split(",")[0].strip(), prompt=prompt,
                    )
                )  # fmt: skip
            for attempt in attempts:
                try:
                    text, provider = await attempt()
                except (AiProviderError, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                    failures.append(f"{type(exc).__name__}: {exc}"[:200])
                    continue
                parsed = _parse_json_object(text)
                if parsed is None:
                    failures.append(f"{provider}: answer is not a JSON object")
                    continue
                return parsed, provider
        raise AiProviderError("; ".join(failures) or "no AI provider is configured")

    def _check_numeric(self, parsed: Mapping[str, Any], value: str | None) -> str | None:
        expr = parsed.get("numeric_expression")
        if self.numeric_checker is None or value is None or not isinstance(expr, str) or not expr.strip():
            return None
        try:
            check = self.numeric_checker(expr, value)
        except Exception as exc:  # the checker is the caller's: a failure is reported, never raised
            return f"CHECK_ERROR:{type(exc).__name__}"
        if getattr(check, "matches", False):
            return "MATCH"
        return str(getattr(check, "error", None) or "MISMATCH")

    async def solve(self, req: SolveRequest) -> SolveResult:
        nodes_by_id = {str(n.id): n for n in req.taxonomy_nodes}
        existing = answer_schema_for(req.item_type, req.existing_answer, options=req.options, max_points=req.max_points)
        warnings: list[str] = []
        if req.existing_answer not in (None, "") and existing is None:
            warnings.append("SOURCE_ANSWER_UNPARSEABLE")

        parsed: dict[str, Any] | None = None
        provider: str | None = None
        ai_error: str | None = None
        if self.has_provider:
            try:
                parsed, provider = await self._ask(build_prompt(req))
            except AiProviderError as exc:
                ai_error = str(exc)
                log.warning("ai_solver: every provider failed: %s", ai_error)
        else:
            ai_error = "no AI provider is configured"

        ai_schema = None
        numeric_check = None
        if parsed is not None:
            ai_schema = answer_schema_for(
                req.item_type, parsed.get("answer"), options=req.options, max_points=req.max_points
            )
            if req.item_type == "SHORT_NUMERIC" and ai_schema is not None:
                numeric_check = self._check_numeric(parsed, ai_schema["value"])
                if numeric_check not in (None, "MATCH"):
                    warnings.append(f"NUMERIC_CHECK_{numeric_check}")
                    ai_schema = None  # an AI number its own expression does not reproduce is not trusted

        conflict = existing is not None and ai_schema is not None and existing != ai_schema
        if conflict:
            warnings.append("AI_DISAGREES_WITH_SOURCE_KEY")
        if existing is not None:
            schema, answer_source = existing, "EXISTING"
        elif ai_schema is not None:
            schema, answer_source = ai_schema, "AI"
        else:
            schema, answer_source = None, "NONE"

        topic_id: uuid.UUID | None = None
        topic_source = "NONE"
        if parsed is not None and str(parsed.get("taxonomy_node_id")) in nodes_by_id:
            topic_id, topic_source = nodes_by_id[str(parsed["taxonomy_node_id"])].id, "AI"
        else:
            guess = keyword_topic(req.stem_text, req.taxonomy_nodes)
            if guess is not None:
                topic_id, topic_source = guess.id, "KEYWORD"

        difficulty = DEFAULT_DIFFICULTY
        if parsed is not None and str(parsed.get("difficulty_level", "")).upper() in DIFFICULTY_LEVELS:
            difficulty = str(parsed["difficulty_level"]).upper()
        steps = parsed.get("solution_steps") if parsed is not None else None

        unresolved = None
        if schema is None and req.item_type in ITEM_TYPES_WITH_KEY | {"ESSAY"}:
            unresolved = "NO_ANSWER: the source has no usable key and the AI gave no valid answer" + (
                f" ({ai_error})" if ai_error else ""
            )
        solved: Any = None
        if schema is not None:
            solved = (
                schema.get("correct_option") or schema.get("statements") or schema.get("value") or schema.get("rubric")
            )
        return SolveResult(
            answer_schema=schema,
            solved_answer=solved,
            solution_steps=str(steps).strip() if isinstance(steps, str) and steps.strip() else None,
            difficulty_level=difficulty,
            taxonomy_node_id=topic_id,
            answer_source=answer_source,
            topic_source=topic_source,
            provider=provider,
            numeric_check=numeric_check,
            answer_conflict=conflict,
            unresolved_reason=unresolved,
            warnings=tuple(warnings),
        )
