"""A07 extraction service: split a study guide into candidates, read, split and merge candidates (tenant RLS).

`normalize_extraction_run` feeds the pages of an extraction run (A06 `extracted_pages`, or pages the caller
passes) to the pure splitter: the theory, method and formula blocks become ONE `content_candidates` row and
every exercise a `question_candidates` row (hashes from the A08 hashing core, page/bbox anchors in the payload
until `provenance_records` exists). A three-column scoring table, when given, is matched to the questions by label.
Each new question candidate announces `question.candidate.created` through the outbox, in the same transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.dedup.public import compute_question_hashes
from app.modules.extraction.internal.models import ContentCandidate, QuestionCandidate
from app.modules.extraction.internal.rubric_table import (
    RubricParseReport,
    TableCellSpan,
    parse_three_column_rubric,
)
from app.modules.extraction.internal.splitter import (
    PageSegmentInput,
    ProvenanceAnchor,
    QuestionCandidateItem,
    split_study_guide_mixed,
)
from app.modules.outbox.public import enqueue

CANDIDATE_CREATED_EVENT = "question.candidate.created"
MAX_PARTS = 20
MAX_MERGE = 20


class CandidateNotFoundError(LookupError):
    """The run or candidate does not exist or belongs to another tenant (same answer)."""


class CandidateConflictError(ValueError):
    """A state conflict; `code` is ALREADY_NORMALIZED, NOT_PENDING, DIFFERENT_RUN or NOTHING_TO_EXTRACT."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class QuestionCandidateRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID | None
    parent_candidate_id: uuid.UUID | None
    acquisition_method: str
    item_type: str
    payload: dict[str, Any]
    content_hash: str
    structural_hash: str
    extraction_confidence: Decimal | None
    verification_status: str
    rights_status: str
    candidate_status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ContentCandidateRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID
    title: str
    blocks: tuple[dict[str, Any], ...]
    verification_status: str
    rights_status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NormalizeResult:
    content: ContentCandidateRecord | None
    questions: tuple[QuestionCandidateRecord, ...]
    unassigned_exercise_text: tuple[str, ...]
    warnings: tuple[str, ...]
    rubric_points_total: Decimal | None


def _question(row: QuestionCandidate) -> QuestionCandidateRecord:
    values = {f: getattr(row, f) for f in QuestionCandidateRecord.__dataclass_fields__ if f != "payload"}
    return QuestionCandidateRecord(payload=dict(row.payload_json), **values)


def _content(row: ContentCandidate) -> ContentCandidateRecord:
    values = {f: getattr(row, f) for f in ContentCandidateRecord.__dataclass_fields__ if f != "blocks"}
    return ContentCandidateRecord(blocks=tuple(row.blocks_json or ()), **values)


def _anchors(anchors: Sequence[ProvenanceAnchor]) -> list[dict[str, Any]]:
    return [{"page_no": a.page_no, "bbox": [a.x0, a.y0, a.x1, a.y1]} for a in anchors]


def _label_key(label: str) -> str:
    return " ".join(label.casefold().split())


def _hashes(stem: str, options: Mapping[str, str]) -> tuple[str, str]:
    hashes = compute_question_hashes(stem, [options[k] for k in sorted(options)])
    return hashes.content_hash, hashes.structural_hash


def _payload(item: QuestionCandidateItem, rubric: Mapping[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": item.label,
        "stem": item.stem_text,
        "options": dict(item.options),
        "anchors": _anchors(item.anchors),
    }
    if rubric is not None:
        payload["rubric"] = dict(rubric)
    return payload


async def _require_run(session: AsyncSession, run_id: uuid.UUID) -> None:
    found = await session.scalar(text("SELECT 1 FROM extraction_runs WHERE id = :r"), {"r": run_id})
    if found is None:
        raise CandidateNotFoundError("extraction run not found")


async def _pages_of_run(session: AsyncSession, run_id: uuid.UUID) -> list[PageSegmentInput]:
    rows = await session.execute(
        text(
            "SELECT page_no, raw_text, width_pt, height_pt FROM extracted_pages "
            "WHERE extraction_run_id = :r ORDER BY page_no"
        ),
        {"r": run_id},
    )
    return [
        PageSegmentInput(
            page_no=r.page_no, text=r.raw_text or "", bbox=(0.0, 0.0, float(r.width_pt), float(r.height_pt))
        )
        for r in rows
    ]


def rubric_by_label(
    rows: Sequence[Sequence[TableCellSpan]], expected_total: Decimal | None
) -> tuple[RubricParseReport, dict]:
    """Parse a three-column scoring table and index its rows by question label (several rows of one question add up)."""
    report = parse_three_column_rubric(rows, expected_total_points=expected_total)
    by_label: dict[str, dict[str, Any]] = {}
    for item in report.rows:
        entry = by_label.setdefault(
            _label_key(item.question_label), {"label": item.question_label, "points": Decimal(0), "criteria": []}
        )
        entry["points"] += item.points
        entry["criteria"].append(item.criterion_text)
    return report, {k: {**v, "points": str(v["points"])} for k, v in by_label.items()}


async def _announce(session: AsyncSession, row: QuestionCandidate, correlation_id: str | None) -> None:
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=CANDIDATE_CREATED_EVENT,
            tenant_id=row.tenant_id,
            aggregate_type="question_candidate",
            aggregate_id=row.id,
            correlation_id=correlation_id,
            payload={
                "tenant_id": str(row.tenant_id),
                "candidate_id": str(row.id),
                "acquisition_method": row.acquisition_method,
                "content_hash": row.content_hash,
                "structural_hash": row.structural_hash,
            },
        ),
    )


