"""Tests for E04 step-by-step solution & SymPy cross-check core."""

from __future__ import annotations

import time

import pytest

from app.modules.explanations.public import (
    CrossCheckVerdict,
    SolutionSectionKind,
    SolutionStep,
    StepByStepSolutionDraft,
    UnsafeSandboxSnippetError,
    cross_check_solution_answer,
    validate_katex_fragment,
    validate_solution_draft,
    validate_sympy_snippet,
)


def _draft(body: str = "Ap dung $x^2$.", final: str = "$0,4$") -> StepByStepSolutionDraft:
    return StepByStepSolutionDraft(
        explanation_run_id="exp-1",
        question_version_id="qv-1",
        steps=(
            SolutionStep(SolutionSectionKind.METHOD, body),
            SolutionStep(SolutionSectionKind.TRANSFORM, "Bien doi."),
            SolutionStep(SolutionSectionKind.SUBSTITUTION, "Thay so."),
            SolutionStep(SolutionSectionKind.CONCLUSION, "Ket luan."),
        ),
        final_answer_text=final,
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


# ---------------------------------------------------------------------------
# BLOCKER: the AST pre-filter is an allowlist (execution stays in the isolated worker).
# ---------------------------------------------------------------------------

ESCAPES = [
    # the finding: MatchClass keyword attributes reached object.__subclasses__()
    "match 1:\n case int(__class__=c): pass",
    "breakpoint()",
    "input()",
    "type(1)",
    "9**9**9**9",
    "'{0.__class__.__mro__}'.format(1)",
    # imports / dunder / builtins
    "import os\nos.system('id')",
    "from subprocess import Popen",
    "__import__('os').system('ls')",
    "().__class__.__base__.__subclasses__()",
    "open('/etc/passwd').read()",
    "eval('1')",
    "exec('1')",
    "compile('1', 'f', 'eval')",
    "globals()",
    # structural nodes outside the allowlist
    "x.real",
    "(1).real",
    "x[0]",
    "[i for i in range(3)]",
    "{i for i in range(3)}",
    "{i: i for i in range(3)}",
    "(i for i in range(3))",
    "lambda: 1",
    "(lambda x: x)(1)",
    "f'{x}'",
    "f'{x!r:>{10}}'",
    "'abc'",
    "b'abc'",
    "(y := 5)",
    "sin(*x)",
    "sin(**x)",
    "sin(x=1)",
    "[1, 2]",
    "(1, 2)",
    "{1: 2}",
    "x if x else y",
    "x and y",
    "not x",
    "x < y",
    "x == y",
    "x % 2",
    "x // 2",
    "x @ y",
    "x & y",
    "x | y",
    "~x",
    "...",
    "None",
    "True",
    "1j",
    "await x",
    "x = 1",
    "x\ny",
    "x; y",
    "# comment\n1",
    "",
    "   ",
]


@pytest.mark.parametrize("snippet", ESCAPES)
def test_ast_prefilter_rejects_every_escape(snippet: str) -> None:
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(snippet)


@pytest.mark.parametrize("name", ["foo", "os", "__builtins__", "X", "SIN", "Sin", "lambda_", "sympify", "S"])
def test_names_outside_the_allowed_set_are_rejected_case_sensitively(name: str) -> None:
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(name)
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(f"{name}(1)")
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(f"1 + {name}")


@pytest.mark.parametrize(
    "snippet",
    [
        "(x**2 - 1)/(x - 1)",
        "3/4",
        "sqrt(2)*pi + E",
        "-x + +y",
        "Rational(3, 4)",
        "Abs(x) - log(2)",
        "sin(x)**2 + cos(x)**2",
        "2^3",  # caret is the worker's power operator
        "1.5e3 * 2",
        "x**-2",
        "2**10",
    ],
)
def test_ast_prefilter_accepts_plain_math(snippet: str) -> None:
    validate_sympy_snippet(snippet)


@pytest.mark.parametrize(
    "snippet",
    ["x**11", "2**100", "x**(-11)", "x**y", "x**(1+1)", "2**3**4", "x**2**2", "x**1e9", "2^100"],
)
def test_exponents_are_capped_and_must_be_literal(snippet: str) -> None:
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(snippet)


def test_too_many_power_operators_are_rejected() -> None:
    with pytest.raises(UnsafeSandboxSnippetError, match="power"):
        validate_sympy_snippet("((x**2)**2)**2 + x**2")


def test_length_and_node_count_are_capped() -> None:
    with pytest.raises(UnsafeSandboxSnippetError, match="long"):
        validate_sympy_snippet("1+" * 300 + "1")
    # short enough in characters, but too many AST nodes
    with pytest.raises(UnsafeSandboxSnippetError, match="nodes"):
        validate_sympy_snippet("+".join(["x"] * 150))
    validate_sympy_snippet("+".join(["x"] * 20))


def test_deep_nesting_and_huge_literals_do_not_crash_or_hang() -> None:
    start = time.monotonic()
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet("(" * 400 + "1" + ")" * 400)
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet("1" * 400)
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet("9" * 40)
    assert time.monotonic() - start < 2.0


@pytest.mark.parametrize("bad", [None, 5, b"1+1", ["1"], "1\x00+1", "\u0430lpha", "x\u00a0+ 1", "x\u2028+1"])
def test_non_string_and_exotic_input_fails_closed(bad: object) -> None:
    with pytest.raises(UnsafeSandboxSnippetError):
        validate_sympy_snippet(bad)  # type: ignore[arg-type]


def test_allowed_names_come_from_the_quality_module() -> None:
    from app.modules.quality.public import ALLOWED_SYMPY_NAMES

    for name in sorted(ALLOWED_SYMPY_NAMES):
        validate_sympy_snippet(name)
        validate_sympy_snippet(f"{name}(1)")


def test_module_never_executes_or_imports_sympy() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app/modules/explanations/internal/explainer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    called |= {
        n.func.attr  # re.compile is a regex builder, not code execution
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and not (isinstance(n.func.value, ast.Name) and n.func.value.id == "re")
    }
    assert not called & {"eval", "exec", "compile", "sympify", "parse_expr", "__import__"}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m == "sympy" or m.startswith("sympy.") for m in imported)


