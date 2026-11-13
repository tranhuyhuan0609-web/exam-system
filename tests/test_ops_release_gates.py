"""Tests for V11.3 canonical Release Gates & Evidence Bundle evaluator (P58)."""

from __future__ import annotations

from app.ops.release_gates import (
    CANONICAL_RELEASE_GATES,
    GateEvidenceRecord,
    HardwareBenchmarkSpec,
    compute_little_law_capacity,
    evaluate_sprint_exit_readiness,
)


def _valid_benchmark() -> HardwareBenchmarkSpec:
    return HardwareBenchmarkSpec(
        commit_sha="55c926b",
        pg_version="17.2",
        pgbouncer_pool_size=60,
        cpu_cores=16,
        ram_gb=64,
        nvme_iops=40000,
        rpo_zero_verified=True,
    )


def test_canonical_gate_ids_match_manifest_count_and_little_law_sizing() -> None:
    gate_ids = {g.gate_id for g in CANONICAL_RELEASE_GATES}
    assert len(CANONICAL_RELEASE_GATES) == 11
    assert "GATE-06a" in gate_ids and "GATE-06b" in gate_ids

    # Chapter 9.3C derivation: 5000 CCU, 15s cycle -> 333.33 RPS, 12ms -> 4.0 active conns
    cap = compute_little_law_capacity(
        concurrent_users=5000,
        avg_cycle_seconds=15.0,
        db_latency_ms=12.0,
        pgbouncer_default_pool_size=60,
    )
    assert cap.rps == 333.33
    assert cap.active_db_connections == 4.0
    assert cap.within_pool_capacity is True


def test_sprint_6_exit_requires_real_infra_and_rpo_zero_benchmark() -> None:
    sprint6_gates = [g.gate_id for g in CANONICAL_RELEASE_GATES if g.target_sprint <= 6]
    evidence = {
        gid: GateEvidenceRecord(
            gate_id=gid,
            maturity="VERIFIED",
            passed=True,
            real_infra_executed=True,
            benchmark=_valid_benchmark() if gid in {"GATE-04", "GATE-05"} else None,
        )
        for gid in sprint6_gates
    }
    ok = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert ok.ready_for_release is True
    assert set(ok.required_gates) == {
        "GATE-01",
        "GATE-02",
        "GATE-03",
        "GATE-04",
        "GATE-05",
        "GATE-06a",
        "GATE-07",
    }

    # Downgrade GATE-05 to synthetic-only (not executed on real Patroni/k6) -> blocked
    evidence["GATE-05"] = GateEvidenceRecord(
        gate_id="GATE-05",
        maturity="TESTED",
        passed=True,
        real_infra_executed=False,
        benchmark=None,
    )
    blocked = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert blocked.ready_for_release is False
    assert any("GATE-05:INSUFFICIENT_MATURITY:TESTED" in r for r in blocked.blocking_reasons)
    assert any("GATE-05:SYNTHETIC_ONLY_CANNOT_CLAIM_VERIFIED" in r for r in blocked.blocking_reasons)
    assert any("GATE-05:MISSING_BENCHMARK_EVIDENCE_BUNDLE" in r for r in blocked.blocking_reasons)


# --- regression + boundary tests --------------------------------------------------------------


def _record(gid: str, **kw) -> GateEvidenceRecord:
    base = dict(
        gate_id=gid,
        maturity="VERIFIED",
        passed=True,
        real_infra_executed=True,
        benchmark=_valid_benchmark() if gid in {"GATE-04", "GATE-05", "GATE-10"} else None,
    )
    base.update(kw)
    return GateEvidenceRecord(**base)


def _full_evidence(target_sprint: int) -> dict[str, GateEvidenceRecord]:
    return {g.gate_id: _record(g.gate_id) for g in CANONICAL_RELEASE_GATES if g.target_sprint <= target_sprint}


def test_record_filed_under_wrong_gate_blocks_with_mismatch() -> None:
    evidence = _full_evidence(6)
    # A GATE-06a record (no benchmark needed) must not satisfy GATE-04.
    evidence["GATE-04"] = GateEvidenceRecord("GATE-06a", "VERIFIED", True, True, None)
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert report.ready_for_release is False
    assert "GATE-04:EVIDENCE_GATE_MISMATCH" in report.blocking_reasons


