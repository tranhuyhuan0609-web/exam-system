"""Tests for V11.3 canonical Release Gates & Evidence Bundle evaluator (P58)."""

from __future__ import annotations

from app.platform.release_gates import (
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
