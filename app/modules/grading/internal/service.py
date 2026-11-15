"""C07 grading service: grade and regrade an attempt, release a grade, file an appeal; under the caller's RLS.

Grading runs as `ulp_worker_grading` and reads only what grading needs: the attempt (its stored option permutation),
its responses, the current answer key of the paper version (self-contained since C04 publishes it so) and, for a
question of the system tenant, that question's answer through `grading_read_question_answers`. Scores are computed by
the pure `internal.scoring` (Decimal, THPTQG 2025+: 0.25 per Part I item, 0.10 / 0.25 / 0.50 / 1.00 for 1..4 right
sub-items in Part II, `NumericParsePolicy` in Part III).

* The first grading of a SUBMITTED / AUTO_SUBMITTED attempt is revision 1 and marks the attempt GRADED.
* A regrade (APPEAL_REGRADE, ERRATA_REGRADE, MANUAL_OVERRIDE) inserts `revision_no + 1` and turns the previous grade
  `is_current = FALSE`, in one transaction: the history of every grading is kept.
* Every grading announces `attempt.graded`; releasing (API role) stamps `released_at` once and announces
  `grade.released`. A student appeals a released, current grade of their own attempt.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.enums import QuestionItemType
from app.contracts.events import EventEnvelopeContract
from app.contracts.scoring import NumericParsePolicy
from app.modules.audit.public import append_audit_event
from app.modules.grading.internal.models import Appeal, Grade, GradingDetail
from app.modules.grading.internal.scoring import (
    ClusterTrueFalseAnswerKey,
    ClusterTrueFalseResponse,
    GradingItemInput,
    ItemGradeResult,
    ShortNumericAnswerKey,
    ShortNumericResponse,
    SingleChoiceAnswerKey,
    SingleChoiceResponse,
    grade_assessment,
)
from app.modules.outbox.public import enqueue

ATTEMPT_GRADED_EVENT = "attempt.graded"
GRADE_RELEASED_EVENT = "grade.released"
OVERRIDE_GRADE_ACTION = "OVERRIDE_GRADE"
REGRADE_REASONS = ("APPEAL_REGRADE", "ERRATA_REGRADE", "MANUAL_OVERRIDE")
_TWO = Decimal("0.01")


class GradingNotFoundError(LookupError):
    """The attempt, grade or appeal does not exist, is another tenant's or is not the caller's (same answer)."""


class GradingConflictError(ValueError):
    """A state conflict; `code` is one of ATTEMPT_NOT_SUBMITTED, ALREADY_GRADED, NOT_GRADED_YET, NO_ANSWER_KEY,
    ANSWER_MISSING, GRADE_NOT_CURRENT, GRADE_NOT_RELEASED, APPEAL_NOT_OPEN, QUESTION_NOT_ON_PAPER."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GradeRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    attempt_id: uuid.UUID
    revision_no: int
    is_current: bool
    grade_reason: str
    scoring_scheme_id: uuid.UUID
    total_score: Decimal
    section_breakdown: dict[str, Any]
    items: tuple[dict[str, Any], ...]
    graded_by: uuid.UUID | None
    graded_at: datetime
    released_at: datetime | None


@dataclass(frozen=True, slots=True)
class AppealRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    grade_id: uuid.UUID
    membership_id: uuid.UUID
    question_version_id: uuid.UUID | None
    reason_text: str
    status: str
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None


def _grade(row: Grade) -> GradeRecord:
    skip = {"section_breakdown", "items"}
    values = {f: getattr(row, f) for f in GradeRecord.__dataclass_fields__ if f not in skip}
    return GradeRecord(
        section_breakdown=dict(row.section_breakdown_json), items=tuple(row.item_details_json or ()), **values
    )


def _appeal(row: Appeal) -> AppealRecord:
    return AppealRecord(**{f: getattr(row, f) for f in AppealRecord.__dataclass_fields__})


# --- key and response mapping (pure) -----------------------------------------------------------------------


def _policy(raw: Mapping[str, Any] | None) -> NumericParsePolicy:
    raw = dict(raw or {})
    fields: dict[str, Any] = {k: raw[k] for k in ("decimal_sep", "allow_thousand_sep", "max_chars") if k in raw}
    if "tolerance" in raw:
        fields["tolerance"] = Decimal(str(raw["tolerance"]))
    return NumericParsePolicy(**fields)