# ---------------------------------------------------------------------------
# KaTeX / LaTeX safety: allowlist of commands.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "\\url{javascript:alert(1)}",
        "\\url {http://evil}",
        "\\href{ data:text/html,<script>}{x}",
        "\\href {https://evil}{x}",
        "\\href\t{https://evil}{x}",
        "\\htmlClass{a}{x}",
        "\\htmlClass {a}{x}",
        "\\htmlId{a}{x}",
        "\\htmlStyle{color:red}{x}",
        "\\htmlData{a=b}{x}",
        "\\includegraphics{x.png}",
        "\\includegraphics [width=1]{x.png}",
        "\\def\\a{1}",
        "\\newcommand{\\a}{1}",
        "\\renewcommand{\\a}{1}",
        "\\input{/etc/passwd}",
        "\\input {x}",
        "\\include{x}",
        "\\write18{id}",
        "\\write {x}",
        "\\catcode`\\~=13",
        "\\immediate\\write18{id}",
        "\\openout1=x",
        "\\color{red}{x}",
        "\\textcolor{red}{x}",
        "\\unknowncommand{x}",
        "\\begin{verbatim}x\\end{verbatim}",
        "\\begin {evil}x\\end{evil}",
        "<script>alert(1)</script>",
        "< SCRIPT src=x>",
        "<img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        "JaVaScRiPt :alert(1)",
        "x \\\\ \\href{https://evil}{y}",  # an escaped backslash must not hide the next command
    ],
)
def test_katex_guard_rejects_dangerous_or_unknown_commands(bad: str) -> None:
    errors = validate_katex_fragment(f"$ {bad} $")
    assert errors, bad
    assert any("forbidden" in e for e in errors), (bad, errors)


