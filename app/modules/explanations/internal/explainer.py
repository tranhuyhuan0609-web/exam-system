"""E04 AI Step-by-Step Solution & SymPy Cross-Check core rules: 4-step structure, KaTeX guard, AST sandbox filter, and cross-check verdict."""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

_FORBIDDEN_LATEX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\\href\s*\{\s*javascript:", re.IGNORECASE),
    re.compile(r"\\(write18|input|include|openout|immediate)\b", re.IGNORECASE),
    re.compile(r"<\s*script\b", re.IGNORECASE),
)

_FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {"__import__", "eval", "exec", "compile", "open", "globals", "locals", "vars", "getattr", "setattr", "delattr"}
)


class SolutionSectionKind(StrEnum):
    METHOD = "METHOD"
    TRANSFORM = "TRANSFORM"
    SUBSTITUTION = "SUBSTITUTION"
    CONCLUSION = "CONCLUSION"


class CrossCheckVerdict(StrEnum):
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"
    UNVERIFIABLE = "UNVERIFIABLE"


class UnsafeSandboxSnippetError(ValueError):
    """Raised when a SymPy verification snippet violates AST sandbox restrictions."""


@dataclass(frozen=True)
class SolutionStep:
    kind: SolutionSectionKind
    body: str


@dataclass(frozen=True)
class StepByStepSolutionDraft:
    explanation_run_id: str
    question_version_id: str
    steps: tuple[SolutionStep, ...]
    final_answer_text: str


@dataclass(frozen=True)
class SolutionValidationReport:
    valid_structure: bool
    valid_katex: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class CrossCheckResult:
    verdict: CrossCheckVerdict
    candidate_value: str
    sympy_value: str
    draft_save_allowed: bool
    reason: str


def validate_katex_fragment(text: str) -> list[str]:
    """Verify balanced KaTeX braces/$ delimiters and reject dangerous LaTeX directives."""
    errors: list[str] = []
    for pat in _FORBIDDEN_LATEX_PATTERNS:
        if pat.search(text):
            errors.append(f"forbidden LaTeX/HTML pattern matched: {pat.pattern}")

    brace_depth = 0
    single_dollar_open = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                errors.append("unexpected closing brace '}' in KaTeX")
                break
        elif ch == "$":
            if i + 1 < n and text[i + 1] == "$":
                # count block $$ separately by skipping pair
                end = text.find("$$", i + 2)
                if end == -1:
                    errors.append("unclosed display math delimiter '$$'")
                    break
                i = end + 2
                continue
            single_dollar_open = not single_dollar_open
        i += 1

    if brace_depth > 0:
        errors.append("unclosed '{' brace in KaTeX")
    if single_dollar_open:
        errors.append("unclosed inline math delimiter '$'")
    return errors


def validate_solution_draft(draft: StepByStepSolutionDraft) -> SolutionValidationReport:
    """Validate 4 pedagogical sections (Method -> Transform -> Substitution -> Conclusion) + KaTeX syntax."""
    errors: list[str] = []
    katex_errors: list[str] = []

    kinds_present = [step.kind for step in draft.steps]
    expected_order = [
        SolutionSectionKind.METHOD,
        SolutionSectionKind.TRANSFORM,
        SolutionSectionKind.SUBSTITUTION,
        SolutionSectionKind.CONCLUSION,
    ]
    if kinds_present != expected_order:
        errors.append(
            "steps must appear cleanly in order METHOD -> TRANSFORM -> SUBSTITUTION -> CONCLUSION"
        )

    for step in draft.steps:
        if not step.body.strip():
            errors.append(f"step {step.kind} has empty body")
        katex_errors.extend(validate_katex_fragment(step.body))

    if not draft.final_answer_text.strip():
        errors.append("final_answer_text must be non-empty")
    katex_errors.extend(validate_katex_fragment(draft.final_answer_text))

    all_errors = tuple(dict.fromkeys(errors + katex_errors))
    return SolutionValidationReport(
        valid_structure=not errors,
        valid_katex=not katex_errors,
        errors=all_errors,
    )


def validate_sympy_snippet(code: str) -> None:
    """Enforce strict AST allowlist before any Python/SymPy snippet enters sandbox execution."""
    if not code.strip():
        raise UnsafeSandboxSnippetError("SymPy snippet cannot be empty")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeSandboxSnippetError(f"invalid Python syntax in snippet: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise UnsafeSandboxSnippetError("imports are forbidden in SymPy sandbox snippets")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise UnsafeSandboxSnippetError(f"dunder attribute access {node.attr!r} is forbidden")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_BUILTINS:
            raise UnsafeSandboxSnippetError(f"forbidden builtin {node.id!r}")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise UnsafeSandboxSnippetError(f"dunder name {node.id!r} is forbidden")


def _parse_numeric_value(raw: str) -> float | None:
    cleaned = raw.strip().strip("$").strip()
    if not cleaned:
        return None
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1].strip()
    # Convert Vietnamese decimal comma when no thousands grouping ambiguity
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        val = float(Fraction(cleaned))
    except (ValueError, ZeroDivisionError):
        return None
    return val / 100.0 if is_percent else val


def cross_check_solution_answer(
    *,
    solution_answer: str,
    sympy_result: str,
    tolerance: float = 1e-4,
) -> CrossCheckResult:
    """Compare solution draft answer against independent SymPy result cleanly."""
    sol_norm = solution_answer.strip().strip("$").strip()
    sym_norm = sympy_result.strip().strip("$").strip()
    if not sol_norm or not sym_norm:
        return CrossCheckResult(
            verdict=CrossCheckVerdict.UNVERIFIABLE,
            candidate_value=sol_norm,
            sympy_value=sym_norm,
            draft_save_allowed=False,
            reason="missing solution or SymPy value",
        )

    num_sol = _parse_numeric_value(sol_norm)
    num_sym = _parse_numeric_value(sym_norm)
    if num_sol is not None and num_sym is not None:
        if math.isclose(num_sol, num_sym, rel_tol=tolerance, abs_tol=tolerance):
            return CrossCheckResult(
                verdict=CrossCheckVerdict.MATCHED,
                candidate_value=sol_norm,
                sympy_value=sym_norm,
                draft_save_allowed=True,
                reason="numeric/fractional equivalence within tolerance",
            )
        return CrossCheckResult(
            verdict=CrossCheckVerdict.MISMATCH,
            candidate_value=sol_norm,
            sympy_value=sym_norm,
            draft_save_allowed=False,
            reason=f"numeric mismatch: solution={num_sol} vs sympy={num_sym}",
        )

    if sol_norm.lower() == sym_norm.lower():
        return CrossCheckResult(
            verdict=CrossCheckVerdict.MATCHED,
            candidate_value=sol_norm,
            sympy_value=sym_norm,
            draft_save_allowed=True,
            reason="exact symbolic match",
        )

    return CrossCheckResult(
        verdict=CrossCheckVerdict.MISMATCH,
        candidate_value=sol_norm,
        sympy_value=sym_norm,
        draft_save_allowed=False,
        reason="symbolic expression mismatch",
    )