def answer_key_for(item_type: str, answer_schema: Mapping[str, Any], numeric_policy: Mapping[str, Any] | None):
    """The typed key of one item from the C01 answer schema; None for types graded by rubric (ESSAY ...)."""
    if item_type == QuestionItemType.SINGLE_CHOICE:
        correct = cast(Literal["A", "B", "C", "D"], str(answer_schema["correct_option"]))
        return SingleChoiceAnswerKey(correct_key=correct)
    if item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        return ClusterTrueFalseAnswerKey(sub_items={k: bool(v) for k, v in answer_schema["statements"].items()})
    if item_type == QuestionItemType.SHORT_NUMERIC:
        value = str(answer_schema["value"]).strip().replace(",", ".")
        try:
            target = Decimal(value)
        except InvalidOperation as exc:
            raise GradingConflictError("ANSWER_MISSING", f"stored numeric answer {value!r} is not a number") from exc
        return ShortNumericAnswerKey(target_value=target, policy=_policy(numeric_policy))
    return None


def student_response_for(item_type: str, payload: Mapping[str, Any] | None):
    """The typed response from the autosaved payload; an absent or malformed answer counts as blank."""
    payload = payload or {}
    if item_type == QuestionItemType.SINGLE_CHOICE:
        key = payload.get("selected_key")
        return SingleChoiceResponse(selected_key=key if isinstance(key, str) else None)
    if item_type == QuestionItemType.CLUSTER_TRUE_FALSE:
        raw = payload.get("sub_answers")
        answers = {k: v for k, v in raw.items() if isinstance(v, bool)} if isinstance(raw, Mapping) else {}
        return ClusterTrueFalseResponse(sub_answers=answers)
    if item_type == QuestionItemType.SHORT_NUMERIC:
        value = payload.get("text_value")
        return ShortNumericResponse(text_value=value if isinstance(value, str) else "")
    return None


def _detail(result: ItemGradeResult, section_code: str) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "question_version_id": result.item_id,
        "section_code": section_code,
        "item_type": str(result.item_type),
        "earned_points": str(result.earned_points.quantize(_TWO)),
        "max_points": str(result.max_points.quantize(_TWO)),
        "is_correct": result.is_correct,
        "status": result.status,
    }
    for name in ("displayed_key", "canonical_key", "matched_sub_items", "parse_error"):
        value = getattr(result, name)
        if value is not None:
            detail[name] = value
    if result.parsed_value is not None:
        detail["parsed_value"] = str(result.parsed_value)
    return detail


# --- grading -----------------------------------------------------------------------------------------------------


