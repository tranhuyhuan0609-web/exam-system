"""Public API of the explanations module (E04)."""

from .internal.explainer import (
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

__all__ = [
    "CrossCheckResult",
    "CrossCheckVerdict",
    "SolutionSectionKind",
    "SolutionStep",
    "SolutionValidationReport",
    "StepByStepSolutionDraft",
    "UnsafeSandboxSnippetError",
    "cross_check_solution_answer",
    "validate_katex_fragment",
    "validate_solution_draft",
    "validate_sympy_snippet",
]
