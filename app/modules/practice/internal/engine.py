"""D03 Practice Engine: immediate feedback, progressive hints, and mastery/error-note signal emission."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class PracticeMode(StrEnum):
    """Exact DDL CHECK values for `practice_sessions.mode`."""

    LESSON_EMBED = "LESSON_EMBED"
    TOPIC_DRILL = "TOPIC_DRILL"
    REMEDIATION_DRILL = "REMEDIATION_DRILL"


class PracticeSessionStatus(StrEnum):
    """Exact DDL CHECK values for `practice_sessions.status`."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class PracticeEngineError(ValueError):
    """Raised on invalid practice session operations with deterministic error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PracticeItemSpec:
    question_version_id: str
    taxonomy_node_id: str | None
    expected_answer: str
    explanation_md: str
    hints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PracticeResponseRecord:
    response_id: str
    question_version_id: str
    submitted_answer: str
    is_correct: bool
    hints_used: int
    explanation_md: str | None  # Revealed immediately in IMMEDIATE_FEEDBACK mode


@dataclass(frozen=True, slots=True)
class MasterySignal:
    taxonomy_node_id: str | None
    question_version_id: str
    is_correct: bool
    hints_used: int
    source_kind: str = "PRACTICE"  # Explicitly never "OFFICIAL_EXAM"


@dataclass(frozen=True, slots=True)
class PracticeSessionState:
    session_id: str
    tenant_id: str
    membership_id: str
    mode: PracticeMode
    immediate_feedback: bool = True
    status: PracticeSessionStatus = PracticeSessionStatus.IN_PROGRESS
    items: tuple[PracticeItemSpec, ...] = ()
    hints_revealed: tuple[tuple[str, int], ...] = ()
    responses: tuple[PracticeResponseRecord, ...] = ()
    applied_response_ids: frozenset[str] = field(default_factory=frozenset)


def _find_item(state: PracticeSessionState, question_version_id: str) -> PracticeItemSpec:
    for item in state.items:
        if item.question_version_id == question_version_id:
            return item
    raise PracticeEngineError(
        "ITEM_NOT_IN_SESSION",
        f"Question {question_version_id!r} is not part of practice session {state.session_id}",
    )


def reveal_next_hint(
    state: PracticeSessionState,
    *,
    question_version_id: str,
) -> tuple[PracticeSessionState, str | None, int]:
    """Unlock one progressive hint step for `question_version_id` without exceeding available hints."""
    if state.status != PracticeSessionStatus.IN_PROGRESS:
        raise PracticeEngineError("SESSION_ALREADY_COMPLETED", "Cannot reveal hints on completed session")

    item = _find_item(state, question_version_id)
    hint_counts = dict(state.hints_revealed)
    current = hint_counts.get(question_version_id, 0)
    if current >= len(item.hints):
        return state, None, current

    hint_text = item.hints[current]
    new_count = current + 1
    hint_counts[question_version_id] = new_count
    next_state = replace(state, hints_revealed=tuple(sorted(hint_counts.items())))
    return next_state, hint_text, new_count


def _answers_match(submitted: str, expected: str) -> bool:
    return submitted.strip().casefold() == expected.strip().casefold()


def submit_practice_response(
    state: PracticeSessionState,
    *,
    response_id: str,
    question_version_id: str,
    submitted_answer: str,
) -> tuple[PracticeSessionState, PracticeResponseRecord]:
    """
    Record an idempotent learner response inside a practice session.

    In `immediate_feedback=True`, scores the answer immediately and unlocks `explanation_md`.
    """
    if not response_id:
        raise PracticeEngineError("MISSING_RESPONSE_ID", "response_id must be non-empty")

    if response_id in state.applied_response_ids:
        for existing in state.responses:
            if existing.response_id == response_id:
                return state, existing
        raise PracticeEngineError("RESPONSE_ID_CONFLICT", f"Response {response_id} state inconsistent")

    if state.status != PracticeSessionStatus.IN_PROGRESS:
        raise PracticeEngineError("SESSION_ALREADY_COMPLETED", "Practice session is already completed")

    item = _find_item(state, question_version_id)
    hints_used = dict(state.hints_revealed).get(question_version_id, 0)
    is_correct = _answers_match(submitted_answer, item.expected_answer)
    explanation = item.explanation_md if state.immediate_feedback else None

    record = PracticeResponseRecord(
        response_id=response_id,
        question_version_id=question_version_id,
        submitted_answer=submitted_answer,
        is_correct=is_correct,
        hints_used=hints_used,
        explanation_md=explanation,
    )
    next_state = replace(
        state,
        responses=state.responses + (record,),
        applied_response_ids=state.applied_response_ids | {response_id},
    )
    return next_state, record


@dataclass(frozen=True, slots=True)
class PracticeCompletionReport:
    session_state: PracticeSessionState
    total_answered: int
    correct_count: int
    accuracy: float
    mastery_signals: tuple[MasterySignal, ...]
    error_note_question_ids: tuple[str, ...]
    resolved_remediation_question_ids: tuple[str, ...]


def complete_practice_session(state: PracticeSessionState) -> PracticeCompletionReport:
    """Finalize practice session and compute downstream signals for D05 Error Notebook & D06 Mastery."""
    completed_state = (
        state
        if state.status == PracticeSessionStatus.COMPLETED
        else replace(state, status=PracticeSessionStatus.COMPLETED)
    )

    # Deduplicate to latest response per question_version_id for signal summary.
    latest_by_q: dict[str, PracticeResponseRecord] = {}
    for resp in completed_state.responses:
        latest_by_q[resp.question_version_id] = resp

    item_lookup: Mapping[str, PracticeItemSpec] = {i.question_version_id: i for i in completed_state.items}
    signals: list[MasterySignal] = []
    wrong_q_ids: list[str] = []
    remediated_q_ids: list[str] = []
    correct_count = 0

    for qid, resp in latest_by_q.items():
        spec = item_lookup[qid]
        if resp.is_correct:
            correct_count += 1
            if completed_state.mode == PracticeMode.REMEDIATION_DRILL and resp.hints_used == 0:
                remediated_q_ids.append(qid)
        else:
            wrong_q_ids.append(qid)

        signals.append(
            MasterySignal(
                taxonomy_node_id=spec.taxonomy_node_id,
                question_version_id=qid,
                is_correct=resp.is_correct,
                hints_used=resp.hints_used,
            )
        )

    total = len(latest_by_q)
    accuracy = round(correct_count / total, 4) if total > 0 else 0.0
    return PracticeCompletionReport(
        session_state=completed_state,
        total_answered=total,
        correct_count=correct_count,
        accuracy=accuracy,
        mastery_signals=tuple(signals),
        error_note_question_ids=tuple(wrong_q_ids),
        resolved_remediation_question_ids=tuple(remediated_q_ids),
    )


def aggregate_signal_summary(signals: Sequence[MasterySignal]) -> dict[str, Any]:
    """Aggregate accuracy breakdown by taxonomy node purely from practice mastery signals."""
    by_node: dict[str, dict[str, int]] = {}
    for sig in signals:
        key = sig.taxonomy_node_id or "UNASSIGNED"
        stats = by_node.setdefault(key, {"total": 0, "correct": 0, "hinted": 0})
        stats["total"] += 1
        if sig.is_correct:
            stats["correct"] += 1
        if sig.hints_used > 0:
            stats["hinted"] += 1
    return by_node