def test_mismatch_blocks_even_when_both_gates_would_pass() -> None:
    evidence = _full_evidence(6)
    evidence["GATE-04"] = _record("GATE-05")
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert "GATE-04:EVIDENCE_GATE_MISMATCH" in report.blocking_reasons


def test_all_gates_through_sprint_14_with_valid_evidence_are_ready() -> None:
    report = evaluate_sprint_exit_readiness(target_sprint=14, evidence_by_gate=_full_evidence(14))
    assert report.ready_for_release is True
    assert len(report.required_gates) == 11


def test_missing_evidence_is_reported_per_gate() -> None:
    evidence = _full_evidence(6)
    del evidence["GATE-03"]
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert report.blocking_reasons == ("GATE-03:MISSING_EVIDENCE",)


def test_sprint_before_first_gate_requires_nothing() -> None:
    report = evaluate_sprint_exit_readiness(target_sprint=0, evidence_by_gate={})
    assert report.required_gates == ()
    assert report.ready_for_release is True


def test_passed_false_blocks() -> None:
    evidence = _full_evidence(6)
    evidence["GATE-01"] = _record("GATE-01", passed=False)
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert "GATE-01:NOT_PASSED" in report.blocking_reasons


def test_unknown_maturity_value_blocks_instead_of_raising() -> None:
    evidence = _full_evidence(6)
    evidence["GATE-01"] = _record("GATE-01", maturity="DONE")  # type: ignore[arg-type]
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert report.ready_for_release is False
    assert "GATE-01:UNKNOWN_MATURITY:DONE" in report.blocking_reasons


def test_rpo_zero_not_verified_blocks_gate_10() -> None:
    bench = HardwareBenchmarkSpec("abc1234", "17.2", 60, 16, 64, 40000, rpo_zero_verified=False)
    evidence = _full_evidence(14)
    evidence["GATE-10"] = _record("GATE-10", benchmark=bench)
    report = evaluate_sprint_exit_readiness(target_sprint=14, evidence_by_gate=evidence)
    assert "GATE-10:RPO_ZERO_NOT_VERIFIED" in report.blocking_reasons


def test_invalid_hardware_spec_and_blank_commit_block() -> None:
    bad = HardwareBenchmarkSpec("  ", "", 0, 0, 0, 0, rpo_zero_verified=True)
    evidence = _full_evidence(6)
    evidence["GATE-05"] = _record("GATE-05", benchmark=bad)
    report = evaluate_sprint_exit_readiness(target_sprint=6, evidence_by_gate=evidence)
    assert "GATE-05:MISSING_COMMIT_OR_PG_VERSION" in report.blocking_reasons
    assert "GATE-05:INVALID_HARDWARE_SPEC" in report.blocking_reasons


def test_unknown_gate_id_record_is_rejected() -> None:
    from app.ops.release_gates import evaluate_evidence_record

    ok, errs = evaluate_evidence_record(_record("GATE-99"))
    assert ok is False and errs == ("UNKNOWN_GATE_ID:GATE-99",)


def test_little_law_boundary_exactly_at_eighty_percent() -> None:
    # 60 * 0.8 = 48 connections is allowed, anything above is not.
    at_limit = compute_little_law_capacity(
        concurrent_users=4800, avg_cycle_seconds=1.0, db_latency_ms=10.0, pgbouncer_default_pool_size=60
    )
    assert at_limit.active_db_connections == 48.0 and at_limit.within_pool_capacity is True
    over = compute_little_law_capacity(
        concurrent_users=4801, avg_cycle_seconds=1.0, db_latency_ms=10.0, pgbouncer_default_pool_size=60
    )
    assert over.within_pool_capacity is False


def test_little_law_rejects_invalid_and_non_finite_parameters() -> None:
    import pytest

    for kwargs in (
        dict(concurrent_users=0, avg_cycle_seconds=1.0, db_latency_ms=1.0),
        dict(concurrent_users=1, avg_cycle_seconds=0.0, db_latency_ms=1.0),
        dict(concurrent_users=1, avg_cycle_seconds=1.0, db_latency_ms=-1.0),
        dict(concurrent_users=1, avg_cycle_seconds=float("nan"), db_latency_ms=1.0),
        dict(concurrent_users=1, avg_cycle_seconds=1.0, db_latency_ms=float("inf")),
        dict(concurrent_users=1, avg_cycle_seconds=1.0, db_latency_ms=1.0, pgbouncer_default_pool_size=0),
    ):
        with pytest.raises(ValueError):
            compute_little_law_capacity(**kwargs)
