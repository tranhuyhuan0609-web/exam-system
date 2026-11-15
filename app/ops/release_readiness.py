"""Release readiness report: GATE-01..GATE-10 evidence, repository checks and the licence audit in one document.

``python -m app.ops.release_readiness --run-tests --run-checks --write docs/architecture/V11_3_RELEASE_READINESS.md``

Chapter 9 rule: a gate is never VERIFIED on synthetic evidence. This tool does not decide that by itself: it runs the
test files that back each gate (``--run-tests``), turns the outcome into a ``GateEvidenceRecord`` and lets
``app.ops.release_gates.evaluate_sprint_exit_readiness`` judge. Tests on a developer machine are TESTED at best,
because ``real_infra_executed`` stays False until a run on the real stack (Patroni, PgBouncer, the 5.000 CCU k6 run,
the external pentest, a DR drill) is attached. Nothing here fabricates such evidence.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.release_gates import (
    CANONICAL_RELEASE_GATES,
    GateEvidenceRecord,
    MaturityStatus,
    evaluate_evidence_record,
    evaluate_sprint_exit_readiness,
)
from app.ops.sbom_license_audit import SbomAuditReport, audit_cyclonedx_sbom, audit_requirements_lines

ROOT = Path(__file__).resolve().parents[2]
FINAL_SPRINT = 14


@dataclass(frozen=True)
class GatePlan:
    """The tests that back a gate and what is still missing for VERIFIED."""

    tests: tuple[str, ...]
    missing_for_verified: str


GATE_PLANS: dict[str, GatePlan] = {
    "GATE-01": GatePlan(
        (
            "tests/security/test_ddl_dry_run.py",
            "tests/security/test_alembic_matches_chapter4.py",
            "tests/security/test_chapter4_column_parity.py",
            "tests/test_rls_builders.py",
        ),
        "InitPlan and zero-IDOR measured on the production-like stack under 5.000 CCU",
    ),
    "GATE-02": GatePlan(
        ("tests/test_assembly_delivery_db.py", "tests/test_content_db.py"),
        "immutability triggers exercised against the production database cluster",
    ),
    "GATE-03": GatePlan(
        ("tests/test_assembly_delivery_db.py", "tests/test_attempts_autosave_db.py"),
        "answer-leak scan of the deployed API and web workspaces",
    ),
    "GATE-04": GatePlan(
        ("tests/test_attempts_autosave_db.py",),
        "Dexie offline store (frontend, outside this repository scope) and a chaos run with RPO = 0",
    ),
    "GATE-05": GatePlan((), "the 5.000 CCU per-VU k6 run on the target hardware (not run)"),
    "GATE-06a": GatePlan(
        ("tests/test_ops_golden_metrics.py",), "golden and held-out extraction run on real Toán and Vật lí papers"
    ),
    "GATE-06b": GatePlan((), "the 41 Hóa học and Sinh học papers of the golden corpus (not run)"),
    "GATE-07": GatePlan(
        ("tests/test_grading_e2e_db.py",), "regression on the full golden corpus against a production-like database"
    ),
    "GATE-08": GatePlan(
        (
            "tests/test_sympy_sandbox.py",
            "tests/test_fsrs_core.py",
            "tests/test_tutor_core.py",
            "tests/test_explanations_core.py",
        ),
        "SymPy verification rate of at least 95% on real explanations, FSRS vectors against py-fsrs",
    ),
    "GATE-09": GatePlan(
        ("tests/test_ops_sbom_license_audit.py", "tests/test_ops_pdp_compliance.py", "tests/test_privacy_db.py"),
        "DPIA/DPA signed, Vault Transit erasure on the real Vault, external pentest report",
    ),
    "GATE-10": GatePlan(
        ("tests/test_ops_pitr_tombstones.py",), "a Patroni strict-quorum failover and a post-PITR tombstone drill"
    ),
}

CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ruff check .", ("-m", "ruff", "check", ".")),
    ("lint-imports", ("@lint-imports",)),
    ("bash ci/check_gate_ids.sh", ("@bash", "ci/check_gate_ids.sh")),
    ("alembic check", ("-m", "alembic", "check")),
    ("mypy app/contracts app/platform app/ops app/modules", (
        "-m", "mypy", "--follow-imports=silent", "app/contracts", "app/platform", "app/ops", "app/modules",
    )),
)  # fmt: skip


@dataclass(frozen=True)
class CommandResult:
    label: str
    ok: bool
    summary: str


def _run(argv: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argument lists, no shell
        argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False
    )


def _resolve(args: tuple[str, ...]) -> list[str]:
    if args and args[0].startswith("@"):
        tool = args[0][1:]
        found = shutil.which(tool, path=str(Path(sys.executable).parent)) or shutil.which(tool)
        return [found or tool, *args[1:]]
    return [sys.executable, *args]


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def run_checks() -> list[CommandResult]:
    results = []
    for label, args in CHECKS:
        done = _run(_resolve(args))
        results.append(CommandResult(label, done.returncode == 0, _last_line(done.stdout + "\n" + done.stderr)))
    return results


def run_gate_tests(plan: GatePlan) -> CommandResult:
    if not plan.tests:
        return CommandResult("no tests", False, "no automated test backs this gate")
    existing = [t for t in plan.tests if (ROOT / t).exists()]
    if len(existing) != len(plan.tests):
        return CommandResult("missing tests", False, f"missing: {sorted(set(plan.tests) - set(existing))}")
    done = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *existing])
    return CommandResult(", ".join(existing), done.returncode == 0, _last_line(done.stdout))


def evidence_for(gate_id: str, tests: CommandResult | None) -> GateEvidenceRecord:
    """TESTED when its tests pass, IMPLEMENTED when they fail or were not run, SPECIFIED without any test."""
    maturity: MaturityStatus = "SPECIFIED"
    passed = False
    if tests is not None and tests.label != "no tests":
        maturity = "TESTED" if tests.ok else "IMPLEMENTED"
        passed = tests.ok
    return GateEvidenceRecord(
        gate_id=gate_id, maturity=maturity, passed=passed, real_infra_executed=False, benchmark=None
    )


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def requirements_closure(lines: list[str]) -> set[str]:
    """Normalised names of the requirements and of everything they pull in (the release dependency set)."""
    pending = []
    for raw in lines:
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", re.split(r"(?:^|\s)#", raw, maxsplit=1)[0])
        if match:
            pending.append(_norm(match.group(1)))
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for requirement in requires:
            if "extra ==" in requirement:  # optional extras are not installed with the release
                continue
            match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", requirement)
            if match:
                pending.append(_norm(match.group(1)))
    return seen


def installed_sbom(only: set[str] | None = None) -> dict[str, Any]:
    """A CycloneDX-shaped inventory of installed distributions (limited to `only`) with the licence each declares."""
    components = []
    for dist in importlib.metadata.distributions():
        meta = dist.metadata
        name = meta["Name"]
        if not name or (only is not None and _norm(name) not in only):
            continue
        licence = meta.get("License-Expression") or ""
        if not licence:
            text = (meta.get("License") or "").strip()
            licence = text if 0 < len(text) < 80 and "\n" not in text else ""
            if not licence and text.lower().startswith("apache license") and "version 2.0" in text[:200].lower():
                licence = "Apache-2.0"  # the whole licence text is pasted into the License field
        if not licence:
            classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
            licence = classifiers[-1].split("::")[-1].strip() if classifiers else ""
        licence = re.sub(r"\s*\([^)]*\)\s*$", "", licence)  # "Mozilla Public License 2.0 (MPL 2.0)": drop the alias
        entry: dict[str, Any] = {"name": name, "version": dist.version}
        if licence:
            entry["licenses"] = [{"license": {"name": licence}}]
        components.append(entry)
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}


def audit_licences() -> tuple[SbomAuditReport, SbomAuditReport]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return audit_requirements_lines(lines), audit_cyclonedx_sbom(installed_sbom(requirements_closure(lines)))


def _git(*args: str) -> str:
    try:
        return _run(["git", *args], timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(
    records: dict[str, GateEvidenceRecord],
    test_results: dict[str, CommandResult | None],
    checks: list[CommandResult] | None,
    licences: tuple[SbomAuditReport, SbomAuditReport],
) -> str:
    exit_report = evaluate_sprint_exit_readiness(target_sprint=FINAL_SPRINT, evidence_by_gate=records)
    out: list[str] = [
        "# ULP V11.3 - Release readiness (GATE-01..GATE-10)",
        "",
        f"Generated by `python -m app.ops.release_readiness` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, "
        f"commit `{_git('rev-parse', '--short', 'HEAD') or 'unknown'}`, branch `{_git('branch', '--show-current')}`.",
        "",
        "## Verdict",
        "",
        f"**{'READY' if exit_report.ready_for_release else 'NOT READY FOR RELEASE'}** - "
        f"{len(exit_report.blocking_reasons)} blocking reason(s) across {len(exit_report.required_gates)} gates.",
        "",
        "Chapter 9: no gate is VERIFIED on synthetic evidence. Tests run on a developer machine make a gate TESTED at "
        "best; `real_infra_executed` stays False until a run on the real stack is attached. A failing or missing "
        "test lowers the gate further. This report invents no evidence.",
        "",
        "## Gates",
        "",
        "| Gate | Name | Maturity | Tests | Result | Missing for VERIFIED |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for gate in CANONICAL_RELEASE_GATES:
        record = records[gate.gate_id]
        tests = test_results.get(gate.gate_id)
        result = "not run" if tests is None else f"{'PASS' if tests.ok else 'FAIL'}: {tests.summary}"
        files = "none" if tests is None or tests.label in {"no tests", "missing tests"} else tests.label
        out.append(
            f"| {gate.gate_id} | {_escape(gate.name)} | {record.maturity} | {_escape(files)} | {_escape(result)} | "
            f"{_escape(GATE_PLANS[gate.gate_id].missing_for_verified)} |"
        )
    out += ["", "### Blocking reasons (from `evaluate_sprint_exit_readiness`)", ""]
    out += [f"- `{reason}`" for reason in exit_report.blocking_reasons] or ["- none"]

    if checks is not None:
        out += ["", "## Repository checks", "", "| Check | Result | Last line |", "| --- | --- | --- |"]
        out += [f"| `{c.label}` | {'PASS' if c.ok else 'FAIL'} | {_escape(c.summary)} |" for c in checks]

    req, installed = licences
    out += [
        "",
        "## Licence audit (ADR-010: no AGPLv3 / SSPL without a commercial waiver)",
        "",
        f"- `requirements.txt`: {req.scanned_components} requirements scanned, "
        f"{len(req.blocking_violations)} blocking, {'PASS' if req.passed else 'FAIL'}.",
        f"- release dependency closure (CycloneDX from the installed metadata): "
        f"{installed.scanned_components} components, "
        f"{len(installed.blocking_violations)} blocking, {len(installed.warnings)} warning(s), "
        f"{'PASS' if installed.passed else 'FAIL'}.",
    ]
    for violation in installed.blocking_violations:
        out.append(
            f"  - BLOCKING `{violation.package_name}` {violation.version}: {violation.license_id} - {violation.reason}"
        )
    for violation in installed.warnings:
        out.append(f"  - WARNING `{violation.package_name}` {violation.version}: {violation.license_id}")
    out += [
        "",
        "## What is not covered here",
        "",
        "- Compliance logic (`pdp_compliance`, `pitr_tombstones`) is covered by the GATE-09 and GATE-10 test files "
        "above; a Patroni failover, a PITR restore drill, the k6 5.000 CCU run, the external pentest and the signed "
        "DPIA/DPA need the real environment and people, they cannot be produced by this tool.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate GATE-01..GATE-10 evidence into a readiness report.")
    parser.add_argument("--run-tests", action="store_true", help="run the test files that back each gate")
    parser.add_argument("--run-checks", action="store_true", help="run ruff, lint-imports, gate ids, alembic, mypy")
    parser.add_argument("--write", type=Path, help="write the Markdown report here instead of stdout")
    args = parser.parse_args(argv)

    test_results: dict[str, CommandResult | None] = {}
    cache: dict[tuple[str, ...], CommandResult] = {}
    for gate_id, plan in GATE_PLANS.items():
        if not args.run_tests:
            test_results[gate_id] = None
        elif plan.tests in cache:
            test_results[gate_id] = cache[plan.tests]
        else:
            test_results[gate_id] = cache[plan.tests] = run_gate_tests(plan)
    records = {gate_id: evidence_for(gate_id, test_results[gate_id]) for gate_id in GATE_PLANS}
    for record in records.values():
        evaluate_evidence_record(record)  # raises nothing: a bad record is a blocking reason in the report
    checks = run_checks() if args.run_checks else None
    report = render(records, test_results, checks, audit_licences())
    if args.write:
        args.write.write_text(report, encoding="utf-8", newline="\n")
        print(f"wrote {args.write}")
    else:
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")  # the gate names have Vietnamese letters; a Windows console is cp1252
        print(re.sub(r"\n{3,}", "\n\n", report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
