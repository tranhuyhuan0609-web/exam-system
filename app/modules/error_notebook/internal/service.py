"""Database service layer for D05 Error Notebook & Two-Streak Remediation."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.error_notebook.internal.models import ErrorNote, RemediationTask
from app.modules.error_notebook.internal.state import (
    REQUIRED_CONSECUTIVE_CORRECT,
    ErrorNoteState,
    apply_attempt,
)

ERROR_NOTE_STATUSES = frozenset({"OPEN", "IN_REMEDIATION", "RESOLVED"})


class ErrorNotebookValidationError(ValueError):
    """Raised when error-notebook attempt inputs or transitions violate D05/P11 rules."""


class ErrorNotebookNotFoundError(LookupError):
    """Raised when an error note or question reference cannot be found."""


class ErrorNotebookPolicyError(RuntimeError):
    """Raised when caller attempts a state change barred by P11 (2 consecutive correct)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ErrorNoteView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    question_version_id: uuid.UUID
    primary_taxonomy_node_id: uuid.UUID | None
    wrong_count: int
    consecutive_correct_count: int
    status: str
    last_wrong_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class RemediationTaskView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    error_note_id: uuid.UUID
    recommended_question_version_ids: tuple[uuid.UUID, ...]
    completed_count: int
    status: str
    created_at: datetime
    note_status: str


def _ensure_aware(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ErrorNotebookValidationError("attempt timestamp must be timezone-aware")
    return ts


def _note_view(row: ErrorNote) -> ErrorNoteView:
    return ErrorNoteView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        question_version_id=row.question_version_id,
        primary_taxonomy_node_id=row.primary_taxonomy_node_id,
        wrong_count=row.wrong_count,
        consecutive_correct_count=row.consecutive_correct_count,
        status=row.status,
        last_wrong_at=row.last_wrong_at,
        resolved_at=row.resolved_at,
    )


def _task_view(row: RemediationTask, *, note_status: str) -> RemediationTaskView:
    return RemediationTaskView(
        id=row.id,
        tenant_id=row.tenant_id,
        error_note_id=row.error_note_id,
        recommended_question_version_ids=tuple(row.recommended_question_version_ids),
        completed_count=row.completed_count,
        status=row.status,
        created_at=row.created_at,
        note_status=note_status,
    )


async def _has_table_privilege(session: AsyncSession, table_name: str, privilege: str) -> bool:
    row = (
        await session.execute(
            text("SELECT has_table_privilege(current_user, 'public.' || quote_ident(:t), :priv)"),
            {"t": table_name, "priv": privilege},
        )
    ).scalar_one()
    return bool(row)


async def _resolve_question_taxonomy_node(
    session: AsyncSession,
    *,
    question_version_id: uuid.UUID,
) -> uuid.UUID:
    node_id = (
        await session.execute(
            text(
                "SELECT q.primary_taxonomy_node_id "
                "FROM question_versions qv "
                "JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id "
                "WHERE qv.id = :qv_id"
            ),
            {"qv_id": question_version_id},
        )
    ).scalar_one_or_none()
    if node_id is None:
        raise ErrorNotebookNotFoundError(
            f"question_version {question_version_id} or its primary_taxonomy_node_id not found"
        )
    return uuid.UUID(str(node_id))


