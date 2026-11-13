"""GATE-10 & RB-11 Disaster Recovery: post-PITR WORM tombstone replay & Patroni strict quorum check."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

MAX_EXAM_FAILOVER_RTO_SECONDS = 30
MAX_REGIONAL_PITR_RTO_MINUTES = 120


@dataclass(frozen=True)
class ErasureTombstone:
    tenant_id: str
    subject_id: str
    erased_at: datetime
    certificate_sha256: str


@dataclass(frozen=True)
class RestoredSubjectRow:
    tenant_id: str
    subject_id: str
    has_active_pii: bool
    pii_fields_present: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScrubbedSubjectRow:
    tenant_id: str
    subject_id: str
    has_active_pii: bool
    pii_fields_present: tuple[str, ...]
    scrubbed_by_certificate: str | None


@dataclass(frozen=True)
class PitrTombstoneReplayReport:
    restored_rows: tuple[ScrubbedSubjectRow, ...]
    tombstones_applied_count: int
    already_clean_count: int
    resurrected_pii_remaining: int
    passed: bool


@dataclass(frozen=True)
class PatroniQuorumConfig:
    synchronous_mode: bool
    synchronous_mode_strict: bool
    synchronous_commit: str
    healthy_sync_standbys: int
    measured_failover_rto_seconds: float
    measured_pitr_rto_minutes: float


@dataclass(frozen=True)
class PatroniQuorumReport:
    compliant: bool
    rpo_zero_guaranteed: bool
    violations: tuple[str, ...]


def reapply_tombstones_after_pitr_restore(
    *,
    restored_rows: list[RestoredSubjectRow] | tuple[RestoredSubjectRow, ...],
    worm_tombstones: list[ErasureTombstone] | tuple[ErasureTombstone, ...],
) -> PitrTombstoneReplayReport:
    """GATE-10: prevent deleted learner/teacher PII from resurrecting after PITR restore.

    Replays immutable WORM erasure certificates against rows restored from a
    pre-erasure PostgreSQL WAL/basebackup point-in-time snapshot.
    """
    tombstone_map: dict[tuple[str, str], ErasureTombstone] = {
        (t.tenant_id.strip(), t.subject_id.strip()): t for t in worm_tombstones
    }

    output: list[ScrubbedSubjectRow] = []
    applied = 0
    already_clean = 0
    resurrected_remaining = 0

    for row in restored_rows:
        key = (row.tenant_id.strip(), row.subject_id.strip())
        tombstone = tombstone_map.get(key)
        if tombstone is not None:
            if row.has_active_pii or row.pii_fields_present:
                applied += 1
            else:
                already_clean += 1
            output.append(
                ScrubbedSubjectRow(
                    tenant_id=row.tenant_id,
                    subject_id=row.subject_id,
                    has_active_pii=False,
                    pii_fields_present=(),
                    scrubbed_by_certificate=tombstone.certificate_sha256,
                )
            )
        else:
            output.append(
                ScrubbedSubjectRow(
                    tenant_id=row.tenant_id,
                    subject_id=row.subject_id,
                    has_active_pii=row.has_active_pii,
                    pii_fields_present=row.pii_fields_present,
                    scrubbed_by_certificate=None,
                )
            )

    for item in output:
        key = (item.tenant_id.strip(), item.subject_id.strip())
        if key in tombstone_map and (item.has_active_pii or item.pii_fields_present):
            resurrected_remaining += 1

    return PitrTombstoneReplayReport(
        restored_rows=tuple(output),
        tombstones_applied_count=applied,
        already_clean_count=already_clean,
        resurrected_pii_remaining=resurrected_remaining,
        passed=resurrected_remaining == 0,
    )


def evaluate_patroni_quorum_config(config: PatroniQuorumConfig) -> PatroniQuorumReport:
    """Validate Patroni synchronous strict mode & DR RTO targets for GATE-10."""
    violations: list[str] = []
    if not config.synchronous_mode:
        violations.append("PATRONI_SYNC_MODE_DISABLED")
    if not config.synchronous_mode_strict:
        violations.append("PATRONI_STRICT_QUORUM_DISABLED")
    if config.synchronous_commit.strip().lower() not in {"on", "remote_apply"}:
        violations.append(f"UNSAFE_SYNCHRONOUS_COMMIT:{config.synchronous_commit}")
    if config.healthy_sync_standbys < 1:
        violations.append("NO_HEALTHY_SYNC_STANDBY")
    if config.measured_failover_rto_seconds > MAX_EXAM_FAILOVER_RTO_SECONDS:
        violations.append(
            f"FAILOVER_RTO_EXCEEDED:{config.measured_failover_rto_seconds}s>{MAX_EXAM_FAILOVER_RTO_SECONDS}s"
        )
    if config.measured_pitr_rto_minutes > MAX_REGIONAL_PITR_RTO_MINUTES:
        violations.append(
            f"PITR_RTO_EXCEEDED:{config.measured_pitr_rto_minutes}m>{MAX_REGIONAL_PITR_RTO_MINUTES}m"
        )

    rpo_zero = (
        config.synchronous_mode
        and config.synchronous_mode_strict
        and config.synchronous_commit.strip().lower() in {"on", "remote_apply"}
        and config.healthy_sync_standbys >= 1
    )
    return PatroniQuorumReport(
        compliant=len(violations) == 0,
        rpo_zero_guaranteed=rpo_zero,
        violations=tuple(violations),
    )