async def grade_attempt(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    *,
    graded_by: uuid.UUID | None,
    grade_reason: str = "INITIAL",
    correlation_id: str | None = None,
    now: datetime | None = None,
) -> GradeRecord:
    """Grade (or regrade) an attempt from its stored responses and the paper's current answer key."""
    moment = now or datetime.now(UTC)
    attempt = (
        await session.execute(
            text(
                "SELECT id, tenant_id, membership_id, assessment_version_id, status, option_permutations_json "
                "FROM attempts WHERE id = :a FOR UPDATE"
            ),
            {"a": attempt_id},
        )
    ).one_or_none()
    if attempt is None:
        raise GradingNotFoundError("attempt not found")
    if attempt.status in ("IN_PROGRESS", "VOIDED"):
        raise GradingConflictError("ATTEMPT_NOT_SUBMITTED", f"the attempt is {attempt.status}")
    current = await session.scalar(
        select(Grade).where(Grade.attempt_id == attempt_id, Grade.is_current.is_(True)).with_for_update()
    )
    if grade_reason == "INITIAL" and current is not None:
        raise GradingConflictError("ALREADY_GRADED", "the attempt is graded: regrade it with a reason")
    if grade_reason in REGRADE_REASONS and current is None:
        raise GradingConflictError("NOT_GRADED_YET", "there is no grade to regrade")

    key = (
        await session.execute(
            text(
                "SELECT answer_key_payload_json FROM assessment_answer_keys "
                "WHERE assessment_version_id = :v AND is_current"
            ),
            {"v": attempt.assessment_version_id},
        )
    ).scalar_one_or_none()
    if key is None:
        raise GradingConflictError("NO_ANSWER_KEY", "the paper version has no current answer key")
    items = sorted(key["items"].items(), key=lambda kv: int(kv[1].get("sequence_order", 0)))

    system_ids = [uuid.UUID(q) for q, e in items if e.get("answer_source") == "SYSTEM_QUESTION_VERSION"]
    system_answers: dict[str, Any] = {}
    if system_ids:
        rows = (
            await session.execute(
                text(
                    "SELECT question_version_id, answer_schema FROM question_version_answers "
                    "WHERE question_version_id = ANY(:ids)"
                ),
                {"ids": system_ids},
            )
        ).all()
        system_answers = {str(r.question_version_id): r.answer_schema for r in rows}
    responses = {
        str(r.question_version_id): r.response_payload
        for r in (
            await session.execute(
                text("SELECT question_version_id, response_payload FROM responses WHERE attempt_id = :a"),
                {"a": attempt_id},
            )
        ).all()
    }
    permutations = attempt.option_permutations_json or {}

    inputs: list[GradingItemInput] = []
    sections: dict[str, str] = {}
    blank_rubric: list[ItemGradeResult] = []
    for qv_id, entry in items:
        item_type = entry["item_type"]
        schema = entry.get("answer_schema") if entry.get("answer_source") == "SNAPSHOT" else system_answers.get(qv_id)
        if schema is None:
            raise GradingConflictError("ANSWER_MISSING", f"no answer for question version {qv_id}")
        sections[qv_id] = entry["section_code"]
        answer_key = answer_key_for(item_type, schema, entry.get("numeric_parse_policy"))
        points = Decimal(entry["points"])
        if answer_key is None:  # ESSAY and other rubric-graded types: 0 until a rubric grading
            blank_rubric.append(
                ItemGradeResult(
                    item_id=qv_id,
                    item_type=QuestionItemType(item_type),
                    earned_points=Decimal("0.00"),
                    max_points=points,
                    is_correct=False,
                    status="REQUIRES_RUBRIC_GRADING",
                )
            )
            continue
        permutation = permutations.get(qv_id)
        inputs.append(
            GradingItemInput(
                item_id=qv_id,
                item_type=QuestionItemType(item_type),
                max_points=points,
                answer_key=answer_key,
                student_response=student_response_for(item_type, responses.get(qv_id)),
                option_permutation=tuple(permutation) if permutation else None,
            )
        )
    graded = grade_assessment(inputs)
    by_id = {r.item_id: r for r in (*graded.item_results, *blank_rubric)}
    results = [by_id[qv_id] for qv_id, _ in items]

    breakdown: dict[str, dict[str, str]] = {}
    for r in results:
        cell = breakdown.setdefault(sections[r.item_id], {"earned": "0.00", "max": "0.00", "items": "0"})
        cell["earned"] = str((Decimal(cell["earned"]) + r.earned_points).quantize(_TWO))
        cell["max"] = str((Decimal(cell["max"]) + r.max_points).quantize(_TWO))
        cell["items"] = str(int(cell["items"]) + 1)
    total = sum((r.earned_points for r in results), Decimal("0")).quantize(_TWO)

    revision = 1
    if current is not None:
        revision = (
            await session.scalar(text("SELECT max(revision_no) FROM grades WHERE attempt_id = :a"), {"a": attempt_id})
        ) + 1
        await session.execute(update(Grade).where(Grade.id == current.id).values(is_current=False))
    grade = (
        await session.execute(
            insert(Grade)
            .values(
                tenant_id=attempt.tenant_id,
                attempt_id=attempt_id,
                revision_no=revision,
                is_current=True,
                grade_reason=grade_reason,
                scoring_scheme_id=uuid.UUID(key["scoring_scheme_id"]),
                total_score=total,
                section_breakdown_json=breakdown,
                item_details_json=[_detail(r, sections[r.item_id]) for r in results],
                graded_by=graded_by,
                graded_at=moment,
            )
            .returning(Grade)
        )
    ).scalar_one()
    for r in results:
        await session.execute(
            insert(GradingDetail).values(
                tenant_id=attempt.tenant_id,
                grade_id=grade.id,
                question_version_id=uuid.UUID(r.item_id),
                earned_points=r.earned_points.quantize(_TWO),
                max_points=r.max_points.quantize(_TWO),
                is_correct=r.is_correct,
                sub_item_breakdown_json=_detail(r, sections[r.item_id]),
            )
        )
    if attempt.status != "GRADED":
        await session.execute(text("UPDATE attempts SET status = 'GRADED' WHERE id = :a"), {"a": attempt_id})
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=ATTEMPT_GRADED_EVENT,
            tenant_id=attempt.tenant_id,
            aggregate_type="attempt",
            aggregate_id=attempt_id,
            correlation_id=correlation_id,
            payload={
                "tenant_id": str(attempt.tenant_id),
                "attempt_id": str(attempt_id),
                "grade_id": str(grade.id),
                "revision_no": revision,
                "total_score": str(total),
                "wrong_question_version_ids": [r.item_id for r in results if not r.is_correct],
            },
        ),
    )
    return _grade(grade)


