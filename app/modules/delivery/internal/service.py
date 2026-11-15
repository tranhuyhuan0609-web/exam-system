"""C05 delivery service: start an exam session, serve the paper, submit; under the caller's tenant RLS.

Meant to run as `ulp_exam_session_rw`, a role that cannot read an answer at all (no grant on
`question_version_answers` or `assessment_answer_keys`): the paper is built only from tables that hold none.

* Starting is idempotent per student and paper: a student with an IN_PROGRESS attempt gets that attempt back (F5,
  another device), never a second one. A new attempt gets its own seed, derived from the version's `base_seed`, the
  membership and the attempt number, and `generate_attempt_permutation` fixes the question order and the A..D
  permutation, both stored on the attempt, so every later read, grading and appeals see the same paper.
* `deadline_at = started_at + duration_seconds`, by the server's clock (`now`).
* The paper is served only to the attempt's own membership, while it is IN_PROGRESS and before the deadline.
* Submitting is idempotent on `Idempotency-Key`; an attempt closed after its deadline is AUTO_SUBMITTED.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.delivery import SanitizedExamPaperContract
from app.contracts.events import EventEnvelopeContract
from app.modules.assembly.public import AttemptPermutation, PermutationItemInput, generate_attempt_permutation
from app.modules.delivery.internal.models import Attempt
from app.modules.delivery.internal.sanitizer import sanitize_exam_paper
from app.modules.outbox.public import enqueue

ATTEMPT_SUBMITTED_EVENT = "attempt.submitted"
ATTEMPT_EXPIRED_EVENT = "attempt.expired"
_TIER_RANK = {"FREE": 0, "STUDENT_PRO": 1, "TEACHER_PRO": 2, "SCHOOL_ENTERPRISE": 3}


class DeliveryNotFoundError(LookupError):
    """The paper or attempt does not exist, is not visible to the tenant or is not the caller's (same answer)."""


class DeliveryForbiddenError(PermissionError):
    """The caller may not sit this paper; `code` is MEMBERSHIP_NOT_ACTIVE, ENTITLEMENT_REQUIRED or
    ATTEMPT_DEADLINE_EXPIRED."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeliveryConflictError(ValueError):
    """A state conflict; `code` is ASSESSMENT_NOT_PUBLISHED, PAPER_UNAVAILABLE or
    ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    assessment_version_id: uuid.UUID
    membership_id: uuid.UUID
    status: str
    started_at: datetime
    deadline_at: datetime
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class StartResult:
    attempt: AttemptRecord
    duration_seconds: int
    resumed: bool


@dataclass(frozen=True, slots=True)
class SubmitResult:
    attempt: AttemptRecord
    replayed: bool


def _record(row: Attempt) -> AttemptRecord:
    return AttemptRecord(**{f: getattr(row, f) for f in AttemptRecord.__dataclass_fields__})


