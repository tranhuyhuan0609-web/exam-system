"""Public API of the explanations module (E04)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.explanations.internal.explainer import (
    CrossCheckResult,
    CrossCheckVerdict,
    SolutionSectionKind,
    SolutionStep,
    SolutionValidationReport,
    StepByStepSolutionDraft,
    UnsafeSandboxSnippetError,
    cross_check_solution_answer,
    validate_katex_fragment,
    validate_solution_draft,
    validate_sympy_snippet,
)

if TYPE_CHECKING:
    from app.modules.explanations.internal.service import (
        AnswerCheckRunView,
        ExplanationRunView,
        ExplanationValidationError,
        ExplanationVerificationError,
        SandboxEvaluator,
        SolutionStepInput,
        create_explanation_run,
        run_answer_check,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "AnswerCheckRunView",
        "ExplanationRunView",
        "ExplanationValidationError",
        "ExplanationVerificationError",
        "SandboxEvaluator",
        "SolutionStepInput",
        "create_explanation_run",
        "run_answer_check",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.explanations.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AnswerCheckRunView",
    "CrossCheckResult",
    "CrossCheckVerdict",
    "ExplanationRunView",
    "ExplanationValidationError",
    "ExplanationVerificationError",
    "SandboxEvaluator",
    "SolutionSectionKind",
    "SolutionStep",
    "SolutionStepInput",
    "SolutionValidationReport",
    "StepByStepSolutionDraft",
    "UnsafeSandboxSnippetError",
    "create_explanation_run",
    "cross_check_solution_answer",
    "run_answer_check",
    "validate_katex_fragment",
    "validate_solution_draft",
    "validate_sympy_snippet",
]
