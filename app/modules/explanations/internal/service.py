"""Database service layer for E04 AI Step-by-Step Explanations & isolated SymPy cross-check (H-02)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.explanations.internal.explainer import (
    CrossCheckVerdict,
    SolutionSectionKind,
    SolutionStep,
    StepByStepSolutionDraft,
    UnsafeSandboxSnippetError,
    cross_check_solution_answer,
    validate_solution_draft,
    validate_sympy_snippet,
)
from app.modules.explanations.internal.models import ExplanationRun
from app.modules.quality.public import SandboxResult, evaluate_expression

SandboxEvaluator = Callable[[str], SandboxResult]


class ExplanationValidationError(ValueError):
    """Raised when the 4-step solution draft or KaTeX markup fails validation."""


class ExplanationVerificationError(ValueError):
    """Raised when isolated SymPy verification rejects a mismatched or unverifiable draft."""

    def __init__(self, check: AnswerCheckRunView) -> None:
        super().__init__(f"SymPy verification rejected draft ({check.verdict.value}): {check.reason}")
        self.check = check


@dataclass(frozen=True)
class SolutionStepInput:
    kind: str
    body: str


@dataclass(frozen=True)
class AnswerCheckRunView:
    sympy_expr: str
    expected_answer: str
    sympy_value: str | None
    verdict: CrossCheckVerdict
    sympy_verified: bool
    draft_save_allowed: bool
    sandbox_error: str | None
    reason: str


@dataclass(frozen=True)
class ExplanationRunView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    question_version_id: uuid.UUID
    draft_rationale_json: dict[str, Any]
    sympy_verified: bool
    status: str
    created_at: datetime
    verdict: str | None


def run_answer_check(
    *,
    sympy_expr: str,
    expected_answer: str,
    tolerance: float = 1e-4,
    sandbox_evaluator: SandboxEvaluator | None = None,
) -> AnswerCheckRunView:
    """AST-prefilter `sympy_expr`, evaluate it in the isolated worker (H-02), and cross-check."""
    validate_sympy_snippet(sympy_expr)
    evaluator = sandbox_evaluator or evaluate_expression
    sandbox_result = evaluator(sympy_expr.strip())

    if not sandbox_result.ok or not sandbox_result.value:
        err_code = sandbox_result.error or "SANDBOX_ERROR"
        detail = f"isolated sandbox failed ({err_code})"
        if sandbox_result.detail:
            detail = f"{detail}: {sandbox_result.detail}"
        return AnswerCheckRunView(
            sympy_expr=sympy_expr.strip(),
            expected_answer=expected_answer,
            sympy_value=None,
            verdict=CrossCheckVerdict.UNVERIFIABLE,
            sympy_verified=False,
            draft_save_allowed=False,
            sandbox_error=err_code,
            reason=detail,
        )

    cross = cross_check_solution_answer(
        solution_answer=expected_answer,
        sympy_result=sandbox_result.value,
        tolerance=tolerance,
    )
    return AnswerCheckRunView(
        sympy_expr=sympy_expr.strip(),
        expected_answer=expected_answer,
        sympy_value=sandbox_result.value,
        verdict=cross.verdict,
        sympy_verified=cross.verdict == CrossCheckVerdict.MATCHED,
        draft_save_allowed=cross.draft_save_allowed,
        sandbox_error=None,
        reason=cross.reason,
    )


async def create_explanation_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    question_version_id: uuid.UUID,
    steps: Sequence[SolutionStepInput],
    final_answer_text: str,
    sympy_expr: str | None = None,
    tolerance: float = 1e-4,
    sandbox_evaluator: SandboxEvaluator | None = None,
) -> ExplanationRunView:
    """Validate 4-step KaTeX solution draft, run SymPy sandbox cross-check, and persist to `explanation_runs`."""
    parsed_steps: list[SolutionStep] = []
    for item in steps:
        try:
            kind = SolutionSectionKind(item.kind)
        except ValueError as exc:
            raise ExplanationValidationError(f"invalid solution step kind {item.kind!r}") from exc
        parsed_steps.append(SolutionStep(kind=kind, body=item.body))

    draft = StepByStepSolutionDraft(
        explanation_run_id="pending",
        question_version_id=str(question_version_id),
        steps=tuple(parsed_steps),
        final_answer_text=final_answer_text,
    )
    report = validate_solution_draft(draft)
    if not report.valid_structure or not report.valid_katex:
        raise ExplanationValidationError("; ".join(report.errors))

    sympy_verified = False
    verdict_value: str | None = None
    sympy_value: str | None = None
    cleaned_expr = sympy_expr.strip() if isinstance(sympy_expr, str) and sympy_expr.strip() else None

    if cleaned_expr is not None:
        check = run_answer_check(
            sympy_expr=cleaned_expr,
            expected_answer=final_answer_text,
            tolerance=tolerance,
            sandbox_evaluator=sandbox_evaluator,
        )
        if not check.draft_save_allowed:
            raise ExplanationVerificationError(check)
        sympy_verified = True
        verdict_value = check.verdict.value
        sympy_value = check.sympy_value

    rationale_payload: dict[str, Any] = {
        "steps": [{"kind": s.kind.value, "body": s.body.strip()} for s in parsed_steps],
        "final_answer_text": final_answer_text.strip(),
        "sympy_expr": cleaned_expr,
        "sympy_value": sympy_value,
        "verdict": verdict_value,
    }

    row = ExplanationRun(
        tenant_id=tenant_id,
        question_version_id=question_version_id,
        draft_rationale_json=rationale_payload,
        sympy_verified=sympy_verified,
        status="PENDING_APPROVAL",
    )
    session.add(row)
    await session.flush()

    return ExplanationRunView(
        id=row.id,
        tenant_id=row.tenant_id,
        question_version_id=row.question_version_id,
        draft_rationale_json=dict(row.draft_rationale_json),
        sympy_verified=row.sympy_verified,
        status=row.status,
        created_at=row.created_at,
        verdict=verdict_value,
    )


__all__ = [
    "AnswerCheckRunView",
    "ExplanationRunView",
    "ExplanationValidationError",
    "ExplanationVerificationError",
    "SandboxEvaluator",
    "SolutionStepInput",
    "UnsafeSandboxSnippetError",
    "create_explanation_run",
    "run_answer_check",
]