async def override_grade(
    session: AsyncSession,
    grade_id: uuid.UUID,
    *,
    total_score: Decimal,
    reason: str,
    actor_id: uuid.UUID,
    correlation_id: str,
    now: datetime | None = None,
) -> GradeRecord:
    """A teacher sets the total of the current grade by hand: a MANUAL_OVERRIDE revision + 1, never an edit.

    The per-item results of the overridden revision are copied (what the machine scored stays visible); only the
    total differs. The reason is mandatory and the change is audited (`OVERRIDE_GRADE`, before and after) in the same
    transaction. It runs as the grading worker, the only role that writes grades, and announces `attempt.graded`.
    """
    moment = now or datetime.now(UTC)
    current = await session.scalar(
        select(Grade).where(Grade.id == grade_id).with_for_update().execution_options(populate_existing=True)
    )
    if current is None:
        raise GradingNotFoundError("grade not found")
    if not current.is_current:
        raise GradingConflictError("GRADE_NOT_CURRENT", f"revision {current.revision_no} was superseded by a regrade")
    maximum = sum((Decimal(cell["max"]) for cell in current.section_breakdown_json.values()), Decimal("0"))
    new_total = Decimal(total_score).quantize(_TWO)
    if new_total < 0 or new_total > maximum:
        raise GradingConflictError("SCORE_OUT_OF_RANGE", f"total_score must be between 0 and {maximum}")
    before = {"grade_id": str(current.id), "revision_no": current.revision_no, "total_score": str(current.total_score)}

    revision = (
        await session.scalar(
            text("SELECT max(revision_no) FROM grades WHERE attempt_id = :a"), {"a": current.attempt_id}
        )
    ) + 1
    await session.execute(update(Grade).where(Grade.id == current.id).values(is_current=False))
    grade = (
        await session.execute(
            insert(Grade)
            .values(
                tenant_id=current.tenant_id,
                attempt_id=current.attempt_id,
                revision_no=revision,
                is_current=True,
                grade_reason="MANUAL_OVERRIDE",
                scoring_scheme_id=current.scoring_scheme_id,
                total_score=new_total,
                section_breakdown_json=current.section_breakdown_json,
                item_details_json=current.item_details_json,
                graded_by=actor_id,
                graded_at=moment,
            )
            .returning(Grade)
        )
    ).scalar_one()
    details = (await session.execute(select(GradingDetail).where(GradingDetail.grade_id == current.id))).scalars().all()
    for d in details:
        await session.execute(
            insert(GradingDetail).values(
                tenant_id=d.tenant_id,
                grade_id=grade.id,
                question_version_id=d.question_version_id,
                earned_points=d.earned_points,
                max_points=d.max_points,
                is_correct=d.is_correct,
                sub_item_breakdown_json=d.sub_item_breakdown_json,
            )
        )
    await append_audit_event(
        session,
        tenant_id=current.tenant_id,
        actor_id=actor_id,
        action=OVERRIDE_GRADE_ACTION,
        target_type="grades",
        target_id=grade.id,
        reason=reason,
        before=before,
        after={"grade_id": str(grade.id), "revision_no": revision, "total_score": str(new_total)},
        correlation_id=correlation_id,
        occurred_at=moment,
    )
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=ATTEMPT_GRADED_EVENT,
            tenant_id=current.tenant_id,
            aggregate_type="attempt",
            aggregate_id=current.attempt_id,
            correlation_id=correlation_id,
            payload={
                "tenant_id": str(current.tenant_id),
                "attempt_id": str(current.attempt_id),
                "grade_id": str(grade.id),
                "revision_no": revision,
                "total_score": str(new_total),
                "wrong_question_version_ids": [str(d.question_version_id) for d in details if not d.is_correct],
            },
        ),
    )
    return _grade(grade)


async def get_grade(session: AsyncSession, grade_id: uuid.UUID) -> GradeRecord | None:
    row = await session.scalar(select(Grade).where(Grade.id == grade_id).execution_options(populate_existing=True))
    return None if row is None else _grade(row)


# --- release and appeals (API role) ------------------------------------------------------------------------------


