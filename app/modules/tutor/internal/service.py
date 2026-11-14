"""Database service layer for E02 AI Tutor: Exam Lock (GATE-08), Grounded RAG, & Anti-Leakage."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tutor.internal.guard import (
    LearnerAttemptSnapshot,
    RetrievalCandidate,
    TutorMode,
    TutorSenderRole,
    assert_tutor_mode_allowed,
    filter_grounded_retrieval_candidates,
    filter_tutor_output,
    screen_user_prompt,
)
from app.modules.tutor.internal.models import (
    RetrievalTrace,
    TutorMessage,
    TutorSession,
)

ReplyGenerator = Callable[[str, TutorMode, tuple["GroundedCitation", ...]], str]

_SUBMITTED_ATTEMPT_STATUSES = frozenset({"SUBMITTED", "AUTO_SUBMITTED", "GRADED"})


class TutorValidationError(ValueError):
    """Raised when session or message payload fails structural validation."""


class TutorNotFoundError(LookupError):
    """Raised when a tutor session does not exist inside the caller's tenant."""


@dataclass(frozen=True)
class GroundedCitation:
    source_type: str
    version_id: uuid.UUID
    title: str
    score: Decimal


@dataclass(frozen=True)
class TutorSessionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    context_question_version_id: uuid.UUID | None
    context_content_version_id: uuid.UUID | None
    tutor_mode: TutorMode
    created_at: datetime


@dataclass(frozen=True)
class TutorMessageView:
    id: uuid.UUID
    tutor_session_id: uuid.UUID
    tenant_id: uuid.UUID
    sender_role: TutorSenderRole
    message_md: str
    citations_json: tuple[dict[str, Any], ...]
    created_at: datetime


@dataclass(frozen=True)
class TutorTurnView:
    session: TutorSessionView
    user_message: TutorMessageView
    assistant_message: TutorMessageView
    retrieval_trace_id: uuid.UUID
    retrieved_ids: tuple[uuid.UUID, ...]
    scores: tuple[Decimal, ...]
    blocked_injection: bool
    blocked_leakage: bool
    guard_reason_code: str


def _parse_tutor_mode(raw: str) -> TutorMode:
    try:
        return TutorMode(raw)
    except ValueError as exc:
        raise TutorValidationError(f"invalid tutor_mode {raw!r}") from exc


async def _load_learner_attempts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    target_attempt_id: uuid.UUID | None = None,
) -> tuple[list[LearnerAttemptSnapshot], bool]:
    rows = (
        await session.execute(
            text(
                "SELECT id, status FROM attempts "
                "WHERE tenant_id = :tenant_id AND membership_id = :membership_id"
            ),
            {"tenant_id": tenant_id, "membership_id": membership_id},
        )
    ).all()
    snapshots = [LearnerAttemptSnapshot(attempt_id=str(r.id), status=str(r.status)) for r in rows]
    if target_attempt_id is not None:
        target_str = str(target_attempt_id)
        submitted = any(
            snap.attempt_id == target_str and snap.status in _SUBMITTED_ATTEMPT_STATUSES
            for snap in snapshots
        )
    else:
        submitted = any(snap.status in _SUBMITTED_ATTEMPT_STATUSES for snap in snapshots)
    return snapshots, submitted


