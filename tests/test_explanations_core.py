"""Tests for E04 step-by-step solution & SymPy cross-check core."""

from __future__ import annotations

import pytest

from app.modules.explanations.public import (
    CrossCheckVerdict,
    SolutionSectionKind,
    SolutionStep,
    StepByStepSolutionDraft,
    UnsafeSandboxSnippetError,
    cross_check_solution_answer,
    validate_solution_draft,
    validate_sympy_snippet,
)


def test_validates_4_step_solution_structure_and_katex_safety() -> None:
    good = StepByStepSolutionDraft(
        explanation_run_id="exp-1",
        question_version_id="qv-1",
        steps=(
            SolutionStep(SolutionSectionKind.METHOD, "Áp dụng công thức Bayes $P(A|B) = \\frac{P(B|A)P(A)}{P(B)}$."),
            SolutionStep(SolutionSectionKind.TRANSFORM, "$$P(B) = P(B|A)P(A) + P(B|\\bar{A})P(\\bar{A})$$"),
            SolutionStep(SolutionSectionKind.SUBSTITUTION, "$P(A|B) = \\frac{0,8 \\times 0,2}{0,4} = 0,4$."),
            SolutionStep(SolutionSectionKind.CONCLUSION, "Vậy kết quả cần tìm là $0,4$."),
        ),
        final_answer_text="$0,4$",
    )
    report = validate_solution_draft(good)
    assert report.valid_structure is True
    assert report.valid_katex is True
    assert report.errors == ()

    bad_xss = StepByStepSolutionDraft(
        explanation_run_id="exp-2",
        question_version_id="qv-1",
        steps=(
            SolutionStep(SolutionSectionKind.METHOD, "\\href{javascript:alert(1)}{x}"),
            SolutionStep(SolutionSectionKind.TRANSFORM, "$x = \\frac{1}{2$"),
            SolutionStep(SolutionSectionKind.SUBSTITUTION, "x=0.5"),
            SolutionStep(SolutionSectionKind.CONCLUSION, "0.5"),
        ),
        final_answer_text="0.5",
    )
    bad_report = validate_solution_draft(bad_xss)
    assert bad_report.valid_katex is False
    assert any("forbidden LaTeX" in e for e in bad_report.errors)
    assert any("unclosed '{'" in e for e in bad_report.errors)


def test_sympy_sandbox_ast_guard_blocks_imports_and_dunder_escapes() -> None:
    validate_sympy_snippet("expr = (x**2 - 1)/(x - 1)\nans = 3/4")

    for malicious in (
        "import os\nos.system('id')",
        "from subprocess import Popen",
        "__import__('os').system('ls')",
        "x = ().__class__.__base__.__subclasses__()",
        "open('/etc/passwd').read()",
    ):
        with pytest.raises(UnsafeSandboxSnippetError):
            validate_sympy_snippet(malicious)


def test_cross_check_supports_vietnamese_comma_fractions_and_catches_mismatch() -> None:
    matched = cross_check_solution_answer(solution_answer="$0,75$", sympy_result="3/4")
    assert matched.verdict == CrossCheckVerdict.MATCHED
    assert matched.draft_save_allowed is True

    pct = cross_check_solution_answer(solution_answer="25%", sympy_result="0.25")
    assert pct.verdict == CrossCheckVerdict.MATCHED

    mismatch = cross_check_solution_answer(solution_answer="0,5", sympy_result="3/4")
    assert mismatch.verdict == CrossCheckVerdict.MISMATCH
    assert mismatch.draft_save_allowed is False