async def normalize_extraction_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    pages: Sequence[PageSegmentInput] | None = None,
    title: str = "Chuyên đề",
    rubric_rows: Sequence[Sequence[TableCellSpan]] | None = None,
    rubric_expected_total: Decimal | None = None,
    correlation_id: str | None = None,
) -> NormalizeResult:
    """Split the run's pages into one content candidate and N question candidates (once per run)."""
    await _require_run(session, extraction_run_id)
    already = await session.scalar(
        select(QuestionCandidate.id).where(QuestionCandidate.extraction_run_id == extraction_run_id).limit(1)
    ) or await session.scalar(
        select(ContentCandidate.id).where(ContentCandidate.extraction_run_id == extraction_run_id).limit(1)
    )
    if already is not None:
        raise CandidateConflictError("ALREADY_NORMALIZED", "this extraction run already has candidates")
    segments = list(pages) if pages is not None else await _pages_of_run(session, extraction_run_id)
    result = split_study_guide_mixed(segments, default_title=title)
    if not result.content_blocks and not result.question_candidates:
        raise CandidateConflictError("NOTHING_TO_EXTRACT", "no content or question was found in the pages")

    rubric: dict[str, dict[str, Any]] = {}
    rubric_total: Decimal | None = None
    warnings = list(result.warnings)
    if rubric_rows:
        report, rubric = rubric_by_label(rubric_rows, rubric_expected_total)
        rubric_total = report.total_points
        warnings += list(report.warnings)

    content_row = None
    if result.content_blocks:
        blocks = [
            {"kind": b.kind.value, "heading": b.heading, "body_text": b.body_text, "anchors": _anchors(b.anchors)}
            for b in result.content_blocks
        ]
        content_row = (
            await session.execute(
                insert(ContentCandidate)
                .values(
                    tenant_id=tenant_id, extraction_run_id=extraction_run_id, title=result.title, blocks_json=blocks
                )
                .returning(ContentCandidate)
            )
        ).scalar_one()
    questions: list[QuestionCandidate] = []
    for item in result.question_candidates:
        content_hash, structural_hash = _hashes(item.stem_text, item.options)
        row = (
            await session.execute(
                insert(QuestionCandidate)
                .values(
                    tenant_id=tenant_id,
                    extraction_run_id=extraction_run_id,
                    acquisition_method="DOCUMENT_IMPORT",
                    item_type=item.kind.value,
                    payload_json=_payload(item, rubric.get(_label_key(item.label))),
                    content_hash=content_hash,
                    structural_hash=structural_hash,
                )
                .returning(QuestionCandidate)
            )
        ).scalar_one()
        questions.append(row)
        await _announce(session, row, correlation_id)
    return NormalizeResult(
        content=None if content_row is None else _content(content_row),
        questions=tuple(_question(q) for q in questions),
        unassigned_exercise_text=tuple(result.unassigned_exercise_text),
        warnings=tuple(warnings),
        rubric_points_total=rubric_total,
    )


async def get_candidate(
    session: AsyncSession, candidate_id: uuid.UUID
) -> QuestionCandidateRecord | ContentCandidateRecord | None:
    """A question or a content candidate of the caller's tenant (ids are UUIDv7: no collision between the two)."""
    row = await session.scalar(
        select(QuestionCandidate).where(QuestionCandidate.id == candidate_id).execution_options(populate_existing=True)
    )
    if row is not None:
        return _question(row)
    content = await session.scalar(
        select(ContentCandidate).where(ContentCandidate.id == candidate_id).execution_options(populate_existing=True)
    )
    return None if content is None else _content(content)


