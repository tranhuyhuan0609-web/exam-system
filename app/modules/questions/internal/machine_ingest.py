"""C01 intake of questions found by a machine (A02 crawler, A03 Drive sync, a teacher's PDF in C04).

One path for all of them: the raw question (type, stem, option texts, the key the source printed, if any) is solved
by `app.infrastructure.ai_solver` (answer, worked solution, difficulty, topic), and stored through `ingest_question`
(a DRAFT, deduplicated on the content hash). A question the solver could not give an answer to is not stored: the
reason is returned, so the caller reports it instead of storing a question without a key.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.ai_solver import AiSolver, SolveRequest, SolveResult, TaxonomyHint
from app.modules.questions.internal.service import (
    IngestedQuestion,
    OptionInput,
    QuestionValidationFailedError,
    ingest_question,
)
from app.modules.taxonomy.public import list_taxonomy_hints

MACHINE_ITEM_TYPES = frozenset({"SINGLE_CHOICE", "CLUSTER_TRUE_FALSE", "SHORT_NUMERIC", "ESSAY"})
_CHOICE_KEYS = "ABCDEFGH"


@dataclass(frozen=True, slots=True)
class RawQuestion:
    item_type: str
    stem_text: str
    options: Sequence[str] = ()
    source_answer: Any = None
    points: float = 1.0
    source_label: str | None = None  # "Câu 3", a URL fragment: only for reports


@dataclass(frozen=True, slots=True)
class MachineIngestOutcome:
    raw: RawQuestion
    solve: SolveResult | None
    ingested: IngestedQuestion | None
    skipped_reason: str | None

    @property
    def stored(self) -> bool:
        return self.ingested is not None


def keyed_options(item_type: str, options: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """A..H for a SINGLE_CHOICE, a..d for a CLUSTER_TRUE_FALSE, nothing for the other types."""
    texts = [str(o).strip() for o in options if str(o).strip()]
    if item_type == "SINGLE_CHOICE":
        return tuple(zip(_CHOICE_KEYS, texts[: len(_CHOICE_KEYS)], strict=False))
    if item_type == "CLUSTER_TRUE_FALSE":
        return tuple(zip("abcd", texts[:4], strict=False))
    return ()


async def load_taxonomy_hints(session: AsyncSession, *, root_path: str | None = None) -> tuple[TaxonomyHint, ...]:
    """The topics visible to the tenant, in the shape the solver takes."""
    return tuple(
        TaxonomyHint(id=h.id, code=h.code, name=h.name, aliases=h.aliases)
        for h in await list_taxonomy_hints(session, root_path=root_path)
    )


def default_numeric_checker() -> Any:
    """`quality.public.verify_numeric_answer` (the SymPy sandbox), imported late: quality imports this module."""
    from app.modules.quality.public import verify_numeric_answer

    return verify_numeric_answer


@dataclass(frozen=True, slots=True)
class PreparedQuestion:
    """A raw question after the solver (no database touched): ready to store, or the reason it cannot be."""

    raw: RawQuestion
    item_type: str
    options: tuple[tuple[str, str], ...]
    solve: SolveResult | None
    skipped_reason: str | None


DEFAULT_SOLVE_CONCURRENCY = 4


async def solve_raw(solver: AiSolver, raw: RawQuestion, taxonomy_hints: Sequence[TaxonomyHint]) -> PreparedQuestion:
    """Validate the shape of a raw question and solve it. Pure of the database: call it outside any transaction."""
    item_type = raw.item_type.upper()
    options = keyed_options(item_type, raw.options)
    stem = raw.stem_text.strip()
    reason: str | None = None
    if item_type not in MACHINE_ITEM_TYPES:
        reason = f"UNSUPPORTED_ITEM_TYPE:{item_type}"
    elif not stem:
        reason = "EMPTY_STEM"
    elif item_type == "SINGLE_CHOICE" and len(options) < 2:
        reason = "SINGLE_CHOICE_NEEDS_TWO_OPTIONS"
    elif item_type == "CLUSTER_TRUE_FALSE" and len(options) != 4:
        reason = "CLUSTER_TRUE_FALSE_NEEDS_FOUR_STATEMENTS"
    if reason is not None:
        return PreparedQuestion(raw, item_type, options, None, reason)
    result = await solver.solve(
        SolveRequest(
            stem_text=stem,
            item_type=item_type,
            options=options,
            existing_answer=raw.source_answer,
            taxonomy_nodes=taxonomy_hints,
            max_points=raw.points,
        )
    )
    return PreparedQuestion(
        raw, item_type, options, result, result.unresolved_reason if result.answer_schema is None else None
    )


async def solve_many(
    solver: AiSolver,
    raws: Sequence[RawQuestion],
    taxonomy_hints: Sequence[TaxonomyHint],
    *,
    concurrency: int = DEFAULT_SOLVE_CONCURRENCY,
) -> list[PreparedQuestion]:
    """`solve_raw` over many questions, at most `concurrency` at a time, in order. Outside any transaction."""
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(raw: RawQuestion) -> PreparedQuestion:
        async with gate:
            return await solve_raw(solver, raw, taxonomy_hints)

    return list(await asyncio.gather(*(one(r) for r in raws)))


async def store_prepared(
    session: AsyncSession,
    *,
    prepared: PreparedQuestion,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    stable_code: str,
    rights_status: str = "PENDING_REVIEW",
    exam_framework_tags: Sequence[str] = ("SGK_KNTT",),
    provenance: dict[str, Any] | None = None,
) -> MachineIngestOutcome:
    """Store a solved question as a DRAFT of the tenant's bank (or find the identical one): the short DB step."""
    result = prepared.solve
    if prepared.skipped_reason is not None or result is None or result.answer_schema is None:
        return MachineIngestOutcome(prepared.raw, result, None, prepared.skipped_reason or "NO_ANSWER")
    rationale: dict[str, Any] = {
        "solution_steps": result.solution_steps,
        "answer_source": result.answer_source,
        "topic_source": result.topic_source,
        "ai_solved": result.answer_source == "AI",
        "solver_provider": result.provider,
        "numeric_check": result.numeric_check,
        "warnings": list(result.warnings),
        **({"provenance": provenance} if provenance else {}),
    }
    try:
        ingested = await ingest_question(
            session,
            tenant_id=tenant_id,
            created_by=created_by,
            stable_code=stable_code,
            item_type=prepared.item_type,
            stem_text=prepared.raw.stem_text.strip(),
            answer_schema=result.answer_schema,
            options=tuple(OptionInput(key=k, text=t) for k, t in prepared.options),
            difficulty_level=result.difficulty_level,
            rationale=rationale,
            primary_taxonomy_node_id=result.taxonomy_node_id,
            exam_framework_tags=exam_framework_tags,
            rights_status=rights_status,
        )
    except QuestionValidationFailedError as exc:
        return MachineIngestOutcome(prepared.raw, result, None, f"INVALID_QUESTION:{exc.code}")
    return MachineIngestOutcome(prepared.raw, result, ingested, None)


async def solve_and_ingest(
    session: AsyncSession,
    *,
    solver: AiSolver,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    stable_code: str,
    raw: RawQuestion,
    taxonomy_hints: Sequence[TaxonomyHint],
    rights_status: str = "PENDING_REVIEW",
    exam_framework_tags: Sequence[str] = ("SGK_KNTT",),
    provenance: dict[str, Any] | None = None,
) -> MachineIngestOutcome:
    """Solve then store, in one call (a script or test with no transaction to protect; routes use the two steps)."""
    prepared = await solve_raw(solver, raw, taxonomy_hints)
    return await store_prepared(
        session, prepared=prepared, tenant_id=tenant_id, created_by=created_by, stable_code=stable_code,
        rights_status=rights_status, exam_framework_tags=exam_framework_tags, provenance=provenance,
    )  # fmt: skip
