"""API-side launcher for the isolated SymPy worker (guardrail H-02, spec section 6.3).

This module runs inside the API/worker process and must never import SymPy. Every
expression is evaluated in a fresh child process, ``python -I sympy_sandbox_worker.py``,
with a wall-clock timeout, a minimal environment and no shell.

Fail-closed rules: a missing worker file, a non-Linux platform (no ``resource`` limits),
a timeout, a non-zero exit, a kill by signal (rlimit) or unreadable output all produce an
error result. There is no in-process evaluation path of any kind.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

WORKER_PATH = Path(__file__).resolve().with_name("sympy_sandbox_worker.py")
DEFAULT_TIMEOUT_SECONDS = 10.0
# Mirrors sympy_sandbox_worker.MAX_EXPR_LEN; the worker is standalone and cannot be shared.
MAX_EXPR_LEN = 500
MAX_WORKER_OUTPUT_CHARS = 4096
WORKER_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}

# Error codes produced by the runner itself.
SANDBOX_WORKER_UNAVAILABLE = "SANDBOX_WORKER_UNAVAILABLE"
SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
SANDBOX_WORKER_CRASHED = "SANDBOX_WORKER_CRASHED"
SANDBOX_BAD_OUTPUT = "SANDBOX_BAD_OUTPUT"
EXPR_LENGTH_EXCEEDED_OR_EMPTY = "EXPR_LENGTH_EXCEEDED_OR_EMPTY"
# Error codes produced by the worker and passed through unchanged.
FORBIDDEN_SYNTAX_TOKEN = "FORBIDDEN_SYNTAX_TOKEN"
DISALLOWED_CHARACTER = "DISALLOWED_CHARACTER"
DISALLOWED_IDENTIFIER = "DISALLOWED_IDENTIFIER"
EXPRESSION_NOT_NUMERIC = "EXPRESSION_NOT_NUMERIC"
EXPRESSION_NOT_REAL = "EXPRESSION_NOT_REAL"
SANDBOX_RESOURCE_LIMIT = "SANDBOX_RESOURCE_LIMIT"
SANDBOX_PARSE_ERROR = "SANDBOX_PARSE_ERROR"
SANDBOX_BAD_REQUEST = "SANDBOX_BAD_REQUEST"
# Error codes of the answer verifier.
UNPARSEABLE_SOURCE_ANSWER = "UNPARSEABLE_SOURCE_ANSWER"


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    value: str | None = None
    error: str | None = None
    detail: str | None = None

    @classmethod
    def failure(cls, error: str, detail: str | None = None) -> SandboxResult:
        return cls(ok=False, error=error, detail=detail)


@dataclass(frozen=True)
class AnswerCheck:
    matches: bool
    computed: str | None = None
    source: str | None = None
    abs_diff: str | None = None
    error: str | None = None


def build_worker_command(worker_path: Path) -> list[str]:
    """The exact argv used to start the worker: ``python -I <worker>``."""
    return [sys.executable, "-I", str(worker_path)]


def _parse_worker_output(stdout: str) -> SandboxResult:
    if not stdout or len(stdout) > MAX_WORKER_OUTPUT_CHARS:
        return SandboxResult.failure(SANDBOX_BAD_OUTPUT, "empty or oversized output")
    try:
        data = json.loads(stdout)
    except ValueError:
        return SandboxResult.failure(SANDBOX_BAD_OUTPUT, "invalid JSON")
    if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
        return SandboxResult.failure(SANDBOX_BAD_OUTPUT, "unexpected shape")
    if data["ok"]:
        value = data.get("value")
        if not isinstance(value, str) or not value:
            return SandboxResult.failure(SANDBOX_BAD_OUTPUT, "missing value")
        return SandboxResult(ok=True, value=value)
    error = data.get("error")
    detail = data.get("detail")
    if not isinstance(error, str) or not error:
        return SandboxResult.failure(SANDBOX_BAD_OUTPUT, "missing error code")
    return SandboxResult.failure(error, detail if isinstance(detail, str) else None)


def evaluate_expression(
    expr: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    worker_path: Path | None = None,
) -> SandboxResult:
    """Evaluate a numeric expression in the isolated worker. Never evaluates in-process."""
    worker = worker_path or WORKER_PATH
    if not worker.is_file():
        return SandboxResult.failure(SANDBOX_WORKER_UNAVAILABLE, "worker file missing")
    if sys.platform != "linux":
        return SandboxResult.failure(SANDBOX_WORKER_UNAVAILABLE, f"unsupported platform {sys.platform}")
    if not isinstance(expr, str) or not expr.strip() or len(expr.strip()) > MAX_EXPR_LEN:
        return SandboxResult.failure(EXPR_LENGTH_EXCEEDED_OR_EMPTY)

    try:
        proc = subprocess.run(
            build_worker_command(worker),
            input=json.dumps({"expr": expr}),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=dict(WORKER_ENV),
            cwd="/",
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills the child before re-raising.
        return SandboxResult.failure(SANDBOX_TIMEOUT)
    except OSError as exc:
        return SandboxResult.failure(SANDBOX_WORKER_UNAVAILABLE, type(exc).__name__)

    if proc.returncode < 0:
        # Killed by a signal, typically SIGXCPU (RLIMIT_CPU) or SIGKILL.
        return SandboxResult.failure(SANDBOX_RESOURCE_LIMIT, f"signal {-proc.returncode}")
    if proc.returncode != 0:
        return SandboxResult.failure(SANDBOX_WORKER_CRASHED, f"exit {proc.returncode}")
    return _parse_worker_output(proc.stdout)


def canonicalize_numeric_answer(raw_value: str | None) -> Decimal | None:
    """Normalize a source answer such as ``"0,5"``, ``"3/4"`` or ``"12 cm"`` to a Decimal."""
    if raw_value is None:
        return None
    cleaned = raw_value.strip().replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[a-zA-Z%/°]+$", "", cleaned)
    try:
        if "/" in cleaned:
            numerator, denominator = cleaned.split("/", 1)
            return Decimal(numerator) / Decimal(denominator)
        return Decimal(cleaned)
    except (InvalidOperation, ZeroDivisionError):
        return None


def verify_numeric_answer(
    expr: str,
    source_answer: str,
    tolerance: Decimal = Decimal("0.01"),
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> AnswerCheck:
    """Compare a sandbox-evaluated expression with a source answer within ``tolerance``."""
    result = evaluate_expression(expr, timeout_seconds=timeout_seconds)
    if not result.ok or result.value is None:
        return AnswerCheck(matches=False, error=result.error or SANDBOX_BAD_OUTPUT)
    try:
        computed = Decimal(result.value)
    except InvalidOperation:
        return AnswerCheck(matches=False, error=SANDBOX_BAD_OUTPUT)
    source = canonicalize_numeric_answer(source_answer)
    if source is None:
        return AnswerCheck(matches=False, computed=str(computed), error=UNPARSEABLE_SOURCE_ANSWER)
    diff = abs(computed - source)
    return AnswerCheck(
        matches=diff <= tolerance,
        computed=str(round(computed, 4)),
        source=str(source),
        abs_diff=str(round(diff, 4)),
    )
