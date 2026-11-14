"""V11.3 canonical Release Gates (GATE-01..GATE-10) & Evidence Bundle evaluator.

Implements maturity level verification (`SPECIFIED` -> `OPERATED` per
Chapter 9.3B), Little's Law concurrency sizing checks (Chapter 9.3C),
and hardware benchmark evidence validation matching ``platform_manifest.yaml``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

MaturityStatus = Literal["SPECIFIED", "IMPLEMENTED", "TESTED", "VERIFIED", "OPERATED"]

_MATURITY_ORDER: dict[MaturityStatus, int] = {
    "SPECIFIED": 0,
    "IMPLEMENTED": 1,
    "TESTED": 2,
    "VERIFIED": 3,
    "OPERATED": 4,
}


class ReleaseGateError(ValueError):
    """Raised when release-gate capacity parameters are invalid."""


@dataclass(frozen=True)
class GateDefinition:
    gate_id: str
    name: str
    target_sprint: int
    requires_real_infra: bool = True
    requires_rpo_zero: bool = False


CANONICAL_RELEASE_GATES: tuple[GateDefinition, ...] = (
    GateDefinition("GATE-01", "Multi-Tenant RLS, InitPlan & Zero-IDOR Gate", 6),
    GateDefinition("GATE-02", "Data Immutability & 5-Axis State Gate", 6),
    GateDefinition("GATE-03", "Exam Security & Zero-Answer-Leak Gate", 6),
    GateDefinition("GATE-04", "Autosave Durability, Dexie Offline & Chaos Gate", 6, requires_rpo_zero=True),
    GateDefinition("GATE-05", "High-Concurrency 5.000 CCU Per-VU Load Gate", 6, requires_rpo_zero=True),
    GateDefinition("GATE-06a", "Golden & Held-out Extraction Gate (Toán & Vật lí)", 6),
    GateDefinition("GATE-06b", "Golden & Held-out Extraction Gate (Hóa học & Sinh học — Đủ 41 đề)", 8),
    GateDefinition("GATE-07", "Deterministic Grading & Option Permutation Gate", 6),
    GateDefinition("GATE-08", "AI Safety, Exam Lock, Subprocess SymPy >= 95% & FSRS-5 Reference Vectors Gate", 9),
    GateDefinition("GATE-09", "Legal DPIA/DPA, Vault Transit Erasure, OSS SBOM & External Pentest Gate", 14),
    GateDefinition(
        "GATE-10",
        "Disaster Recovery, Patroni Strict Quorum & Post-PITR Tombstone Drill Gate",
        14,
        requires_rpo_zero=True,
    ),
)

_GATE_MAP: dict[str, GateDefinition] = {g.gate_id: g for g in CANONICAL_RELEASE_GATES}


@dataclass(frozen=True)
class HardwareBenchmarkSpec:
    commit_sha: str
    pg_version: str
    pgbouncer_pool_size: int
    cpu_cores: int
    ram_gb: int
    nvme_iops: int
    rpo_zero_verified: bool = False


@dataclass(frozen=True)
class GateEvidenceRecord:
    gate_id: str
    maturity: MaturityStatus
    passed: bool
    real_infra_executed: bool
    benchmark: HardwareBenchmarkSpec | None = None


@dataclass(frozen=True)
class SprintExitReport:
    target_sprint: int
    ready_for_release: bool
    required_gates: tuple[str, ...]
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LittleLawCapacityReport:
    concurrent_users: int
    avg_cycle_seconds: float
    db_latency_ms: float
    rps: float
    active_db_connections: float
    pgbouncer_default_pool_size: int
    within_pool_capacity: bool


def evaluate_evidence_record(record: GateEvidenceRecord) -> tuple[bool, tuple[str, ...]]:
    """Enforce Chapter 9 rule: never mark VERIFIED without passing real infra checks."""
    gate = _GATE_MAP.get(record.gate_id)
    if gate is None:
        return False, (f"UNKNOWN_GATE_ID:{record.gate_id}",)

    errors: list[str] = []
    if record.passed is not True:
        errors.append(f"{record.gate_id}:NOT_PASSED")
    maturity_rank = _MATURITY_ORDER.get(record.maturity)
    if maturity_rank is None:
        errors.append(f"{record.gate_id}:UNKNOWN_MATURITY:{str(record.maturity)[:32]}")
    elif maturity_rank < _MATURITY_ORDER["VERIFIED"]:
        errors.append(f"{record.gate_id}:INSUFFICIENT_MATURITY:{record.maturity}")
    if gate.requires_real_infra and record.real_infra_executed is not True:
        errors.append(f"{record.gate_id}:SYNTHETIC_ONLY_CANNOT_CLAIM_VERIFIED")

    if gate.gate_id in {"GATE-04", "GATE-05", "GATE-10"}:
        if record.benchmark is None:
            errors.append(f"{record.gate_id}:MISSING_BENCHMARK_EVIDENCE_BUNDLE")
        else:
            b = record.benchmark
            if not b.commit_sha.strip() or not b.pg_version.strip():
                errors.append(f"{record.gate_id}:MISSING_COMMIT_OR_PG_VERSION")
            if b.cpu_cores <= 0 or b.ram_gb <= 0 or b.nvme_iops <= 0 or b.pgbouncer_pool_size <= 0:
                errors.append(f"{record.gate_id}:INVALID_HARDWARE_SPEC")
            if gate.requires_rpo_zero and not b.rpo_zero_verified:
                errors.append(f"{record.gate_id}:RPO_ZERO_NOT_VERIFIED")

    return len(errors) == 0, tuple(errors)


def evaluate_sprint_exit_readiness(
    *,
    target_sprint: int,
    evidence_by_gate: dict[str, GateEvidenceRecord],
) -> SprintExitReport:
    """Evaluate all manifest gates due on or before ``target_sprint``."""
    required = [g for g in CANONICAL_RELEASE_GATES if g.target_sprint <= target_sprint]
    blocking: list[str] = []

    for gate in required:
        rec = evidence_by_gate.get(gate.gate_id)
        if rec is None:
            blocking.append(f"{gate.gate_id}:MISSING_EVIDENCE")
            continue
        if rec.gate_id != gate.gate_id:
            blocking.append(f"{gate.gate_id}:EVIDENCE_GATE_MISMATCH")
            continue
        ok, errs = evaluate_evidence_record(rec)
        if not ok:
            blocking.extend(errs)

    return SprintExitReport(
        target_sprint=target_sprint,
        ready_for_release=len(blocking) == 0,
        required_gates=tuple(g.gate_id for g in required),
        blocking_reasons=tuple(blocking),
    )


def compute_little_law_capacity(
    *,
    concurrent_users: int,
    avg_cycle_seconds: float,
    db_latency_ms: float,
    pgbouncer_default_pool_size: int = 60,
) -> LittleLawCapacityReport:
    """Compute Little's Law (L = lambda * W) DB concurrency against PgBouncer pool."""
    if not (math.isfinite(avg_cycle_seconds) and math.isfinite(db_latency_ms)):
        raise ReleaseGateError("capacity parameters must be finite")
    if concurrent_users <= 0 or avg_cycle_seconds <= 0 or db_latency_ms < 0 or pgbouncer_default_pool_size <= 0:
        raise ReleaseGateError("capacity parameters must be positive (db_latency_ms >= 0)")

    rps = concurrent_users / avg_cycle_seconds
    active_conns = rps * (db_latency_ms / 1000.0)
    return LittleLawCapacityReport(
        concurrent_users=concurrent_users,
        avg_cycle_seconds=avg_cycle_seconds,
        db_latency_ms=db_latency_ms,
        rps=round(rps, 2),
        active_db_connections=round(active_conns, 2),
        pgbouncer_default_pool_size=pgbouncer_default_pool_size,
        within_pool_capacity=active_conns <= pgbouncer_default_pool_size * 0.8,
    )
