"""Database service layer for D03 Interactive Practice Engine."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.error_notebook.public import record_error_note_attempt
from app.modules.outbox.public import enqueue
from app.modules.practice.internal.engine import (
    MasterySignal,
    PracticeEngineError,
    PracticeItemSpec,
    PracticeMode,
    PracticeResponseRecord,
    PracticeSessionState,
    PracticeSessionStatus,
    aggregate_signal_summary,
    complete_practice_session,
    reveal_next_hint,
    submit_practice_response,
)
from app.modules.practice.internal.models import PracticeResponse, PracticeSession


class PracticeValidationError(ValueError):
    """Raised when practice session inputs are invalid."""


class PracticeNotFoundError(LookupError):
    """Raised when a practice session or question item cannot be found."""


class PracticeDomainError(RuntimeError):
    """Wraps D03 pure-core domain errors with explicit HTTP status codes."""

    def __init__(self, code: str, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PracticeResponseView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    practice_session_id: uuid.UUID
    question_version_id: uuid.UUID
    response_id: str
    submitted_answer: str
    is_correct: bool | None
    hints_used: int
    immediate_feedback: bool
    explanation_md: str | None
    answered_at: datetime


@dataclass(frozen=True, slots=True)
class PracticeSessionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    mode: str
    status: str
    immediate_feedback: bool
    created_at: datetime
    completed_at: datetime | None
    responses: tuple[PracticeResponseView, ...]


@dataclass(frozen=True, slots=True)
class PracticeCompletionView:
    session: PracticeSessionView
    total_answered: int
    correct_count: int
    accuracy: float
    error_note_question_ids: tuple[str, ...]
    resolved_remediation_question_ids: tuple[str, ...]
    mastery_signals: tuple[MasterySignal, ...]
    mastery_summary: dict[str, dict[str, int]]


@dataclass(slots=True)
class _SessionRuntimeContext:
    immediate_feedback: bool
    items: tuple[PracticeItemSpec, ...]
    hints_revealed: dict[str, int]


_SESSION_RUNTIME_CACHE: dict[tuple[str, str], _SessionRuntimeContext] = {}


def _extract_expected_answer(answer_schema: Any) -> str:
    if isinstance(answer_schema, dict):
        for key in ("expected_answer", "correct_key", "correct_option", "value"):
            val = answer_schema.get(key)
            if val is not None:
                return str(val)
        accepted = answer_schema.get("accepted_values")
        if isinstance(accepted, list) and accepted:
            return str(accepted[0])
        if "sub_items" in answer_schema and isinstance(answer_schema["sub_items"], dict):
            return json.dumps(answer_schema["sub_items"], sort_keys=True)
    if answer_schema is None:
        return ""
    return str(answer_schema)


async def _selectable_tables(session: AsyncSession, names: Sequence[str]) -> set[str]:
    rows = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(:names) "
                "AND has_table_privilege(current_user, 'public.' || quote_ident(tablename), 'SELECT')"
            ),
            {"names": list(names)},
        )
    ).scalars().all()
    return set(rows)


async def _insertable_tables(session: AsyncSession, names: Sequence[str]) -> set[str]:
    rows = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(:names) "
                "AND has_table_privilege(current_user, 'public.' || quote_ident(tablename), 'INSERT')"
            ),
            {"names": list(names)},
        )
    ).scalars().all()
    return set(rows)


async def _load_item_specs_from_db(
    session: AsyncSession,
    *,
    question_version_ids: Sequence[uuid.UUID],
) -> tuple[PracticeItemSpec, ...]:
    if not question_version_ids:
        raise PracticeValidationError("at least one question_version_id or item_spec is required")

    unique_ids = list(dict.fromkeys(question_version_ids))
    readable = await _selectable_tables(session, ("question_version_answers", "questions", "question_versions"))
    has_answers = "question_version_answers" in readable

    if has_answers:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT qv.id AS qv_id,
                           q.primary_taxonomy_node_id,
                           qva.answer_schema,
                           qva.rationale_json
                    FROM question_versions qv
                    JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id
                    LEFT JOIN question_version_answers qva ON qva.question_version_id = qv.id
                    WHERE qv.id = ANY(:qv_ids)
                    """
                ),
                {"qv_ids": unique_ids},
            )
        ).all()
    else:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT qv.id AS qv_id,
                           q.primary_taxonomy_node_id,
                           NULL::jsonb AS answer_schema,
                           NULL::jsonb AS rationale_json
                    FROM question_versions qv
                    JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id
                    WHERE qv.id = ANY(:qv_ids)
                    """
                ),
                {"qv_ids": unique_ids},
            )
        ).all()

    by_id = {uuid.UUID(str(r.qv_id)): r for r in rows}
    missing = [str(qid) for qid in unique_ids if qid not in by_id]
    if missing:
        raise PracticeNotFoundError(f"question_version(s) not found: {', '.join(missing)}")

    specs: list[PracticeItemSpec] = []
    for qid in unique_ids:
        row = by_id[qid]
        rationale = row.rationale_json if isinstance(row.rationale_json, dict) else {}
        explanation = str(rationale.get("explanation_md") or rationale.get("summary_md") or "")
        raw_hints = rationale.get("hints") or ()
        hints = tuple(str(h) for h in raw_hints if str(h).strip())
        tax_id = str(row.primary_taxonomy_node_id) if row.primary_taxonomy_node_id is not None else "UNSPECIFIED"
        expected = _extract_expected_answer(row.answer_schema)
        if not expected:
            raise PracticeValidationError(f"no expected answer available in answer_schema for question_version {qid}")
        specs.append(
            PracticeItemSpec(
                question_version_id=str(qid),
                taxonomy_node_id=tax_id,
                expected_answer=expected,
                explanation_md=explanation,
                hints=hints,
            )
        )
    return tuple(specs)


def _response_view(
    row: PracticeResponse,
    *,
    immediate_feedback: bool,
) -> PracticeResponseView:
    payload = row.response_payload if isinstance(row.response_payload, dict) else {}
    return PracticeResponseView(
        id=row.id,
        tenant_id=row.tenant_id,
        practice_session_id=row.practice_session_id,
        question_version_id=row.question_version_id,
        response_id=str(payload.get("response_id") or row.id),
        submitted_answer=str(payload.get("submitted_answer") or ""),
        is_correct=row.is_correct if immediate_feedback else None,
        hints_used=row.hints_used,
        immediate_feedback=immediate_feedback,
        explanation_md=payload.get("explanation_md") if immediate_feedback else None,
        answered_at=row.answered_at,
    )


def _session_view(
    db_session: PracticeSession,
    responses: Sequence[PracticeResponse],
    *,
    immediate_feedback: bool,
) -> PracticeSessionView:
    return PracticeSessionView(
        id=db_session.id,
        tenant_id=db_session.tenant_id,
        membership_id=db_session.membership_id,
        mode=db_session.mode,
        status=db_session.status,
        immediate_feedback=immediate_feedback,
        created_at=db_session.created_at,
        completed_at=db_session.completed_at,
        responses=tuple(_response_view(r, immediate_feedback=immediate_feedback) for r in responses),
    )


def _map_engine_error(exc: PracticeEngineError) -> PracticeDomainError:
    status_code = {
        "SESSION_ALREADY_COMPLETED": 409,
        "RESPONSE_ID_CONFLICT": 409,
        "ITEM_NOT_IN_SESSION": 404,
        "UNKNOWN_QUESTION": 404,
        "NO_MORE_HINTS": 409,
        "SESSION_EXPIRED": 409,
    }.get(exc.code, 422)
    return PracticeDomainError(exc.code, str(exc), status_code=status_code)


async def _load_session_and_context(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    item_specs: Sequence[PracticeItemSpec] | None = None,
    immediate_feedback: bool | None = None,
) -> tuple[PracticeSession, list[PracticeResponse], _SessionRuntimeContext, PracticeSessionState]:
    db_sess = (
        await session.execute(
            select(PracticeSession).where(
                PracticeSession.tenant_id == tenant_id,
                PracticeSession.id == session_id,
            )
        )
    ).scalar_one_or_none()
    if db_sess is None:
        raise PracticeNotFoundError(f"practice session {session_id} not found")
    if membership_id is not None and db_sess.membership_id != membership_id:
        raise PracticeNotFoundError(f"practice session {session_id} not found for membership")

    db_resps = list(
        (
            await session.execute(
                select(PracticeResponse)
                .where(
                    PracticeResponse.tenant_id == tenant_id,
                    PracticeResponse.practice_session_id == session_id,
                )
                .order_by(PracticeResponse.answered_at.asc(), PracticeResponse.id.asc())
            )
        ).scalars().all()
    )

    cache_key = (str(tenant_id), str(session_id))
    cached = _SESSION_RUNTIME_CACHE.get(cache_key)

    resolved_immediate = (
        immediate_feedback
        if immediate_feedback is not None
        else (
            cached.immediate_feedback
            if cached is not None
            else (db_sess.mode == PracticeMode.LESSON_EMBED.value)
        )
    )

    if item_specs is not None:
        resolved_items = tuple(item_specs)
    elif cached is not None:
        resolved_items = cached.items
    else:
        qids = list(dict.fromkeys(r.question_version_id for r in db_resps))
        resolved_items = await _load_item_specs_from_db(session, question_version_ids=qids) if qids else ()

    hints_map = dict(cached.hints_revealed) if cached is not None else {}
    for resp in db_resps:
        qid_str = str(resp.question_version_id)
        hints_map[qid_str] = max(hints_map.get(qid_str, 0), resp.hints_used)

    ctx = _SessionRuntimeContext(
        immediate_feedback=resolved_immediate,
        items=resolved_items,
        hints_revealed=hints_map,
    )
    _SESSION_RUNTIME_CACHE[cache_key] = ctx

    records = tuple(
        PracticeResponseRecord(
            response_id=str(r.response_payload.get("response_id") or r.id),
            question_version_id=str(r.question_version_id),
            submitted_answer=str(r.response_payload.get("submitted_answer") or ""),
            is_correct=r.is_correct if resolved_immediate else None,
            hints_used=r.hints_used,
            explanation_md=r.response_payload.get("explanation_md") if resolved_immediate else None,
        )
        for r in db_resps
    )
    applied_ids = frozenset(rec.response_id for rec in records)
    graded_pairs = tuple(
        (str(r.response_payload.get("response_id") or r.id), bool(r.is_correct))
        for r in db_resps
    )

    state = PracticeSessionState(
        session_id=str(db_sess.id),
        tenant_id=str(db_sess.tenant_id),
        membership_id=str(db_sess.membership_id),
        mode=PracticeMode(db_sess.mode),
        immediate_feedback=resolved_immediate,
        items=resolved_items,
        status=PracticeSessionStatus(db_sess.status),
        hints_revealed=dict(hints_map),
        responses=records,
        applied_response_ids=applied_ids,
        graded=graded_pairs,
    )
    return db_sess, db_resps, ctx, state


async def start_practice_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    mode: str | PracticeMode = PracticeMode.LESSON_EMBED,
    question_version_ids: Sequence[uuid.UUID] = (),
    item_specs: Sequence[PracticeItemSpec] | None = None,
    immediate_feedback: bool = True,
) -> PracticeSessionView:
    """Create a `practice_sessions` record (isolated from formal exam `attempts`)."""
    try:
        parsed_mode = mode if isinstance(mode, PracticeMode) else PracticeMode(mode.strip().upper())
    except ValueError as exc:
        raise PracticeValidationError(f"unsupported practice mode: {mode}") from exc

    resolved_items = (
        tuple(item_specs)
        if item_specs is not None
        else await _load_item_specs_from_db(session, question_version_ids=question_version_ids)
    )
    if not resolved_items:
        raise PracticeValidationError("practice session requires at least one question item")

    db_sess = PracticeSession(
        tenant_id=tenant_id,
        membership_id=membership_id,
        mode=parsed_mode.value,
        status=PracticeSessionStatus.IN_PROGRESS.value,
    )
    session.add(db_sess)
    await session.flush()

    _SESSION_RUNTIME_CACHE[(str(tenant_id), str(db_sess.id))] = _SessionRuntimeContext(
        immediate_feedback=immediate_feedback,
        items=resolved_items,
        hints_revealed={},
    )
    return _session_view(db_sess, (), immediate_feedback=immediate_feedback)


async def get_practice_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
) -> PracticeSessionView:
    """Return a practice session and its responses."""
    db_sess, db_resps, ctx, _ = await _load_session_and_context(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        membership_id=membership_id,
    )
    return _session_view(db_sess, db_resps, immediate_feedback=ctx.immediate_feedback)


async def reveal_practice_hint(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    question_version_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    item_specs: Sequence[PracticeItemSpec] | None = None,
) -> tuple[str | None, int]:
    """Reveal the next progressive hint for a question in an active practice session."""
    _, _, ctx, state = await _load_session_and_context(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        membership_id=membership_id,
        item_specs=item_specs,
    )
    if not any(i.question_version_id == str(question_version_id) for i in state.items):
        loaded = await _load_item_specs_from_db(session, question_version_ids=(question_version_id,))
        merged = tuple(list(state.items) + list(loaded))
        ctx.items = merged
        state = replace(state, items=merged)

    try:
        new_state, hint_text, count = reveal_next_hint(state, question_version_id=str(question_version_id))
    except PracticeEngineError as exc:
        raise _map_engine_error(exc) from exc

    ctx.hints_revealed = dict(new_state.hints_revealed)
    return hint_text, count


async def submit_practice_answer(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    question_version_id: uuid.UUID,
    response_id: str,
    submitted_answer: str,
    membership_id: uuid.UUID | None = None,
    hints_used: int | None = None,
    item_specs: Sequence[PracticeItemSpec] | None = None,
    immediate_feedback: bool | None = None,
) -> PracticeResponseView:
    """Grade and persist one practice response with idempotency via `response_id`."""
    db_sess, db_resps, ctx, state = await _load_session_and_context(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        membership_id=membership_id,
        item_specs=item_specs,
        immediate_feedback=immediate_feedback,
    )
    if not any(i.question_version_id == str(question_version_id) for i in state.items):
        loaded = await _load_item_specs_from_db(session, question_version_ids=(question_version_id,))
        merged = tuple(list(state.items) + list(loaded))
        ctx.items = merged
        state = replace(state, items=merged)

    if hints_used is not None:
        if isinstance(hints_used, bool) or hints_used < 0:
            raise PracticeValidationError("hints_used must be >= 0")
        updated_hints = dict(state.hints_revealed)
        updated_hints[str(question_version_id)] = max(
            updated_hints.get(str(question_version_id), 0),
            hints_used,
        )
        ctx.hints_revealed = updated_hints
        state = replace(state, hints_revealed=updated_hints)

    try:
        new_state, record = submit_practice_response(
            state,
            response_id=response_id,
            question_version_id=str(question_version_id),
            submitted_answer=submitted_answer,
        )
    except PracticeEngineError as exc:
        raise _map_engine_error(exc) from exc

    # Idempotent replay matches an existing DB row
    if len(new_state.responses) == len(state.responses):
        for existing in db_resps:
            if str(existing.response_payload.get("response_id")) == record.response_id:
                return _response_view(existing, immediate_feedback=ctx.immediate_feedback)

    payload: dict[str, Any] = {
        "response_id": record.response_id,
        "submitted_answer": record.submitted_answer,
        "immediate_feedback": ctx.immediate_feedback,
    }
    if record.explanation_md is not None:
        payload["explanation_md"] = record.explanation_md

    server_is_correct = record.is_correct
    if server_is_correct is None:
        eval_state = PracticeSessionState(
            session_id=state.session_id,
            tenant_id=state.tenant_id,
            membership_id=state.membership_id,
            mode=state.mode,
            immediate_feedback=True,
            items=state.items,
            status=PracticeSessionStatus.IN_PROGRESS,
            hints_revealed=dict(state.hints_revealed),
            responses=(),
        )
        _, eval_record = submit_practice_response(
            eval_state,
            response_id=record.response_id,
            question_version_id=str(question_version_id),
            submitted_answer=submitted_answer,
        )
        server_is_correct = bool(eval_record.is_correct)

    db_row = PracticeResponse(
        tenant_id=tenant_id,
        practice_session_id=db_sess.id,
        question_version_id=question_version_id,
        response_payload=payload,
        is_correct=bool(server_is_correct),
        hints_used=record.hints_used,
    )
    session.add(db_row)
    await session.flush()
    return _response_view(db_row, immediate_feedback=ctx.immediate_feedback)


async def complete_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    item_specs: Sequence[PracticeItemSpec] | None = None,
    capture_error_notes: bool = True,
    completed_at: datetime | None = None,
) -> PracticeCompletionView:
    """Finalize `practice_sessions`, emit mastery signals / outbox, and capture wrong items in error_notes."""
    db_sess, db_resps, ctx, state = await _load_session_and_context(
        session,
        tenant_id=tenant_id,
        session_id=session_id,
        membership_id=membership_id,
        item_specs=item_specs,
    )
    try:
        report = complete_practice_session(state)
    except PracticeEngineError as exc:
        raise _map_engine_error(exc) from exc

    now_ts = completed_at or datetime.now(UTC)
    db_sess.status = PracticeSessionStatus.COMPLETED.value
    db_sess.completed_at = now_ts
    await session.flush()

    writable = await _insertable_tables(session, ("error_notes", "outbox_events"))
    item_by_qid = {item.question_version_id: item for item in state.items}
    if capture_error_notes and "error_notes" in writable:
        for qid_str in report.error_note_question_ids:
            item = item_by_qid.get(qid_str)
            node_uuid: uuid.UUID | None = None
            if item is not None:
                try:
                    node_uuid = uuid.UUID(item.taxonomy_node_id)
                except ValueError:
                    node_uuid = None
            await record_error_note_attempt(
                session,
                tenant_id=tenant_id,
                membership_id=db_sess.membership_id,
                question_version_id=uuid.UUID(qid_str),
                is_correct=False,
                primary_taxonomy_node_id=node_uuid,
                at=now_ts,
            )
        if report.resolved_remediation_question_ids:
            for qid_str in report.resolved_remediation_question_ids:
                item = item_by_qid.get(qid_str)
                node_uuid = None
                if item is not None:
                    try:
                        node_uuid = uuid.UUID(item.taxonomy_node_id)
                    except ValueError:
                        node_uuid = None
                await record_error_note_attempt(
                    session,
                    tenant_id=tenant_id,
                    membership_id=db_sess.membership_id,
                    question_version_id=uuid.UUID(qid_str),
                    is_correct=True,
                    primary_taxonomy_node_id=node_uuid,
                    at=now_ts,
                )

    accuracy_float = float(report.accuracy)
    if "outbox_events" in writable:
        envelope = EventEnvelopeContract(
            event_type="practice.session_completed",
            tenant_id=tenant_id,
            aggregate_type="practice_session",
            aggregate_id=db_sess.id,
            payload={
                "practice_session_id": str(db_sess.id),
                "membership_id": str(db_sess.membership_id),
                "mode": db_sess.mode,
                "correct_count": report.correct_count,
                "total_answered": report.total_answered,
                "accuracy": accuracy_float,
                "error_note_question_ids": list(report.error_note_question_ids),
                "resolved_remediation_question_ids": list(report.resolved_remediation_question_ids),
                "mastery_signals": [
                    {
                        "taxonomy_node_id": sig.taxonomy_node_id,
                        "question_version_id": sig.question_version_id,
                        "is_correct": sig.is_correct,
                        "hints_used": sig.hints_used,
                        "source_kind": sig.source_kind,
                    }
                    for sig in report.mastery_signals
                ],
            },
        )
        await enqueue(session, envelope)

    mastery_summary = aggregate_signal_summary(report.mastery_signals)
    return PracticeCompletionView(
        session=_session_view(db_sess, db_resps, immediate_feedback=ctx.immediate_feedback),
        total_answered=report.total_answered,
        correct_count=report.correct_count,
        accuracy=accuracy_float,
        error_note_question_ids=report.error_note_question_ids,
        resolved_remediation_question_ids=report.resolved_remediation_question_ids,
        mastery_signals=report.mastery_signals,
        mastery_summary=mastery_summary,
    )