def derive_attempt_seed(base_seed: int, membership_id: uuid.UUID, attempt_no: int) -> int:
    """A reproducible 62-bit seed of one sitting: the same student's n-th attempt at the same paper version."""
    digest = hashlib.sha256(f"{base_seed}:{membership_id}:{attempt_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 2


async def _own_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, membership_id: uuid.UUID, *, lock: bool = False
) -> Attempt:
    stmt = select(Attempt).where(Attempt.id == attempt_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    if row is None or row.membership_id != membership_id:
        raise DeliveryNotFoundError("attempt not found")
    return row


async def start_exam_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    assessment_id: uuid.UUID,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> StartResult:
    """Start (or resume) the caller's attempt at a published paper."""
    moment = now or datetime.now(UTC)
    membership = (
        await session.execute(
            text("SELECT status, entitlement_tier FROM memberships WHERE id = :m"), {"m": membership_id}
        )
    ).one_or_none()
    if membership is None or membership.status != "ACTIVE":
        raise DeliveryForbiddenError("MEMBERSHIP_NOT_ACTIVE", "only an active member can sit a paper")

    paper = (
        await session.execute(
            text(
                "SELECT id, access_tier, publication_status, rights_status, current_version_id "
                "FROM assessments WHERE id = :a"
            ),
            {"a": assessment_id},
        )
    ).one_or_none()
    if paper is None:
        raise DeliveryNotFoundError("assessment not found")
    if (
        paper.publication_status != "PUBLISHED"
        or paper.current_version_id is None
        or paper.rights_status not in ("OWNED", "LICENSED", "ALLOWED_DERIVATIVE")
    ):
        raise DeliveryConflictError("ASSESSMENT_NOT_PUBLISHED", "the assessment is not published")
    if _TIER_RANK.get(membership.entitlement_tier, 0) < _TIER_RANK[paper.access_tier]:
        raise DeliveryForbiddenError("ENTITLEMENT_REQUIRED", f"a {paper.access_tier} membership is required")

    version = (
        await session.execute(
            text("SELECT id, duration_seconds, base_seed FROM assessment_versions WHERE id = :v"),
            {"v": paper.current_version_id},
        )
    ).one_or_none()
    if version is None:
        raise DeliveryConflictError("PAPER_UNAVAILABLE", "the current version of the assessment is not readable")

    # One sitting at a time per student and paper version: concurrent starts queue here.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"attempt-start:{membership_id}:{version.id}"},
    )
    open_attempt = await session.scalar(
        select(Attempt)
        .where(
            Attempt.membership_id == membership_id,
            Attempt.assessment_version_id == version.id,
            Attempt.status == "IN_PROGRESS",
        )
        .order_by(Attempt.started_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if open_attempt is not None:
        if moment < open_attempt.deadline_at:
            return StartResult(_record(open_attempt), version.duration_seconds, resumed=True)
        await _close(session, open_attempt, moment, key=None, correlation_id=correlation_id)

    attempt_no = (
        await session.scalar(
            text("SELECT count(*) FROM attempts WHERE membership_id = :m AND assessment_version_id = :v"),
            {"m": membership_id, "v": version.id},
        )
        or 0
    ) + 1
    items = (
        await session.execute(
            text(
                "SELECT i.question_version_id, i.section_code, i.sequence_order, v.stimulus_id, q.item_type "
                "FROM assessment_items i "
                "JOIN question_versions v ON v.id = i.question_version_id "
                "JOIN questions q ON q.id = v.question_id "
                "WHERE i.assessment_version_id = :v"
            ),
            {"v": version.id},
        )
    ).all()
    stored = await session.scalar(
        text("SELECT count(*) FROM assessment_items WHERE assessment_version_id = :v"), {"v": version.id}
    )
    if not items or len(items) != stored:
        raise DeliveryConflictError("PAPER_UNAVAILABLE", "a question of the paper is no longer available")
    seed = derive_attempt_seed(version.base_seed, membership_id, attempt_no)
    permutation = generate_attempt_permutation(
        [
            PermutationItemInput(
                question_version_id=i.question_version_id,
                section_code=i.section_code,
                stimulus_id=i.stimulus_id,
                item_type=i.item_type,
                position=i.sequence_order,
            )
            for i in items
        ],
        seed,
    )
    row = (
        await session.execute(
            insert(Attempt)
            .values(
                tenant_id=tenant_id,
                assessment_version_id=version.id,
                membership_id=membership_id,
                attempt_seed=seed,
                question_order_ids=list(permutation.ordered_question_ids),
                option_permutations_json={k: list(v) for k, v in permutation.option_permutations.items()},
                started_at=moment,
                deadline_at=moment + timedelta(seconds=version.duration_seconds),
            )
            .returning(Attempt)
        )
    ).scalar_one()
    return StartResult(_record(row), version.duration_seconds, resumed=False)


async def get_attempt_paper(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    *,
    membership_id: uuid.UUID,
    now: datetime | None = None,
) -> SanitizedExamPaperContract:
    """The caller's paper, in the stored order and option permutation, with nothing that says an answer."""
    moment = now or datetime.now(UTC)
    attempt = await _own_attempt(session, attempt_id, membership_id)
    if attempt.status != "IN_PROGRESS":
        raise DeliveryConflictError("ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED", f"the attempt is {attempt.status}")
    if moment >= attempt.deadline_at:
        raise DeliveryForbiddenError("ATTEMPT_DEADLINE_EXPIRED", "the deadline of the attempt has passed")

    ids = list(attempt.question_order_ids)
    questions = (
        await session.execute(
            text(
                "SELECT v.id AS question_version_id, q.item_type, i.section_code, v.stimulus_id, v.stem_json, "
                "       v.numeric_parse_policy_json "
                "FROM assessment_items i "
                "JOIN question_versions v ON v.id = i.question_version_id "
                "JOIN questions q ON q.id = v.question_id "
                "WHERE i.assessment_version_id = :av AND v.id = ANY(:ids)"
            ),
            {"av": attempt.assessment_version_id, "ids": ids},
        )
    ).all()
    options = (
        await session.execute(
            text(
                "SELECT question_version_id, option_key, content_json, sequence_order "
                "FROM question_options WHERE question_version_id = ANY(:ids)"
            ),
            {"ids": ids},
        )
    ).all()
    options_by_question: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for o in options:
        options_by_question.setdefault(o.question_version_id, []).append(
            {"option_key": o.option_key, "content_json": o.content_json, "sequence_order": o.sequence_order}
        )
    stimulus_ids = sorted({q.stimulus_id for q in questions if q.stimulus_id is not None}, key=str)
    stimuli: Mapping[uuid.UUID, Mapping[str, Any]] = {}
    if stimulus_ids:
        stimuli = {
            r.id: r.body_json
            for r in (
                await session.execute(
                    text("SELECT id, body_json FROM question_stimuli WHERE id = ANY(:ids)"), {"ids": stimulus_ids}
                )
            ).all()
        }
    raw_questions = [
        {
            "question_version_id": q.question_version_id,
            "item_type": q.item_type,
            "section_code": q.section_code,
            "stimulus_id": q.stimulus_id,
            "stem_json": q.stem_json,
            "numeric_parse_policy_json": q.numeric_parse_policy_json,
            "options": options_by_question.get(q.question_version_id, []),
        }
        for q in questions
    ]
    permutation = AttemptPermutation(
        ordered_question_ids=tuple(ids),
        option_permutations={k: tuple(v) for k, v in (attempt.option_permutations_json or {}).items()},
    )
    try:
        return sanitize_exam_paper(
            attempt_id=attempt.id,
            assessment_version_id=attempt.assessment_version_id,
            deadline_at=attempt.deadline_at,
            raw_questions=raw_questions,
            permutation=permutation,
            stimuli_by_id=stimuli,
        )
    except ValueError as exc:  # a question or option the stored order names can no longer be read
        raise DeliveryConflictError("PAPER_UNAVAILABLE", str(exc)) from exc


def _stored_key(attempt_id: uuid.UUID, key: str | None) -> str | None:
    """The stored key is unique across the table, a client key only has to be unique per attempt."""
    return None if key is None else f"{attempt_id}:{key}"


async def _close(
    session: AsyncSession,
    attempt: Attempt,
    moment: datetime,
    *,
    key: str | None,
    correlation_id: str | None,
) -> Attempt:
    """Close an IN_PROGRESS attempt: SUBMITTED in time, AUTO_SUBMITTED after the deadline; announce it."""
    late = moment > attempt.deadline_at
    status = "AUTO_SUBMITTED" if late else "SUBMITTED"
    submitted_at = attempt.deadline_at if late else moment  # a late close counts as the deadline's
    row = (
        await session.execute(
            update(Attempt)
            .where(Attempt.id == attempt.id)
            .values(status=status, submitted_at=submitted_at, submit_idempotency_key=_stored_key(attempt.id, key))
            .returning(Attempt)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if late:
        event_type = ATTEMPT_EXPIRED_EVENT
        payload: dict[str, Any] = {
            "tenant_id": str(attempt.tenant_id),
            "attempt_id": str(attempt.id),
            "deadline_at": attempt.deadline_at.isoformat(),
            "auto_submitted_at": moment.isoformat(),
        }
    else:
        event_type = ATTEMPT_SUBMITTED_EVENT
        payload = {
            "tenant_id": str(attempt.tenant_id),
            "attempt_id": str(attempt.id),
            "membership_id": str(attempt.membership_id),
            "assessment_version_id": str(attempt.assessment_version_id),
            "submitted_at": moment.isoformat(),
        }
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=event_type,
            tenant_id=attempt.tenant_id,
            aggregate_type="attempt",
            aggregate_id=attempt.id,
            correlation_id=correlation_id,
            payload=payload,
        ),
    )
    return row


async def submit_attempt(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    *,
    membership_id: uuid.UUID,
    idempotency_key: str | None = None,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> SubmitResult:
    """Submit the caller's attempt; the same Idempotency-Key replays the first answer, another one is a conflict."""
    moment = now or datetime.now(UTC)
    attempt = await _own_attempt(session, attempt_id, membership_id, lock=True)
    if attempt.status != "IN_PROGRESS":
        if idempotency_key is not None and attempt.submit_idempotency_key == _stored_key(attempt.id, idempotency_key):
            return SubmitResult(_record(attempt), replayed=True)
        raise DeliveryConflictError("ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED", f"the attempt is already {attempt.status}")
    closed = await _close(session, attempt, moment, key=idempotency_key, correlation_id=correlation_id)
    return SubmitResult(_record(closed), replayed=False)