async def _retrieve_grounded_context(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    context_content_version_id: uuid.UUID | None,
    context_question_version_id: uuid.UUID | None,
    limit: int = 5,
) -> tuple[GroundedCitation, ...]:
    """Fetch strictly PUBLISHED + immutable RAG context rows visible to the tenant."""
    candidates: list[RetrievalCandidate] = []
    meta_by_id: dict[str, tuple[str, str, Decimal]] = {}

    content_rows = (
        await session.execute(
            text(
                """
                SELECT cv.id, cv.tenant_id, ci.visibility_scope, cv.status AS cv_status,
                       ci.publication_status, ci.rights_status, cv.is_immutable, cv.title
                FROM content_versions cv
                JOIN content_items ci ON ci.id = cv.content_item_id AND ci.tenant_id = cv.tenant_id
                WHERE cv.status = 'PUBLISHED'
                  AND ci.publication_status = 'PUBLISHED'
                  AND cv.is_immutable IS TRUE
                  AND ci.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                  AND (cv.tenant_id = :tenant_id OR ci.visibility_scope = 'SYSTEM_PUBLIC')
                ORDER BY (cv.id = :pinned_cv_id) DESC, cv.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "tenant_id": tenant_id,
                "pinned_cv_id": context_content_version_id,
                "limit": limit,
            },
        )
    ).all()

    for idx, row in enumerate(content_rows):
        doc_id = str(row.id)
        is_pinned = context_content_version_id is not None and row.id == context_content_version_id
        score = Decimal("0.9800") if is_pinned else Decimal(f"{max(0.5000, 0.9000 - idx * 0.0500):.4f}")
        candidates.append(
            RetrievalCandidate(
                document_id=doc_id,
                tenant_id=str(row.tenant_id),
                publication_status=str(row.cv_status),
                visibility_scope=str(row.visibility_scope),
                is_immutable=bool(row.is_immutable),
            )
        )
        meta_by_id[doc_id] = ("CONTENT_VERSION", str(row.title), score)

    has_v113_questions = bool(
        (
            await session.execute(
                text(
                    """
                    SELECT 1
                    FROM pg_attribute a
                    WHERE a.attrelid = to_regclass('questions')
                      AND a.attname = 'publication_status'
                      AND NOT a.attisdropped
                      AND to_regclass('question_versions') IS NOT NULL
                    LIMIT 1
                    """
                )
            )
        ).scalar()
    )

    question_rows = ()
    if has_v113_questions:
        question_rows = (
            await session.execute(
                text(
                    """
                    SELECT qv.id, qv.tenant_id, qv.visibility_scope, q.publication_status,
                           q.rights_status, qv.is_immutable, q.stable_code
                    FROM question_versions qv
                    JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id
                    WHERE q.publication_status = 'PUBLISHED'
                      AND qv.is_immutable IS TRUE
                      AND q.rights_status IN ('OWNED', 'LICENSED', 'ALLOWED_DERIVATIVE')
                      AND (qv.tenant_id = :tenant_id OR qv.visibility_scope = 'SYSTEM_PUBLIC')
                    ORDER BY (qv.id = :pinned_qv_id) DESC, qv.created_at DESC
                    LIMIT :limit
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "pinned_qv_id": context_question_version_id,
                    "limit": limit,
                },
            )
        ).all()

    for idx, row in enumerate(question_rows):
        doc_id = str(row.id)
        is_pinned = context_question_version_id is not None and row.id == context_question_version_id
        score = Decimal("0.9700") if is_pinned else Decimal(f"{max(0.5000, 0.8500 - idx * 0.0500):.4f}")
        candidates.append(
            RetrievalCandidate(
                document_id=doc_id,
                tenant_id=str(row.tenant_id),
                publication_status=str(row.publication_status),
                visibility_scope=str(row.visibility_scope),
                is_immutable=bool(row.is_immutable),
            )
        )
        meta_by_id[doc_id] = ("QUESTION_VERSION", f"Câu hỏi {row.stable_code}", score)

    approved = filter_grounded_retrieval_candidates(candidates, learner_tenant_id=str(tenant_id))
    citations: list[GroundedCitation] = []
    for cand in approved[:limit]:
        src_type, title, score = meta_by_id[cand.document_id]
        citations.append(
            GroundedCitation(
                source_type=src_type,
                version_id=uuid.UUID(cand.document_id),
                title=title,
                score=score,
            )
        )
    return tuple(citations)


async def _load_answer_schema_for_shield(
    session: AsyncSession,
    *,
    question_version_id: uuid.UUID | None,
) -> Mapping[str, Any] | None:
    if question_version_id is None:
        return None
    has_qva = bool(
        (await session.execute(text("SELECT to_regclass('question_version_answers') IS NOT NULL"))).scalar()
    )
    if not has_qva:
        return None
    row = (
        await session.execute(
            text(
                "SELECT answer_schema FROM question_version_answers "
                "WHERE question_version_id = :qid LIMIT 1"
            ),
            {"qid": question_version_id},
        )
    ).first()
    if row is None or not isinstance(row.answer_schema, Mapping):
        return None
    return row.answer_schema


def _default_grounded_reply(
    prompt: str,
    mode: TutorMode,
    citations: tuple[GroundedCitation, ...],
) -> str:
    refs = ", ".join(c.title for c in citations) if citations else "học liệu đã xuất bản"
    if mode == TutorMode.HINT_MODE:
        return (
            f"Gợi ý phương pháp dựa trên {refs}: hãy xác định giả thiết đề bài cho "
            "và viết công thức tổng quát trước khi thay số từng bước."
        )
    if mode == TutorMode.EXPLAIN_MODE:
        return (
            f"Giải thích chi tiết dựa trên {refs}: phân tích dữ kiện bài toán ({prompt.strip()[:120]}), "
            "biến đổi công thức và kiểm tra lại điều kiện nghiệm."
        )
    return (
        f"Thử thách mở rộng dựa trên {refs}: hãy thử giải một biến thể nâng cao "
        "với tham số điều kiện biên tương tự."
    )


def _session_view(row: TutorSession) -> TutorSessionView:
    return TutorSessionView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        context_question_version_id=row.context_question_version_id,
        context_content_version_id=row.context_content_version_id,
        tutor_mode=TutorMode(row.tutor_mode),
        created_at=row.created_at,
    )


def _message_view(row: TutorMessage) -> TutorMessageView:
    return TutorMessageView(
        id=row.id,
        tutor_session_id=row.tutor_session_id,
        tenant_id=row.tenant_id,
        sender_role=TutorSenderRole(row.sender_role),
        message_md=row.message_md,
        citations_json=tuple(row.citations_json or ()),
        created_at=row.created_at,
    )


