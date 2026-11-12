"""Guardrail H-02: SymPy runs only in an isolated, resource-limited, fail-closed worker."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.quality import public as quality
from app.modules.quality.internal import sympy_runner as runner

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
WORKER_FILE = ROOT / "app" / "modules" / "quality" / "internal" / "sympy_sandbox_worker.py"

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="real evaluation needs POSIX rlimits (Linux)")


def _load_worker():
    spec = importlib.util.spec_from_file_location("_sympy_sandbox_worker_under_test", WORKER_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = _load_worker()

# (payload, expected error code from the worker's whitelist)
RCE_PAYLOADS = [
    ("__import__('os').system('id')", "FORBIDDEN_SYNTAX_TOKEN"),
    ("open('/etc/passwd').read()", "DISALLOWED_CHARACTER"),
    ("open(x)", "DISALLOWED_IDENTIFIER"),
    ("exec(x)", "DISALLOWED_IDENTIFIER"),
    ("eval(x)", "DISALLOWED_IDENTIFIER"),
    ("getattr(pi, x)", "DISALLOWED_IDENTIFIER"),
    ("().__class__.__subclasses__()", "FORBIDDEN_SYNTAX_TOKEN"),
    ("(lambda: 1)()", "FORBIDDEN_SYNTAX_TOKEN"),
    ("x = 1; y", "FORBIDDEN_SYNTAX_TOKEN"),
    ("pi.__class__", "FORBIDDEN_SYNTAX_TOKEN"),
    ("pi.func", "DISALLOWED_IDENTIFIER"),
    ("1+" * 300 + "1", "EXPR_LENGTH_EXCEEDED_OR_EMPTY"),
    ("   ", "EXPR_LENGTH_EXCEEDED_OR_EMPTY"),
    ("sympify(x)", "DISALLOWED_IDENTIFIER"),
    ("Symbol(x)", "DISALLOWED_IDENTIFIER"),
    ("compile(x, x, x)", "DISALLOWED_IDENTIFIER"),
    ("globals()", "DISALLOWED_IDENTIFIER"),
    ("vars(pi)", "DISALLOWED_IDENTIFIER"),
    ("[c for c in x]", "DISALLOWED_CHARACTER"),
    ("{x: 1}", "DISALLOWED_CHARACTER"),
    ("x if 1 else y", "DISALLOWED_IDENTIFIER"),
    ("pi\\\n.evalf()", "DISALLOWED_CHARACTER"),
    ("sin(1)\n__import__", "FORBIDDEN_SYNTAX_TOKEN"),
    ("_", "DISALLOWED_IDENTIFIER"),
]


# --- whitelist (runs on every OS, never loads SymPy) -------------------------------------


@pytest.mark.parametrize(("payload", "expected"), RCE_PAYLOADS)
def test_worker_whitelist_rejects_escape_payloads(payload, expected):
    ok, error, _ = worker.validate_expression(payload)
    assert not ok
    assert error["error"] == expected


def test_worker_whitelist_rejects_non_string():
    ok, error, _ = worker.validate_expression({"expr": 1})
    assert not ok
    assert error["error"] == "SANDBOX_BAD_REQUEST"


@pytest.mark.parametrize("expr", ["sqrt(2)*sqrt(2)", "sin(pi/2)", "2^10", "ln(E)", "Rational(1, 3) + 0.5", "x + 1"])
def test_worker_whitelist_accepts_math(expr):
    ok, error, normalized = worker.validate_expression(expr)
    assert ok, error
    assert "^" not in normalized


def test_worker_and_runner_agree_on_limits_and_codes():
    assert worker.MAX_EXPR_LEN == runner.MAX_EXPR_LEN == 500
    assert worker.MEMORY_LIMIT_BYTES == 256 * 1024 * 1024
    assert worker.CPU_LIMIT_SECONDS == 5
    for name in (
        "SANDBOX_WORKER_UNAVAILABLE",
        "EXPR_LENGTH_EXCEEDED_OR_EMPTY",
        "FORBIDDEN_SYNTAX_TOKEN",
        "DISALLOWED_CHARACTER",
        "DISALLOWED_IDENTIFIER",
        "EXPRESSION_NOT_NUMERIC",
        "EXPRESSION_NOT_REAL",
        "SANDBOX_RESOURCE_LIMIT",
        "SANDBOX_PARSE_ERROR",
        "SANDBOX_BAD_REQUEST",
    ):
        assert getattr(worker, name) == getattr(runner, name) == name


def test_worker_refuses_without_resource_limits(monkeypatch):
    """No rlimits (non-POSIX) -> refuse; SymPy evaluation is never reached."""
    monkeypatch.setattr(worker, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=1)))
    monkeypatch.setattr(worker, "apply_resource_limits", lambda: False)
    monkeypatch.setattr(worker, "_evaluate", lambda expr: pytest.fail("evaluated without rlimits"))
    result = worker.handle_request(json.dumps({"expr": "1+1"}))
    assert result["error"] == "SANDBOX_WORKER_UNAVAILABLE"


def test_worker_refuses_when_not_isolated(monkeypatch):
    monkeypatch.setattr(worker, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=0)))
    monkeypatch.setattr(worker, "_evaluate", lambda expr: pytest.fail("evaluated without -I"))
    assert worker.handle_request(json.dumps({"expr": "1+1"}))["error"] == "SANDBOX_WORKER_UNAVAILABLE"


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", json.dumps({"expr": None})])
def test_worker_rejects_bad_requests(raw):
    assert worker.handle_request(raw)["error"] == "SANDBOX_BAD_REQUEST"


@pytest.mark.skipif(sys.platform == "linux", reason="checks the non-POSIX refusal of the real worker")
def test_real_worker_process_refuses_on_non_posix():
    proc = subprocess.run(
        [sys.executable, "-I", str(WORKER_FILE)],
        input=json.dumps({"expr": "1+1"}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["error"] == "SANDBOX_WORKER_UNAVAILABLE"


def test_importing_worker_and_runner_does_not_load_sympy():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('w', {str(WORKER_FILE)!r})\n"
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
        "import app.modules.quality.public\n"
        "assert 'sympy' not in sys.modules, 'sympy was imported'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


# --- runner fail-closed behaviour (runs on every OS) ---------------------------------------


def test_spawned_command_is_python_isolated_worker():
    assert runner.build_worker_command(WORKER_FILE) == [sys.executable, "-I", str(WORKER_FILE)]
    assert runner.WORKER_PATH == WORKER_FILE.resolve()


def _fake_run(monkeypatch, *, stdout="", returncode=0, raises=None):
    calls = []

    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(runner.subprocess, "run", fake)
    return calls


def test_runner_spawns_exactly_python_dash_i_worker(monkeypatch):
    calls = _fake_run(monkeypatch, stdout=json.dumps({"ok": True, "value": "2.00000000000000"}))
    result = quality.evaluate_expression("1+1")
    assert result == quality.SandboxResult(ok=True, value="2.00000000000000")
    argv, kwargs = calls[0]
    assert argv == [sys.executable, "-I", str(WORKER_FILE.resolve())]
    assert kwargs["shell"] is False
    assert set(kwargs["env"]) <= {"PATH", "LANG", "LC_ALL"}
    assert kwargs["timeout"] > 0
    assert json.loads(kwargs["input"]) == {"expr": "1+1"}


def test_missing_worker_file_is_unavailable(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch)
    result = quality.evaluate_expression("1+1", worker_path=tmp_path / "missing_worker.py")
    assert result.error == quality.SANDBOX_WORKER_UNAVAILABLE
    assert calls == []


def test_missing_default_worker_file_is_unavailable(monkeypatch, tmp_path):
    calls = _fake_run(monkeypatch)
    monkeypatch.setattr(runner, "WORKER_PATH", tmp_path / "sympy_sandbox_worker.py")
    assert quality.evaluate_expression("1+1").error == quality.SANDBOX_WORKER_UNAVAILABLE
    assert calls == []


@pytest.mark.parametrize("platform", ["win32", "darwin", "cygwin"])
def test_non_linux_platform_is_unavailable_without_spawning(monkeypatch, platform):
    calls = _fake_run(monkeypatch)
    monkeypatch.setattr(sys, "platform", platform)
    result = quality.evaluate_expression("1+1")
    assert result.error == quality.SANDBOX_WORKER_UNAVAILABLE
    assert calls == []


def test_runner_rejects_over_long_input_before_spawning(monkeypatch):
    calls = _fake_run(monkeypatch)
    assert quality.evaluate_expression("1" * 501).error == quality.EXPR_LENGTH_EXCEEDED_OR_EMPTY
    assert calls == []


def test_timeout_is_an_error_result(monkeypatch):
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired(cmd="worker", timeout=1))
    assert quality.evaluate_expression("1+1").error == quality.SANDBOX_TIMEOUT


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (-24, "", "SANDBOX_RESOURCE_LIMIT"),  # SIGXCPU from RLIMIT_CPU
        (-9, "", "SANDBOX_RESOURCE_LIMIT"),
        (1, json.dumps({"ok": True, "value": "1"}), "SANDBOX_WORKER_CRASHED"),
        (0, "", "SANDBOX_BAD_OUTPUT"),
        (0, "Traceback ...", "SANDBOX_BAD_OUTPUT"),
        (0, json.dumps([1]), "SANDBOX_BAD_OUTPUT"),
        (0, json.dumps({"ok": True}), "SANDBOX_BAD_OUTPUT"),
        (0, json.dumps({"ok": False}), "SANDBOX_BAD_OUTPUT"),
        (0, json.dumps({"ok": False, "error": "EXPRESSION_NOT_NUMERIC"}), "EXPRESSION_NOT_NUMERIC"),
    ],
)
def test_worker_failures_map_to_error_results(monkeypatch, returncode, stdout, expected):
    calls = _fake_run(monkeypatch, stdout=stdout, returncode=returncode)
    result = quality.evaluate_expression("1+1")
    assert not result.ok
    assert result.error == expected
    assert len(calls) == 1  # never retried, never evaluated in-process


def test_spawn_failure_is_unavailable(monkeypatch):
    _fake_run(monkeypatch, raises=FileNotFoundError("python"))
    assert quality.evaluate_expression("1+1").error == quality.SANDBOX_WORKER_UNAVAILABLE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0,5", "0.5"), ("3/4", "0.75"), (" 12 cm", "12"), ("45°", "45"), ("abc", None), ("1/0", None), (None, None)],
)
def test_canonicalize_numeric_answer(raw, expected):
    value = quality.canonicalize_numeric_answer(raw)
    assert (str(value) if value is not None else None) == expected


def test_verify_numeric_answer_uses_the_sandbox(monkeypatch):
    _fake_run(monkeypatch, stdout=json.dumps({"ok": True, "value": "0.333333333333333"}))
    check = quality.verify_numeric_answer("Rational(1, 3)", "0,33")
    assert check.matches and check.error is None
    _fake_run(monkeypatch, stdout=json.dumps({"ok": False, "error": "SANDBOX_PARSE_ERROR"}))
    assert quality.verify_numeric_answer("1+", "1").error == "SANDBOX_PARSE_ERROR"


# --- static guard over app/ ------------------------------------------------------------------

SYMPY_IMPORT_RE = re.compile(r"^\s*(import\s+sympy\b|from\s+sympy\b)", re.MULTILINE)


def test_no_sympify_and_no_sympy_imports_outside_the_worker():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if "sympify(" in text:
            offenders.append(f"{rel}: sympify(")
        if path.resolve() != WORKER_FILE.resolve() and SYMPY_IMPORT_RE.search(text):
            offenders.append(f"{rel}: imports sympy")
        if path.resolve() != WORKER_FILE.resolve() and re.search(r"""import_module\(\s*["']sympy""", text):
            offenders.append(f"{rel}: imports sympy dynamically")
    assert not offenders, offenders


# --- real isolated evaluation (Linux only) ----------------------------------------------------


@linux_only
@pytest.mark.parametrize(
    ("expr", "expected"),
    [("sqrt(2)*sqrt(2)", 2), ("sin(pi/2)", 1), ("2^10", 1024), ("ln(E)", 1), ("Rational(1, 4) + 0.25", 0.5)],
)
def test_real_evaluation(expr, expected):
    result = quality.evaluate_expression(expr)
    assert result.ok, result
    assert abs(float(result.value) - expected) < 1e-9


@linux_only
@pytest.mark.parametrize("expr", ["x + 1", "sin(a)*b", "k^2"])
def test_real_free_symbols_are_not_numeric(expr):
    assert quality.evaluate_expression(expr).error == quality.EXPRESSION_NOT_NUMERIC


@linux_only
@pytest.mark.parametrize("expr", ["sqrt(-1)", "1/0", "log(0)"])
def test_real_non_real_results_are_rejected(expr):
    assert quality.evaluate_expression(expr).error == quality.EXPRESSION_NOT_REAL


@linux_only
@pytest.mark.parametrize(("payload", "expected"), RCE_PAYLOADS)
def test_real_worker_rejects_escape_payloads(payload, expected):
    result = quality.evaluate_expression(payload)
    assert not result.ok
    assert result.error == expected


@linux_only
@pytest.mark.parametrize("expr", ["10**10**10", "9^9^9^9", "2**(10**12)"])
def test_real_blow_ups_end_in_an_error_not_a_hang(expr):
    result = quality.evaluate_expression(expr, timeout_seconds=20)
    assert not result.ok
    assert result.error in {
        "SANDBOX_RESOURCE_LIMIT",
        "SANDBOX_TIMEOUT",
        "SANDBOX_PARSE_ERROR",
        "SANDBOX_WORKER_CRASHED",
    }
