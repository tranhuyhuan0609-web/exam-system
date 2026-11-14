"""C02 quality service: independent SymPy check of a question candidate or of a C01 question version, stored as an
append-only verification run.

The expression is evaluated ONLY by the isolated worker through `verify_numeric_answer` (H-02, fail closed:
without the worker the run is recorded with `SANDBOX_WORKER_UNAVAILABLE` and the candidate is left UNCHECKED).
A match moves the candidate to AUTO_VERIFIED, a mismatch to DISPUTED; only a human decision in A09 can make it VERIFIED.
The verifier is a parameter so tests (and Windows machines, which have no worker) can inject an evaluator.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.quality.internal.models import VerificationRun
from app.modules.quality.internal.sympy_runner import AnswerCheck, verify_numeric_answer
from app.modules.questions.public import get_verification_target, record_automatic_verification

Verifier = Callable[[str, str, Decimal], AnswerCheck]
TOOL_VERSION = "sympy-sandbox-v1"
MAX_EXPR_CHARS = 500
MAX_ANSWER_CHARS = 100


class VerificationNotFoundError(LookupError):
    """The candidate or run does not exist or belongs to another tenant (same answer)."""


class VerificationConflictError(ValueError):
    """The candidate cannot be verified now; `code` is NOT_PENDING."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class VerificationRunRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    question_version_id: uuid.UUID | None
    candidate_id: uuid.UUID | None
    verifier_type: str
    model_or_tool_version: str
    computed_answer: str | None
    is_match_with_source: bool | None
    findings: dict[str, Any]
    created_at: datetime


def _record(row: VerificationRun) -> VerificationRunRecord:
    values = {f: getattr(row, f) for f in VerificationRunRecord.__dataclass_fields__ if f != "findings"}
    return VerificationRunRecord(findings=dict(row.findings_json), **values)


def _default_verifier(expr: str, source_answer: str, tolerance: Decimal) -> AnswerCheck:
    return verify_numeric_answer(expr, source_answer, tolerance)


async def verify_candidate_numeric(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    expression: str,
    source_answer: str,
    tolerance: Decimal = Decimal("0.01"),
    verifier: Verifier | None = None,
) -> tuple[VerificationRunRecord, str]:
    """Check `expression` against `source_answer` of a NEEDS_REVIEW candidate: (run, candidate verification status)."""
    expr = expression.strip()
    answer = source_answer.strip()
    if not expr or len(expr) > MAX_EXPR_CHARS:
        raise ValueError(f"expression must have 1..{MAX_EXPR_CHARS} characters")
    if not answer or len(answer) > MAX_ANSWER_CHARS:
        raise ValueError(f"source_answer must have 1..{MAX_ANSWER_CHARS} characters")
    row = (
        await session.execute(
            text("SELECT candidate_status, verification_status FROM question_candidates WHERE id = :i FOR UPDATE"),
            {"i": candidate_id},
        )
    ).one_or_none()
    if row is None:
        raise VerificationNotFoundError("question candidate not found")
    if row.candidate_status != "NEEDS_REVIEW":
        raise VerificationConflictError(
            "NOT_PENDING", f"candidate is {row.candidate_status}, only NEEDS_REVIEW is checked"
        )
    check = (verifier or _default_verifier)(expr, answer, tolerance)
    if check.error is not None:
        status = row.verification_status
    else:
        status = "AUTO_VERIFIED" if check.matches else "DISPUTED"
    findings = {
        "expression": expr,
        "source_answer": answer,
        "tolerance": str(tolerance),
        "source": check.source,
        "abs_diff": check.abs_diff,
        "error": check.error,
    }
    saved = (
        await session.execute(
            insert(VerificationRun)
            .values(
                tenant_id=tenant_id,
                candidate_id=candidate_id,
                verifier_type="SYMPY_SOLVER",
                model_or_tool_version=TOOL_VERSION,
                computed_answer=check.computed,
                is_match_with_source=None if check.error is not None else check.matches,
                findings_json=findings,
            )
            .returning(VerificationRun)
        )
    ).scalar_one()
    if status != row.verification_status:
        await session.execute(
            text("UPDATE question_candidates SET verification_status = :s WHERE id = :i"),
            {"s": status, "i": candidate_id},
        )
    return _record(saved), status


def source_answer_of(item_type: str, answer_schema: Mapping[str, Any]) -> str | None:
    """The stored answer a numeric check compares with: SHORT_NUMERIC keeps it in `answer_schema['value']`."""
    if item_type != "SHORT_NUMERIC":
        return None
    value = answer_schema.get("value")
    return None if value is None or isinstance(value, bool) else str(value).strip() or None


async def verify_question_version_numeric(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    tenant_id: uuid.UUID,
    expression: str,
    source_answer: str | None = None,
    tolerance: Decimal = Decimal("0.01"),
    verifier: Verifier | None = None,
) -> tuple[VerificationRunRecord, str]:
    """Check `expression` against the version's answer: (run, question verification status afterwards).

    Without `source_answer` the stored answer of a SHORT_NUMERIC version is used. C01 decides whether the
    outcome moves the question (`record_automatic_verification`): only the newest, unpublished version of a
    question no human has decided on yet; the run itself is always recorded.
    """
    expr = expression.strip()
    if not expr or len(expr) > MAX_EXPR_CHARS:
        raise ValueError(f"expression must have 1..{MAX_EXPR_CHARS} characters")
    target = await get_verification_target(session, version_id)
    if target is None:
        raise VerificationNotFoundError("question version not found")
    answer = (source_answer or "").strip() or source_answer_of(target.item_type, target.answer_schema)
    if not answer:
        raise ValueError(f"source_answer is required: a {target.item_type} version stores no numeric answer")
    if len(answer) > MAX_ANSWER_CHARS:
        raise ValueError(f"source_answer must have 1..{MAX_ANSWER_CHARS} characters")
    check = (verifier or _default_verifier)(expr, answer, tolerance)
    matches = None if check.error is not None else check.matches
    findings = {
        "expression": expr,
        "source_answer": answer,
        "source_answer_from": "request" if (source_answer or "").strip() else "question_version_answers",
        "tolerance": str(tolerance),
        "source": check.source,
        "abs_diff": check.abs_diff,
        "error": check.error,
    }
    saved = (
        await session.execute(
            insert(VerificationRun)
            .values(
                tenant_id=tenant_id,
                question_version_id=version_id,
                verifier_type="SYMPY_SOLVER",
                model_or_tool_version=TOOL_VERSION,
                computed_answer=check.computed,
                is_match_with_source=matches,
                findings_json=findings,
            )
            .returning(VerificationRun)
        )
    ).scalar_one()
    status = await record_automatic_verification(session, version_id, matches)
    return _record(saved), status


async def get_verification_run(session: AsyncSession, run_id: uuid.UUID) -> VerificationRunRecord | None:
    row = await session.scalar(select(VerificationRun).where(VerificationRun.id == run_id))
    return None if row is None else _record(row)