async def create_tutor_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    tutor_mode: str = TutorMode.HINT_MODE.value,
    context_question_version_id: uuid.UUID | None = None,
    context_content_version_id: uuid.UUID | None = None,
    target_attempt_id: uuid.UUID | None = None,
) -> TutorSessionView:
    """Create a tutor session after enforcing GATE-08 exam lock & mode eligibility."""
    mode = _parse_tutor_mode(tutor_mode)
    snapshots, target_submitted = await _load_learner_attempts(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        target_attempt_id=target_attempt_id,
    )
    assert_tutor_mode_allowed(
        mode,
        active_attempts=snapshots,
        target_attempt_submitted=target_submitted,
    )

    row = TutorSession(
        tenant_id=tenant_id,
        membership_id=membership_id,
        context_question_version_id=context_question_version_id,
        context_content_version_id=context_content_version_id,
        tutor_mode=mode.value,
    )
    session.add(row)
    await session.flush()
    return _session_view(row)


async def send_tutor_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    tutor_session_id: uuid.UUID,
    message_md: str,
    target_attempt_id: uuid.UUID | None = None,
    reply_generator: ReplyGenerator | None = None,
) -> TutorTurnView:
    """Process one learner prompt under GATE-08 Exam Lock, RAG visibility & HINT_MODE anti-leakage."""
    tutor_session = (
        await session.execute(
            select(TutorSession).where(
                TutorSession.id == tutor_session_id,
                TutorSession.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if tutor_session is None or tutor_session.membership_id != membership_id:
        raise TutorNotFoundError(f"tutor session {tutor_session_id} not found")

    mode = TutorMode(tutor_session.tutor_mode)
    snapshots, target_submitted = await _load_learner_attempts(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        target_attempt_id=target_attempt_id,
    )
    # Fail closed even if an exam session began AFTER the tutor session was opened.
    assert_tutor_mode_allowed(
        mode,
        active_attempts=snapshots,
        target_attempt_submitted=target_submitted,
    )

    prompt_check = screen_user_prompt(message_md, mode=mode)
    if not prompt_check.allowed and not prompt_check.flagged_injection:
        raise TutorValidationError("message_md must not be empty")

    user_row = TutorMessage(
        tenant_id=tenant_id,
        tutor_session_id=tutor_session.id,
        sender_role=TutorSenderRole.USER.value,
        message_md=message_md.strip(),
        citations_json=[],
    )
    session.add(user_row)
    await session.flush()

    citations = await _retrieve_grounded_context(
        session,
        tenant_id=tenant_id,
        context_content_version_id=tutor_session.context_content_version_id,
        context_question_version_id=tutor_session.context_question_version_id,
    )
    trace_row = RetrievalTrace(
        tenant_id=tenant_id,
        tutor_session_id=tutor_session.id,
        retrieved_ids=[c.version_id for c in citations],
        scores=[c.score for c in citations],
    )
    session.add(trace_row)
    await session.flush()

    citation_payload = [
        {
            "source_type": c.source_type,
            "version_id": str(c.version_id),
            "title": c.title,
            "score": str(c.score),
        }
        for c in citations
    ]

    if prompt_check.flagged_injection:
        reply_row = TutorMessage(
            tenant_id=tenant_id,
            tutor_session_id=tutor_session.id,
            sender_role=TutorSenderRole.SYSTEM_GUARD.value,
            message_md=(
                "Chế độ HINT_MODE không thể tiết lộ trực tiếp đáp án đúng. "
                "Hãy thử nêu bước biến đổi đầu tiên để gia sư gợi ý phương pháp giải!"
            ),
            citations_json=citation_payload,
        )
        session.add(reply_row)
        await session.flush()
        return TutorTurnView(
            session=_session_view(tutor_session),
            user_message=_message_view(user_row),
            assistant_message=_message_view(reply_row),
            retrieval_trace_id=trace_row.id,
            retrieved_ids=tuple(trace_row.retrieved_ids),
            scores=tuple(trace_row.scores),
            blocked_injection=True,
            blocked_leakage=False,
            guard_reason_code=prompt_check.reason_code,
        )

    answer_schema = (
        await _load_answer_schema_for_shield(
            session,
            question_version_id=tutor_session.context_question_version_id,
        )
        if mode == TutorMode.HINT_MODE
        else None
    )
    generator = reply_generator or _default_grounded_reply
    raw_reply = generator(message_md.strip(), mode, citations)
    filtered = filter_tutor_output(raw_reply, mode=mode, answer_schema=answer_schema)

    reply_row = TutorMessage(
        tenant_id=tenant_id,
        tutor_session_id=tutor_session.id,
        sender_role=filtered.sender_role.value,
        message_md=filtered.safe_message,
        citations_json=citation_payload,
    )
    session.add(reply_row)
    await session.flush()

    return TutorTurnView(
        session=_session_view(tutor_session),
        user_message=_message_view(user_row),
        assistant_message=_message_view(reply_row),
        retrieval_trace_id=trace_row.id,
        retrieved_ids=tuple(trace_row.retrieved_ids),
        scores=tuple(trace_row.scores),
        blocked_injection=False,
        blocked_leakage=filtered.blocked_leakage,
        guard_reason_code=filtered.reason_code,
    )