async def _pending_question(session: AsyncSession, candidate_id: uuid.UUID) -> QuestionCandidate:
    row = await session.scalar(
        select(QuestionCandidate)
        .where(QuestionCandidate.id == candidate_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise CandidateNotFoundError("question candidate not found")
    if row.candidate_status != "NEEDS_REVIEW":
        raise CandidateConflictError("NOT_PENDING", f"candidate is {row.candidate_status}, only NEEDS_REVIEW changes")
    return row


async def split_candidate(
    session: AsyncSession, candidate_id: uuid.UUID, *, parts: Sequence[str], correlation_id: str | None = None
) -> tuple[QuestionCandidateRecord, tuple[QuestionCandidateRecord, ...]]:
    """Cut one question candidate into several (the stems in `parts`); the original is REJECTED as superseded."""
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if not 2 <= len(cleaned) <= MAX_PARTS:
        raise ValueError(f"a split needs between 2 and {MAX_PARTS} non-empty parts")
    parent = await _pending_question(session, candidate_id)
    children: list[QuestionCandidate] = []
    for index, stem in enumerate(cleaned, start=1):
        payload = {
            "label": f"{parent.payload_json.get('label', '?')}.{index}",
            "stem": stem,
            "options": {},
            "anchors": parent.payload_json.get("anchors", []),
            "split_from": str(parent.id),
        }
        content_hash, structural_hash = _hashes(stem, {})
        child = (
            await session.execute(
                insert(QuestionCandidate)
                .values(
                    tenant_id=parent.tenant_id,
                    extraction_run_id=parent.extraction_run_id,
                    parent_candidate_id=parent.id,
                    acquisition_method=parent.acquisition_method,
                    item_type="ESSAY" if parent.item_type in {"SINGLE_CHOICE", "MULTIPLE_SELECT"} else parent.item_type,
                    payload_json=payload,
                    content_hash=content_hash,
                    structural_hash=structural_hash,
                )
                .returning(QuestionCandidate)
            )
        ).scalar_one()
        children.append(child)
        await _announce(session, child, correlation_id)
    superseded = (
        await session.execute(
            update(QuestionCandidate)
            .where(QuestionCandidate.id == parent.id)
            .values(
                candidate_status="REJECTED",
                payload_json={**parent.payload_json, "split_into": [str(c.id) for c in children]},
            )
            .returning(QuestionCandidate)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _question(superseded), tuple(_question(c) for c in children)


async def merge_candidates(
    session: AsyncSession, target_id: uuid.UUID, *, merge_ids: Sequence[uuid.UUID]
) -> QuestionCandidateRecord:
    """Fold other candidates of the same extraction run into `target_id`; the folded ones are REJECTED."""
    others = list(dict.fromkeys(i for i in merge_ids if i != target_id))
    if not 1 <= len(others) <= MAX_MERGE:
        raise ValueError(f"a merge needs between 1 and {MAX_MERGE} other candidates")
    target = await _pending_question(session, target_id)
    folded = [await _pending_question(session, i) for i in others]
    if any(f.extraction_run_id != target.extraction_run_id for f in folded):
        raise CandidateConflictError("DIFFERENT_RUN", "candidates of different extraction runs cannot be merged")
    stems = [target.payload_json.get("stem", ""), *(f.payload_json.get("stem", "") for f in folded)]
    options: dict[str, str] = dict(target.payload_json.get("options", {}))
    for f in folded:
        options.update(f.payload_json.get("options", {}))
    stem = "\n".join(s for s in stems if s)
    content_hash, structural_hash = _hashes(stem, options)
    payload = {
        **target.payload_json,
        "stem": stem,
        "options": options,
        "anchors": [
            *target.payload_json.get("anchors", []),
            *(a for f in folded for a in f.payload_json.get("anchors", [])),
        ],
        "merged_from": [str(f.id) for f in folded],
    }
    for f in folded:
        await session.execute(
            update(QuestionCandidate)
            .where(QuestionCandidate.id == f.id)
            .values(candidate_status="REJECTED", payload_json={**f.payload_json, "merged_into": str(target.id)})
        )
    merged = (
        await session.execute(
            update(QuestionCandidate)
            .where(QuestionCandidate.id == target.id)
            .values(payload_json=payload, content_hash=content_hash, structural_hash=structural_hash)
            .returning(QuestionCandidate)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _question(merged)
