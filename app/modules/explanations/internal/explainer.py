"""E04 AI Step-by-Step Solution & SymPy Cross-Check core rules.

4-step structure, KaTeX allowlist guard, AST allowlist pre-filter and cross-check verdict.

Guardrail H-02: this module NEVER evaluates an expression. It does not import SymPy and never calls
``sympify``, ``eval``, ``exec`` or ``compile``. ``validate_sympy_snippet`` is only a cheap pre-filter in front
of the isolated worker of ``app.modules.quality``; the worker stays the real security boundary.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from app.modules.quality.public import ALLOWED_SYMPY_NAMES

# --- AST pre-filter limits -------------------------------------------------------------------------------------
MAX_SNIPPET_LENGTH = 500  # mirrors the worker's MAX_EXPR_LEN
MAX_AST_NODES = 120
MAX_EXPONENT = 10
MAX_POW_NODES = 3
MAX_CALL_ARGS = 4
MAX_INT_LITERAL_DIGITS = 15
_SNIPPET_CHARS_RE = re.compile(r"[0-9A-Za-z_+\-*/^().,\s]*")

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)

# --- KaTeX guard limits ----------------------------------------------------------------------------------------
MAX_KATEX_LENGTH = 20_000
MAX_KATEX_BRACE_DEPTH = 60

# Letter commands that ordinary exam solutions need. Anything else (\url, \href, \html*, \includegraphics, \def,
# \newcommand, \input, \write, \catcode, \color, ...) is rejected, so the guard needs no list of bad commands.
ALLOWED_LATEX_COMMANDS: frozenset[str] = frozenset(
    """
    frac dfrac tfrac cfrac sqrt cdot times div pm mp ast star circ bullet oplus otimes
    le leq ge geq ne neq lt gt approx equiv sim simeq cong propto ll gg
    to rightarrow leftarrow Rightarrow Leftarrow leftrightarrow Leftrightarrow longrightarrow Longrightarrow
    mapsto iff implies therefore because uparrow downarrow
    infty sum prod int iint oint lim limsup liminf sup inf max min gcd lcm det deg dim ker arg
    log ln lg exp sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho
    sigma tau upsilon phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    left right big Big bigg Bigg bigl bigr Bigl Bigr
    text textbf textit textrm mathrm mathbf mathit mathsf mathbb mathcal mathfrak operatorname boldsymbol
    overline underline bar hat vec dot ddot widehat widetilde tilde overrightarrow overleftarrow
    overbrace underbrace xrightarrow xleftarrow stackrel overset underset boxed
    cup cap subset subseteq supset supseteq in notin ni setminus emptyset varnothing
    forall exists nexists neg land lor lnot wedge vee
    partial nabla ell hbar angle perp parallel triangle square mid nmid
    ldots cdots vdots ddots dots quad qquad space
    begin end hline cr newline binom choose over atop pmod mod bmod
    displaystyle textstyle scriptstyle limits nolimits prime degree
    lbrace rbrace langle rangle lfloor rfloor lceil rceil lvert rvert lVert rVert
    """.split()
)
ALLOWED_LATEX_ENVIRONMENTS: frozenset[str] = frozenset(
    {
        "array", "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix",
        "cases", "aligned", "align", "align*", "gathered", "split", "alignedat",
    }
)  # fmt: skip
# Control symbols such as \, \; \{ \% \\ ; letters are handled by the allowlist above.
_ALLOWED_LATEX_SYMBOLS: frozenset[str] = frozenset(",;:! {}|\\%&_#$()[]>")
_LATEX_TOKEN_RE = re.compile(r"\\([A-Za-z]+\*?|.)", re.DOTALL)
_LATEX_ENV_RE = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")
_HTML_TAG_RE = re.compile(
    r"<\s*/?\s*(script|iframe|object|embed|style|img|svg|link|meta|base|form|input|a|body|html|math)\b",
    re.IGNORECASE,
)
_SCRIPT_URL_RE = re.compile(r"(javascript|vbscript)\s*:", re.IGNORECASE)
_EVENT_HANDLER_RE = re.compile(r"\bon[a-z]{3,}\s*=", re.IGNORECASE)

# --- Numeric comparison limits ---------------------------------------------------------------------------------
MAX_ANSWER_LENGTH = 200
MAX_NUMERIC_LENGTH = 64
MAX_DECIMAL_EXPONENT = 50
MAX_TOLERANCE = 0.01
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d{1,3})?")
_FRACTION_RE = re.compile(r"([+-]?\d{1,30})\s*/\s*(\d{1,30})")
# "1,000" can be one thousand or the Vietnamese decimal 1.000: ambiguous, so it is never auto-matched.
_AMBIGUOUS_COMMA_RE = re.compile(r"[+-]?[1-9]\d{0,2},\d{3}")
_NON_FINITE_RE = re.compile(r"(?<![A-Za-z])[+-]?(nan|inf|infinity)(?![A-Za-z])", re.IGNORECASE)


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
    """Raised when a SymPy verification snippet violates the AST allowlist pre-filter."""


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
    """Verify balanced KaTeX braces/$ delimiters and reject every LaTeX/HTML construct outside the allowlist."""
    if not isinstance(text, str):
        return ["forbidden input: KaTeX fragment must be a string"]
    if len(text) > MAX_KATEX_LENGTH:
        return [f"KaTeX fragment too long ({len(text)} > {MAX_KATEX_LENGTH} characters)"]

    errors: list[str] = []
    for match in _LATEX_TOKEN_RE.finditer(text):
        # finditer consumes "\\" as one token, so an escaped backslash cannot hide the following command.
        token = match.group(1)
        if token[0].isalpha():
            name = token
            if name not in ALLOWED_LATEX_COMMANDS:
                errors.append(f"forbidden LaTeX command: \\{name}")
        elif token not in _ALLOWED_LATEX_SYMBOLS:
            errors.append(f"forbidden LaTeX control symbol: \\{token}")
    for match in _LATEX_ENV_RE.finditer(text):
        if match.group(2).strip() not in ALLOWED_LATEX_ENVIRONMENTS:
            errors.append(f"forbidden LaTeX environment: {match.group(2).strip()!r}")
    if _HTML_TAG_RE.search(text):
        errors.append("forbidden HTML tag in KaTeX fragment")
    if _SCRIPT_URL_RE.search(text):
        errors.append("forbidden script URL scheme in KaTeX fragment")
    if _EVENT_HANDLER_RE.search(text):
        errors.append("forbidden HTML event handler in KaTeX fragment")
    errors = list(dict.fromkeys(errors))

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
            if brace_depth > MAX_KATEX_BRACE_DEPTH:
                errors.append("KaTeX braces nested too deep")
                break
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

    if 0 < brace_depth <= MAX_KATEX_BRACE_DEPTH:
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
        if not isinstance(step.body, str) or not step.body.strip():
            errors.append(f"step {step.kind} has empty body")
        katex_errors.extend(validate_katex_fragment(step.body))

    if not isinstance(draft.final_answer_text, str) or not draft.final_answer_text.strip():
        errors.append("final_answer_text must be non-empty")
    katex_errors.extend(validate_katex_fragment(draft.final_answer_text))

    all_errors = tuple(dict.fromkeys(errors + katex_errors))
    return SolutionValidationReport(
        valid_structure=not errors,
        valid_katex=not katex_errors,
        errors=all_errors,
    )


def _exponent_value(node: ast.expr) -> float | None:
    """Value of a literal exponent (optionally signed), else None."""
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool) and isinstance(node.value, (int, float)):
        return sign * node.value
    return None


def validate_sympy_snippet(code: str) -> None:
    """Allowlist pre-filter for one numeric SymPy expression; it never evaluates anything.

    Accepts only: a single expression made of numbers, names from ``ALLOWED_SYMPY_NAMES``, calls of such names with
    positional arguments, ``+ - * / **`` and unary ``+ -``. Everything else raises ``UnsafeSandboxSnippetError``.
    A snippet that passes still has to go through the isolated worker (guardrail H-02).
    """
    if not isinstance(code, str):
        raise UnsafeSandboxSnippetError("SymPy snippet must be a string")
    if not code.strip():
        raise UnsafeSandboxSnippetError("SymPy snippet cannot be empty")
    if len(code) > MAX_SNIPPET_LENGTH:
        raise UnsafeSandboxSnippetError(f"SymPy snippet too long (max {MAX_SNIPPET_LENGTH} characters)")
    if not _SNIPPET_CHARS_RE.fullmatch(code) or not code.isascii():
        raise UnsafeSandboxSnippetError("SymPy snippet contains characters outside the allowed set")

    # The worker reads '^' as exponentiation; Python would read it as xor, so parse the worker's meaning.
    try:
        tree = ast.parse(code.replace("^", "**").strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise UnsafeSandboxSnippetError(f"invalid expression syntax: {type(exc).__name__}") from exc

    nodes = 0
    pow_nodes = 0
    for node in ast.walk(tree):
        nodes += 1
        if nodes > MAX_AST_NODES:
            raise UnsafeSandboxSnippetError(f"expression has too many AST nodes (max {MAX_AST_NODES})")
        if isinstance(node, (ast.Expression, ast.Load)):
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise UnsafeSandboxSnippetError(f"operator {type(node.op).__name__} is not allowed")
            if isinstance(node.op, ast.Pow):
                pow_nodes += 1
                if pow_nodes > MAX_POW_NODES:
                    raise UnsafeSandboxSnippetError(f"too many power operators (max {MAX_POW_NODES})")
                exponent = _exponent_value(node.right)
                if exponent is None or not math.isfinite(exponent) or abs(exponent) > MAX_EXPONENT:
                    raise UnsafeSandboxSnippetError(
                        f"exponent must be a literal number with absolute value <= {MAX_EXPONENT}"
                    )
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise UnsafeSandboxSnippetError(f"operator {type(node.op).__name__} is not allowed")
        elif isinstance(node, (ast.operator, ast.unaryop)):
            continue  # operator singletons; their parents were checked above
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords or len(node.args) > MAX_CALL_ARGS:
                raise UnsafeSandboxSnippetError("only plain positional calls of allowed names are permitted")
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise UnsafeSandboxSnippetError("starred arguments are not allowed")
        elif isinstance(node, ast.Name):
            if not isinstance(node.ctx, ast.Load) or node.id not in ALLOWED_SYMPY_NAMES:
                raise UnsafeSandboxSnippetError(f"name {node.id!r} is not in the allowed SymPy names")
        elif isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise UnsafeSandboxSnippetError("only integer and decimal literals are allowed")
            if isinstance(value, int) and len(str(abs(value))) > MAX_INT_LITERAL_DIGITS:
                raise UnsafeSandboxSnippetError(f"integer literal longer than {MAX_INT_LITERAL_DIGITS} digits")
            if isinstance(value, float) and not math.isfinite(value):
                raise UnsafeSandboxSnippetError("non-finite literal")
        else:
            raise UnsafeSandboxSnippetError(f"syntax node {type(node).__name__} is not allowed")


def _parse_numeric_value(raw: str) -> Fraction | None:
    """Exact value of a plain number, decimal (comma or dot), a/b fraction or a percentage; else None."""
    cleaned = raw.strip().strip("$").strip()
    if not cleaned or len(cleaned) > MAX_NUMERIC_LENGTH:
        return None
    is_percent = cleaned.endswith("%")
    if is_percent:
        cleaned = cleaned[:-1].strip()
    value: Fraction | None = None
    fraction = _FRACTION_RE.fullmatch(cleaned)
    if fraction:
        denominator = int(fraction.group(2))
        if denominator == 0:
            return None
        value = Fraction(int(fraction.group(1)), denominator)
    elif _NUMBER_RE.fullmatch(cleaned) and not _AMBIGUOUS_COMMA_RE.fullmatch(cleaned):
        mantissa, _, exponent = cleaned.lower().partition("e")
        if exponent and abs(int(exponent)) > MAX_DECIMAL_EXPONENT:
            return None
        value = Fraction(mantissa.replace(",", ".")) * Fraction(10) ** int(exponent or 0)
    if value is None:
        return None
    return value / 100 if is_percent else value


def _unverifiable(sol: str, sym: str, reason: str) -> CrossCheckResult:
    return CrossCheckResult(
        verdict=CrossCheckVerdict.UNVERIFIABLE,
        candidate_value=sol,
        sympy_value=sym,
        draft_save_allowed=False,
        reason=reason,
    )


def cross_check_solution_answer(
    *,
    solution_answer: str,
    sympy_result: str,
    tolerance: float = 1e-4,
) -> CrossCheckResult:
    """Compare solution draft answer against independent SymPy result.

    Numbers are compared exactly (``Fraction``) with a purely RELATIVE tolerance, so distinct small numbers never
    collapse into each other. Symbolic answers must be identical (whitespace-insensitive, case-sensitive).
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError("tolerance must be a number")
    if not math.isfinite(tolerance) or tolerance < 0 or tolerance > MAX_TOLERANCE:
        raise ValueError(f"tolerance must be finite and within 0..{MAX_TOLERANCE}")

    if not isinstance(solution_answer, str) or not isinstance(sympy_result, str):
        return _unverifiable("", "", "solution and SymPy values must be strings")
    sol_norm = solution_answer.strip().strip("$").strip()
    sym_norm = sympy_result.strip().strip("$").strip()
    if not sol_norm or not sym_norm:
        return _unverifiable(sol_norm, sym_norm, "missing solution or SymPy value")
    if len(sol_norm) > MAX_ANSWER_LENGTH or len(sym_norm) > MAX_ANSWER_LENGTH:
        return _unverifiable(sol_norm[:MAX_ANSWER_LENGTH], sym_norm[:MAX_ANSWER_LENGTH], "answer too long to verify")
    if _NON_FINITE_RE.search(sol_norm) or _NON_FINITE_RE.search(sym_norm):
        return _unverifiable(sol_norm, sym_norm, "non-finite value cannot be verified")

    num_sol = _parse_numeric_value(sol_norm)
    num_sym = _parse_numeric_value(sym_norm)
    if num_sol is not None and num_sym is not None:
        allowed_gap = Fraction(tolerance) * max(abs(num_sol), abs(num_sym))
        if abs(num_sol - num_sym) <= allowed_gap:
            return CrossCheckResult(
                verdict=CrossCheckVerdict.MATCHED,
                candidate_value=sol_norm,
                sympy_value=sym_norm,
                draft_save_allowed=True,
                reason="numeric/fractional equivalence within relative tolerance",
            )
        return CrossCheckResult(
            verdict=CrossCheckVerdict.MISMATCH,
            candidate_value=sol_norm,
            sympy_value=sym_norm,
            draft_save_allowed=False,
            reason="numeric mismatch",
        )

    if _AMBIGUOUS_COMMA_RE.fullmatch(sol_norm) or _AMBIGUOUS_COMMA_RE.fullmatch(sym_norm):
        return _unverifiable(sol_norm, sym_norm, "ambiguous thousands/decimal comma")

    if re.sub(r"\s+", "", sol_norm) == re.sub(r"\s+", "", sym_norm):
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