@pytest.mark.parametrize(
    "good",
    [
        "$\\frac{a}{b} + \\sqrt{2} \\cdot \\pi$",
        "$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$",
        "$$\\begin{cases} x=1 \\\\ y=2 \\end{cases}$$",
        "$\\begin{pmatrix} 1 & 0 \\\\ 0 & 1 \\end{pmatrix}$",
        "$a \\le b \\ne c \\, d \\; e \\: f \\! g \\{ h \\}$",
        "$\\overrightarrow{AB} \\perp \\vec{u}$",
        "Cho $a<b$ va $x \\in \\mathbb{R}$.",
        "$\\text{Vậy } x = 2$",
        "$\\ce{2H2 + O2 -> 2H2O}$ va $\\pu{0,5 mol/L}$",
        "100\\% dung",
    ],
)
def test_katex_guard_accepts_normal_math(good: str) -> None:
    assert validate_katex_fragment(good) == []


def test_katex_guard_rejects_unbalanced_and_oversized_input() -> None:
    assert any("closing brace" in e for e in validate_katex_fragment("$x}$"))
    assert any("unclosed '{'" in e for e in validate_katex_fragment("${x$"))
    assert any("display" in e for e in validate_katex_fragment("$$x"))
    assert any("inline" in e for e in validate_katex_fragment("$x"))
    assert any("too long" in e for e in validate_katex_fragment("a" * 50_000))
    assert any("deep" in e for e in validate_katex_fragment("{" * 500 + "}" * 500))


@pytest.mark.parametrize("bad", [None, 5, b"x"])
def test_katex_guard_fails_closed_on_non_strings(bad: object) -> None:
    assert validate_katex_fragment(bad)  # type: ignore[arg-type]


def test_solution_draft_checks_order_empty_steps_and_final_answer() -> None:
    swapped = StepByStepSolutionDraft(
        "r", "q",
        (
            SolutionStep(SolutionSectionKind.TRANSFORM, "a"),
            SolutionStep(SolutionSectionKind.METHOD, "b"),
            SolutionStep(SolutionSectionKind.SUBSTITUTION, "c"),
            SolutionStep(SolutionSectionKind.CONCLUSION, "d"),
        ),
        "1",
    )
    assert validate_solution_draft(swapped).valid_structure is False
    assert validate_solution_draft(_draft(body="   ")).valid_structure is False
    assert validate_solution_draft(_draft(final="  ")).valid_structure is False
    assert validate_solution_draft(StepByStepSolutionDraft("r", "q", (), "1")).valid_structure is False
    assert validate_solution_draft(_draft()).errors == ()


def test_solution_draft_with_dangerous_latex_in_final_answer_is_flagged() -> None:
    report = validate_solution_draft(_draft(final="\\href{https://evil}{1}"))
    assert report.valid_katex is False


# ---------------------------------------------------------------------------
# Cross-check numeric parsing: Fraction/Decimal with caps, no float tolerance fudge.
# ---------------------------------------------------------------------------


def test_cross_check_supports_vietnamese_comma_fractions_and_catches_mismatch() -> None:
    matched = cross_check_solution_answer(solution_answer="$0,75$", sympy_result="3/4")
    assert matched.verdict == CrossCheckVerdict.MATCHED
    assert matched.draft_save_allowed is True

    pct = cross_check_solution_answer(solution_answer="25%", sympy_result="0.25")
    assert pct.verdict == CrossCheckVerdict.MATCHED

    mismatch = cross_check_solution_answer(solution_answer="0,5", sympy_result="3/4")
    assert mismatch.verdict == CrossCheckVerdict.MISMATCH
    assert mismatch.draft_save_allowed is False


def _verdict(a: str, b: str, **kw) -> CrossCheckVerdict:
    return cross_check_solution_answer(solution_answer=a, sympy_result=b, **kw).verdict


def test_distinct_small_numbers_are_not_equal() -> None:
    # Regression: abs_tol=1e-4 made 0.00001 match 0.00002 (and 0 match 0.00005).
    assert _verdict("0.00001", "0.00002") == CrossCheckVerdict.MISMATCH
    assert _verdict("0", "0.00005") == CrossCheckVerdict.MISMATCH
    assert _verdict("1e-9", "2e-9") == CrossCheckVerdict.MISMATCH
    assert _verdict("0.00001", "0.00001") == CrossCheckVerdict.MATCHED
    assert _verdict("0", "0.0") == CrossCheckVerdict.MATCHED


