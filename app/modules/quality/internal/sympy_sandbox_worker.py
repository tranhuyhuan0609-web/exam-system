"""Isolated SymPy evaluator (guardrail H-02, spec section 6.3).

This file is a STANDALONE script, started only as::

    python -I sympy_sandbox_worker.py

It reads one JSON request ``{"expr": "<expression>"}`` on stdin and writes one JSON
result on stdout: ``{"ok": true, "value": "<decimal>"}`` or ``{"ok": false, "error": "<CODE>"}``
(plus an optional ``"detail"``).

Rules:
- It imports nothing from ``app``: under ``-I`` neither the script directory nor the cwd
  is on ``sys.path``.
- SymPy is imported lazily, only after the input has passed the whitelist and the
  ``RLIMIT_AS``/``RLIMIT_CPU`` limits are in place. Importing this file (e.g. from tests
  to exercise ``validate_expression``) therefore never loads SymPy.
- Parsing goes through ``parse_expr`` with a minimal global namespace whose
  ``__builtins__`` is empty. There is never a fallback to SymPy's generic string
  conversion: any failure is reported as an error result.
- Without the POSIX ``resource`` module (or when not started with ``-I``) the worker
  refuses to evaluate anything and reports ``SANDBOX_WORKER_UNAVAILABLE``.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

MAX_EXPR_LEN = 500
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
CPU_LIMIT_SECONDS = 5
SIGNIFICANT_DIGITS = 15

ALLOWED_SYMPY_NAMES = frozenset(
    {
        "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "ln", "exp",
        "Abs", "pi", "E", "Rational", "x", "y", "z", "t", "n", "a", "b", "c", "k",
    }
)  # fmt: skip
FORBIDDEN_TOKENS = ("__", ";", "lambda")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
# Digits, letters, arithmetic operators, parentheses, comma (function arguments),
# dot (decimals) and whitespace. Quotes, brackets, braces, backslashes, '=', '@', '!',
# ':' and the like are never needed by a numeric expression.
ALLOWED_CHARS_RE = re.compile(r"[0-9A-Za-z_+\-*/^().,\s]*")

# Error codes (mirrored by sympy_runner.py, which must not import this file).
SANDBOX_WORKER_UNAVAILABLE = "SANDBOX_WORKER_UNAVAILABLE"
EXPR_LENGTH_EXCEEDED_OR_EMPTY = "EXPR_LENGTH_EXCEEDED_OR_EMPTY"
FORBIDDEN_SYNTAX_TOKEN = "FORBIDDEN_SYNTAX_TOKEN"
DISALLOWED_CHARACTER = "DISALLOWED_CHARACTER"
DISALLOWED_IDENTIFIER = "DISALLOWED_IDENTIFIER"
EXPRESSION_NOT_NUMERIC = "EXPRESSION_NOT_NUMERIC"
EXPRESSION_NOT_REAL = "EXPRESSION_NOT_REAL"
SANDBOX_RESOURCE_LIMIT = "SANDBOX_RESOURCE_LIMIT"
SANDBOX_PARSE_ERROR = "SANDBOX_PARSE_ERROR"
SANDBOX_BAD_REQUEST = "SANDBOX_BAD_REQUEST"


def _error(code: str, detail: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error": code}
    if detail:
        result["detail"] = detail
    return result


def validate_expression(expr_raw: object) -> tuple[bool, dict[str, Any] | None, str]:
    """Whitelist check that needs no SymPy. Returns ``(ok, error_result, normalized_expr)``."""
    if not isinstance(expr_raw, str):
        return False, _error(SANDBOX_BAD_REQUEST, "expr must be a string"), ""
    expr = expr_raw.strip().replace("^", "**")
    if not expr or len(expr) > MAX_EXPR_LEN:
        return False, _error(EXPR_LENGTH_EXCEEDED_OR_EMPTY), expr
    if any(token in expr for token in FORBIDDEN_TOKENS):
        return False, _error(FORBIDDEN_SYNTAX_TOKEN), expr
    if not ALLOWED_CHARS_RE.fullmatch(expr):
        bad = sorted({ch for ch in expr if not ALLOWED_CHARS_RE.fullmatch(ch)})
        return False, _error(DISALLOWED_CHARACTER, "".join(bad)[:20]), expr
    disallowed = set(IDENTIFIER_RE.findall(expr)) - ALLOWED_SYMPY_NAMES
    if disallowed:
        return False, _error(DISALLOWED_IDENTIFIER, ",".join(sorted(disallowed))[:200]), expr
    return True, None, expr


def apply_resource_limits() -> bool:
    """Cap address space and CPU time of this process. False when not possible (non-POSIX)."""
    try:
        import resource
    except ImportError:
        return False
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS))
    except (ValueError, OSError):
        return False
    return True


def _evaluate(expr: str) -> dict[str, Any]:
    """Parse and evaluate an already validated expression. Call only after the rlimits are set."""
    import sympy as sp
    from sympy.parsing.sympy_parser import parse_expr, standard_transformations

    # Minimal namespace: the whitelisted functions/constants plus the constructors that
    # the standard transformations emit (auto_number, auto_symbol, repeated_decimals,
    # factorial_notation). No builtins at all.
    global_dict: dict[str, Any] = {
        "__builtins__": {},
        "Integer": sp.Integer,
        "Float": sp.Float,
        "Rational": sp.Rational,
        "Symbol": sp.Symbol,
        "Function": sp.Function,
        "factorial": sp.factorial,
        "sqrt": sp.sqrt,
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "asin": sp.asin,
        "acos": sp.acos,
        "atan": sp.atan,
        "log": sp.log,
        "exp": sp.exp,
        "Abs": sp.Abs,
        "pi": sp.pi,
        "E": sp.E,
    }
    local_dict: dict[str, Any] = {"ln": sp.log}
    try:
        result = parse_expr(
            expr, local_dict=local_dict, global_dict=global_dict, transformations=standard_transformations
        )
        if not isinstance(result, sp.Expr):
            return _error(EXPRESSION_NOT_NUMERIC, type(result).__name__)
        if result.free_symbols:
            return _error(EXPRESSION_NOT_NUMERIC)
        value = sp.N(result, SIGNIFICANT_DIGITS)
        if not (value.is_number and value.is_real and value.is_finite):
            return _error(EXPRESSION_NOT_REAL)
        return {"ok": True, "value": str(value)}
    except (MemoryError, RecursionError) as exc:
        return _error(SANDBOX_RESOURCE_LIMIT, type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - fail closed on anything the parser raises
        # H-02: never retry with a more permissive parser.
        return _error(SANDBOX_PARSE_ERROR, type(exc).__name__)


def handle_request(raw: str) -> dict[str, Any]:
    try:
        request = json.loads(raw)
    except (ValueError, TypeError):
        return _error(SANDBOX_BAD_REQUEST, "invalid JSON")
    if not isinstance(request, dict):
        return _error(SANDBOX_BAD_REQUEST, "request must be an object")
    ok, error, expr = validate_expression(request.get("expr"))
    if not ok:
        return error  # type: ignore[return-value]
    if not sys.flags.isolated:
        return _error(SANDBOX_WORKER_UNAVAILABLE, "worker must run under python -I")
    if not apply_resource_limits():
        return _error(SANDBOX_WORKER_UNAVAILABLE, "resource limits unavailable")
    return _evaluate(expr)


def main() -> int:
    raw = sys.stdin.read(MAX_EXPR_LEN * 8 + 1024)
    try:
        result = handle_request(raw)
    except MemoryError:
        result = _error(SANDBOX_RESOURCE_LIMIT, "MemoryError")
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