async def release_grade(
    session: AsyncSession, grade_id: uuid.UUID, *, correlation_id: str | None = None, now: datetime | None = None
) -> tuple[GradeRecord, bool]:
    """Release the current grade to the student: (grade, newly released). Releasing twice changes nothing."""
    row = await session.scalar(
        select(Grade).where(Grade.id == grade_id).with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise GradingNotFoundError("grade not found")
    if not row.is_current:
        raise GradingConflictError("GRADE_NOT_CURRENT", f"revision {row.revision_no} was superseded by a regrade")
    if row.released_at is not None:
        return _grade(row), False
    moment = now or datetime.now(UTC)
    membership_id = await session.scalar(
        text("SELECT membership_id FROM attempts WHERE id = :a"), {"a": row.attempt_id}
    )
    updated = (
        await session.execute(
            update(Grade)
            .where(Grade.id == grade_id)
            .values(released_at=moment)
            .returning(Grade)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    await enqueue(
        session,
        EventEnvelopeContract(
            event_type=GRADE_RELEASED_EVENT,
            tenant_id=row.tenant_id,
            aggregate_type="grade",
            aggregate_id=row.id,
            correlation_id=correlation_id,
            payload={
                "tenant_id": str(row.tenant_id),
                "grade_id": str(row.id),
                "attempt_id": str(row.attempt_id),
                "membership_id": str(membership_id),
                "released_at": moment.isoformat(),
            },
        ),
    )
    return _grade(updated), True


async def file_appeal(
    session: AsyncSession,
    grade_id: uuid.UUID,
    *,
    membership_id: uuid.UUID,
    reason_text: str,
    question_version_id: uuid.UUID | None = None,
) -> AppealRecord:
    """A student appeals a released, current grade of their own attempt (optionally about one question)."""
    row = (
        await session.execute(
            text(
                "SELECT g.id, g.tenant_id, g.is_current, g.released_at, a.membership_id, a.question_order_ids "
                "FROM grades g JOIN attempts a ON a.id = g.attempt_id WHERE g.id = :g"
            ),
            {"g": grade_id},
        )
    ).one_or_none()
    if row is None or row.membership_id != membership_id:
        raise GradingNotFoundError("grade not found")
    if not row.is_current:
        raise GradingConflictError("GRADE_NOT_CURRENT", "appeal the current grade")
    if row.released_at is None:
        raise GradingConflictError("GRADE_NOT_RELEASED", "the grade has not been released yet")
    if question_version_id is not None and question_version_id not in set(row.question_order_ids):
        raise GradingConflictError("QUESTION_NOT_ON_PAPER", "the question is not on this attempt's paper")
    appeal = (
        await session.execute(
            insert(Appeal)
            .values(
                tenant_id=row.tenant_id,
                grade_id=grade_id,
                membership_id=membership_id,
                question_version_id=question_version_id,
                reason_text=reason_text.strip(),
            )
            .returning(Appeal)
        )
    ).scalar_one()
    return _appeal(appeal)


async def check_open_appeal(session: AsyncSession, appeal_id: uuid.UUID, attempt_id: uuid.UUID) -> None:
    """The appeal exists, is OPEN and is about a grade of `attempt_id` (checked before regrading for it)."""
    row = (
        await session.execute(
            text("SELECT ap.status, g.attempt_id FROM appeals ap JOIN grades g ON g.id = ap.grade_id WHERE ap.id = :p"),
            {"p": appeal_id},
        )
    ).one_or_none()
    if row is None or row.attempt_id != attempt_id:
        raise GradingNotFoundError("appeal not found for this attempt")
    if row.status != "OPEN":
        raise GradingConflictError("APPEAL_NOT_OPEN", f"the appeal is {row.status}")


async def resolve_appeal(
    session: AsyncSession,
    appeal_id: uuid.UUID,
    *,
    resolved_by: uuid.UUID,
    status: str,
    resolution_note: str,
    now: datetime | None = None,
) -> AppealRecord:
    row = (
        await session.execute(
            update(Appeal)
            .where(Appeal.id == appeal_id, Appeal.status == "OPEN")
            .values(
                status=status,
                resolution_note=resolution_note,
                resolved_by=resolved_by,
                resolved_at=now or datetime.now(UTC),
            )
            .returning(Appeal)
        )
    ).scalar_one_or_none()
    if row is None:
        raise GradingConflictError("APPEAL_NOT_OPEN", "the appeal is no longer open")
    return _appeal(row)