def test_relative_tolerance_still_accepts_rounded_answers() -> None:
    assert _verdict("0.3333", "1/3") == CrossCheckVerdict.MATCHED
    assert _verdict("0.33", "1/3") == CrossCheckVerdict.MISMATCH
    assert _verdict("1000.4", "1000") == CrossCheckVerdict.MISMATCH
    assert _verdict("0.33", "1/3", tolerance=0.01) == CrossCheckVerdict.MATCHED


@pytest.mark.parametrize("tol", [float("nan"), float("inf"), -0.1, 0.5, True, None])
def test_tolerance_must_be_small_and_finite(tol: object) -> None:
    with pytest.raises(ValueError):
        cross_check_solution_answer(solution_answer="1", sympy_result="1", tolerance=tol)  # type: ignore[arg-type]


def test_huge_exponents_do_not_hang_or_crash() -> None:
    # Regression: Fraction("1e9999999") hung ~10 s and "1e400" raised OverflowError.
    start = time.monotonic()
    for raw in ("1e9999999", "1e400", "-1e400", "1e-9999999", "9" * 5000, "1/" + "9" * 5000):
        result = cross_check_solution_answer(solution_answer=raw, sympy_result="1")
        assert result.verdict == CrossCheckVerdict.UNVERIFIABLE or result.verdict == CrossCheckVerdict.MISMATCH
        assert result.draft_save_allowed is False
    assert time.monotonic() - start < 2.0


@pytest.mark.parametrize("raw", ["1,000", "2,500", "999,999"])
def test_ambiguous_thousands_comma_is_not_silently_read_as_decimal(raw: str) -> None:
    # Regression: "1,000" was read as 1.0.
    result = cross_check_solution_answer(solution_answer=raw, sympy_result="1")
    assert result.draft_save_allowed is False
    assert result.verdict != CrossCheckVerdict.MATCHED


def test_decimal_comma_with_leading_zero_and_multiple_separators() -> None:
    assert _verdict("0,125", "1/8") == CrossCheckVerdict.MATCHED
    assert _verdict("1,000,000", "1000000") != CrossCheckVerdict.MATCHED
    assert _verdict("1.2.3", "1.2") != CrossCheckVerdict.MATCHED


def test_symbolic_comparison_is_case_sensitive() -> None:
    # Regression: "x" matched "X".
    assert _verdict("x", "X") == CrossCheckVerdict.MISMATCH
    assert _verdict("x + 1", "x+1") == CrossCheckVerdict.MATCHED
    assert _verdict("x + 1", "x + 2") == CrossCheckVerdict.MISMATCH


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_non_finite_tokens_are_never_a_match_even_when_identical(raw: str) -> None:
    result = cross_check_solution_answer(solution_answer=raw, sympy_result=raw)
    assert result.verdict == CrossCheckVerdict.UNVERIFIABLE
    assert result.draft_save_allowed is False


def test_empty_zero_denominator_and_oversized_inputs_are_unverifiable_or_mismatch() -> None:
    assert _verdict("", "1") == CrossCheckVerdict.UNVERIFIABLE
    assert _verdict("1", "   ") == CrossCheckVerdict.UNVERIFIABLE
    assert _verdict("$$", "1") == CrossCheckVerdict.UNVERIFIABLE
    assert _verdict("1/0", "1") != CrossCheckVerdict.MATCHED
    assert _verdict("x" * 5000, "x" * 5000) == CrossCheckVerdict.UNVERIFIABLE


@pytest.mark.parametrize("bad", [None, 5])
def test_non_string_answers_fail_closed(bad: object) -> None:
    result = cross_check_solution_answer(solution_answer=bad, sympy_result="1")  # type: ignore[arg-type]
    assert result.verdict == CrossCheckVerdict.UNVERIFIABLE
    assert result.draft_save_allowed is False
