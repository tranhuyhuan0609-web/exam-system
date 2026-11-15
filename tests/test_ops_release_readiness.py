"""app.ops.release_readiness: gate evidence is derived from test outcomes, never invented."""

from __future__ import annotations

from app.ops.release_gates import CANONICAL_RELEASE_GATES, evaluate_sprint_exit_readiness
from app.ops.release_readiness import (
    GATE_PLANS,
    CommandResult,
    audit_licences,
    evidence_for,
    installed_sbom,
    render,
)


def test_every_canonical_gate_has_a_plan_and_every_planned_test_file_exists():
    from app.ops.release_readiness import ROOT

    assert set(GATE_PLANS) == {g.gate_id for g in CANONICAL_RELEASE_GATES}
    for gate_id, plan in GATE_PLANS.items():
        for test in plan.tests:
            assert (ROOT / test).exists(), f"{gate_id}: {test}"


def test_maturity_follows_the_test_outcome_and_real_infrastructure_is_never_claimed():
    passed = evidence_for("GATE-01", CommandResult("t", True, "5 passed"))
    failed = evidence_for("GATE-01", CommandResult("t", False, "1 failed"))
    none = evidence_for("GATE-05", CommandResult("no tests", False, "no automated test backs this gate"))
    unrun = evidence_for("GATE-01", None)
    assert (passed.maturity, passed.passed) == ("TESTED", True)
    assert (failed.maturity, failed.passed) == ("IMPLEMENTED", False)
    assert (none.maturity, none.passed) == ("SPECIFIED", False)
    assert (unrun.maturity, unrun.passed) == ("SPECIFIED", False)
    assert not any(r.real_infra_executed for r in (passed, failed, none, unrun))


def test_passing_tests_on_a_workstation_still_block_the_release():
    records = {g.gate_id: evidence_for(g.gate_id, CommandResult("t", True, "ok")) for g in CANONICAL_RELEASE_GATES}
    report = evaluate_sprint_exit_readiness(target_sprint=14, evidence_by_gate=records)
    assert report.ready_for_release is False
    assert "GATE-01:SYNTHETIC_ONLY_CANNOT_CLAIM_VERIFIED" in report.blocking_reasons
    assert "GATE-01:INSUFFICIENT_MATURITY:TESTED" in report.blocking_reasons


def test_the_report_names_every_gate_and_the_verdict():
    records = {g.gate_id: evidence_for(g.gate_id, None) for g in CANONICAL_RELEASE_GATES}
    text = render(records, {}, [CommandResult("ruff check .", True, "All checks passed!")], audit_licences())
    assert "NOT READY FOR RELEASE" in text and "| `ruff check .` | PASS |" in text
    for gate in CANONICAL_RELEASE_GATES:
        assert f"| {gate.gate_id} |" in text


def test_the_release_dependency_closure_has_no_blocking_licence():
    sbom = installed_sbom()
    assert sbom["components"], "the environment lists its distributions"
    requirements, closure = audit_licences()
    assert requirements.passed, requirements.blocking_violations
    assert closure.passed, closure.blocking_violations
    assert closure.scanned_components >= requirements.scanned_components