async def record_error_note_attempt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    question_version_id: uuid.UUID,
    is_correct: bool,
    primary_taxonomy_node_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> ErrorNoteView | None:
    """Capture or update `error_notes` using P11 (safe under both APP_RW and WORKER_GRADING)."""
    resolved_at = _ensure_aware(at)
    existing = (
        await session.execute(
            select(ErrorNote).where(
                ErrorNote.tenant_id == tenant_id,
                ErrorNote.membership_id == membership_id,
                ErrorNote.question_version_id == question_version_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        if is_correct:
            return None
        resolved_node = primary_taxonomy_node_id or await _resolve_question_taxonomy_node(
            session,
            question_version_id=question_version_id,
        )
        note = ErrorNote(
            tenant_id=tenant_id,
            membership_id=membership_id,
            question_version_id=question_version_id,
            primary_taxonomy_node_id=resolved_node,
            wrong_count=1,
            consecutive_correct_count=0,
            status="OPEN",
            last_wrong_at=resolved_at,
            resolved_at=None,
        )
        session.add(note)
        await session.flush()
        return _note_view(note)

    state = ErrorNoteState(
        last_wrong_at=existing.last_wrong_at,
        status=existing.status,  # type: ignore[arg-type]
        wrong_count=existing.wrong_count,
        consecutive_correct_count=existing.consecutive_correct_count,
        resolved_at=existing.resolved_at,
    )
    try:
        next_state = apply_attempt(state, is_correct=is_correct, at=resolved_at)
    except ValueError as exc:
        raise ErrorNotebookValidationError(str(exc)) from exc

    existing.wrong_count = next_state.wrong_count
    existing.consecutive_correct_count = next_state.consecutive_correct_count
    existing.status = next_state.status
    existing.last_wrong_at = next_state.last_wrong_at
    existing.resolved_at = next_state.resolved_at
    await session.flush()

    if is_correct and await _has_table_privilege(session, "remediation_tasks", "UPDATE"):
        assigned_tasks = (
            await session.execute(
                select(RemediationTask).where(
                    RemediationTask.tenant_id == tenant_id,
                    RemediationTask.error_note_id == existing.id,
                    RemediationTask.status == "ASSIGNED",
                )
            )
        ).scalars().all()
        for task in assigned_tasks:
            task.completed_count += 1
            if next_state.status == "RESOLVED":
                task.status = "COMPLETED"
        await session.flush()

    return _note_view(existing)


async def list_error_notes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    status: str | None = None,
    primary_taxonomy_node_id: uuid.UUID | None = None,
) -> tuple[ErrorNoteView, ...]:
    """List learner `error_notes` inside the caller's tenant."""
    stmt = select(ErrorNote).where(ErrorNote.tenant_id == tenant_id)
    if membership_id is not None:
        stmt = stmt.where(ErrorNote.membership_id == membership_id)
    if status is not None:
        clean_status = status.strip().upper()
        if clean_status not in ERROR_NOTE_STATUSES:
            raise ErrorNotebookValidationError(f"unsupported error_note status: {status}")
        stmt = stmt.where(ErrorNote.status == clean_status)
    if primary_taxonomy_node_id is not None:
        stmt = stmt.where(ErrorNote.primary_taxonomy_node_id == primary_taxonomy_node_id)
    stmt = stmt.order_by(ErrorNote.last_wrong_at.desc(), ErrorNote.id.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(_note_view(r) for r in rows)


async def create_remediation_task(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    error_note_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    limit: int = 3,
    candidate_question_version_ids: Sequence[uuid.UUID] | None = None,
) -> RemediationTaskView:
    """Assign up to 3 sibling `question_version_id` variants from the same taxonomy node."""
    if isinstance(limit, bool) or limit < 1 or limit > 10:
        raise ErrorNotebookValidationError("limit must be between 1 and 10")

    stmt = select(ErrorNote).where(
        ErrorNote.tenant_id == tenant_id,
        ErrorNote.id == error_note_id,
    )
    if membership_id is not None:
        stmt = stmt.where(ErrorNote.membership_id == membership_id)
    note = (await session.execute(stmt)).scalar_one_or_none()
    if note is None:
        raise ErrorNotebookNotFoundError(f"error note {error_note_id} not found")

    if candidate_question_version_ids is not None:
        recommended = [
            qid for qid in dict.fromkeys(candidate_question_version_ids) if qid != note.question_version_id
        ][:limit]
    else:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT qv.id AS qv_id
                    FROM questions q
                    JOIN question_versions qv
                      ON qv.question_id = q.id AND qv.tenant_id = q.tenant_id
                    WHERE q.primary_taxonomy_node_id = :node_id
                      AND qv.id <> :failed_qv_id
                      AND (q.current_version_id IS NULL OR qv.id = q.current_version_id)
                    ORDER BY CASE WHEN q.publication_status = 'PUBLISHED' THEN 0 ELSE 1 END,
                             qv.created_at DESC,
                             qv.id ASC
                    LIMIT :lim
                    """
                ),
                {
                    "node_id": note.primary_taxonomy_node_id,
                    "failed_qv_id": note.question_version_id,
                    "lim": limit,
                },
            )
        ).scalars().all()
        recommended = [uuid.UUID(str(rid)) for rid in rows]

    if not recommended:
        raise ErrorNotebookValidationError(
            f"no sibling question variants available for taxonomy node {note.primary_taxonomy_node_id}"
        )

    if note.status == "OPEN":
        note.status = "IN_REMEDIATION"

    task = RemediationTask(
        tenant_id=tenant_id,
        error_note_id=note.id,
        recommended_question_version_ids=recommended,
        completed_count=0,
        status="ASSIGNED",
    )
    session.add(task)
    await session.flush()
    return _task_view(task, note_status=note.status)


async def submit_remediation_attempt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    error_note_id: uuid.UUID,
    is_correct: bool,
    membership_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> ErrorNoteView:
    """Apply a remediation attempt; resolves only when consecutive_correct_count >= 2 (P11)."""
    stmt = select(ErrorNote).where(
        ErrorNote.tenant_id == tenant_id,
        ErrorNote.id == error_note_id,
    )
    if membership_id is not None:
        stmt = stmt.where(ErrorNote.membership_id == membership_id)
    note = (await session.execute(stmt)).scalar_one_or_none()
    if note is None:
        raise ErrorNotebookNotFoundError(f"error note {error_note_id} not found")

    updated = await record_error_note_attempt(
        session,
        tenant_id=tenant_id,
        membership_id=note.membership_id,
        question_version_id=note.question_version_id,
        is_correct=is_correct,
        primary_taxonomy_node_id=note.primary_taxonomy_node_id,
        at=at,
    )
    assert updated is not None
    return updated


async def assert_can_mark_resolved(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    error_note_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
) -> ErrorNoteView:
    """Explicit guard verifying P11 (consecutive_correct_count >= 2) before external resolution."""
    stmt = select(ErrorNote).where(
        ErrorNote.tenant_id == tenant_id,
        ErrorNote.id == error_note_id,
    )
    if membership_id is not None:
        stmt = stmt.where(ErrorNote.membership_id == membership_id)
    note = (await session.execute(stmt)).scalar_one_or_none()
    if note is None:
        raise ErrorNotebookNotFoundError(f"error note {error_note_id} not found")
    if note.consecutive_correct_count < REQUIRED_CONSECUTIVE_CORRECT:
        raise ErrorNotebookPolicyError(
            "REQUIRES_TWO_CONSECUTIVE_CORRECT",
            f"error note requires {REQUIRED_CONSECUTIVE_CORRECT} consecutive correct attempts "
            f"(currently {note.consecutive_correct_count})",
        )
    return _note_view(note)
