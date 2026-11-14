"""GATE-10 & RB-11 Disaster Recovery: post-PITR WORM tombstone replay & Patroni strict quorum check."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

MAX_EXAM_FAILOVER_RTO_SECONDS = 30
MAX_REGIONAL_PITR_RTO_MINUTES = 120

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_SAFE_SYNC_COMMIT = frozenset({"on", "remote_apply"})


class PitrTombstoneError(ValueError):
    """Raised when tombstones or restored rows are malformed (the drill cannot be trusted)."""


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
    resurrected_pii_found: int = 0
    verified_against_post_scrub_rows: bool = False


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


def _key(tenant_id: object, subject_id: object) -> tuple[str, str]:
    """Identity key: whitespace-stripped, NFKC-normalised and case-folded (UUIDs are case-insensitive)."""
    parts: list[str] = []
    for label, value in (("tenant_id", tenant_id), ("subject_id", subject_id)):
        if not isinstance(value, str) or not value.strip():
            raise PitrTombstoneError(f"{label} must be a non-empty string")
        parts.append(unicodedata.normalize("NFKC", value.strip()).casefold())
    return parts[0], parts[1]


def _has_pii(row: RestoredSubjectRow | ScrubbedSubjectRow) -> bool:
    return bool(row.has_active_pii) or bool(row.pii_fields_present)


def reapply_tombstones_after_pitr_restore(
    *,
    restored_rows: list[RestoredSubjectRow] | tuple[RestoredSubjectRow, ...],
    worm_tombstones: list[ErasureTombstone] | tuple[ErasureTombstone, ...],
    post_scrub_rows: list[RestoredSubjectRow] | tuple[RestoredSubjectRow, ...] | None = None,
) -> PitrTombstoneReplayReport:
    """GATE-10: prevent deleted learner/teacher PII from resurrecting after PITR restore.

    Replays immutable WORM erasure certificates against rows restored from a pre-erasure
    PostgreSQL WAL/basebackup point-in-time snapshot and returns the scrub plan.

    ``passed`` is only true when ``post_scrub_rows`` (the rows re-read from the database AFTER the
    scrub ran) show no PII for any tombstoned subject. Without them the drill cannot attest anything
    and fails closed (``verified_against_post_scrub_rows`` is False).
    """
    tombstone_map: dict[tuple[str, str], ErasureTombstone] = {}
    for tomb in worm_tombstones:
        if not isinstance(tomb.certificate_sha256, str) or _SHA256_RE.fullmatch(tomb.certificate_sha256) is None:
            raise PitrTombstoneError("tombstone certificate must be a 64-character hex SHA-256")
        tombstone_map[_key(tomb.tenant_id, tomb.subject_id)] = tomb

    output: list[ScrubbedSubjectRow] = []
    applied = 0
    already_clean = 0

    for row in restored_rows:
        tombstone = tombstone_map.get(_key(row.tenant_id, row.subject_id))
        if tombstone is not None:
            if _has_pii(row):
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

    remaining = 0
    verified = post_scrub_rows is not None
    if post_scrub_rows is not None:
        remaining = sum(
            1 for row in post_scrub_rows if _key(row.tenant_id, row.subject_id) in tombstone_map and _has_pii(row)
        )

    return PitrTombstoneReplayReport(
        restored_rows=tuple(output),
        tombstones_applied_count=applied,
        already_clean_count=already_clean,
        resurrected_pii_remaining=remaining,
        passed=verified and remaining == 0,
        resurrected_pii_found=applied,
        verified_against_post_scrub_rows=verified,
    )


def _finite_within(value: object, limit: float) -> bool:
    """True only for a finite, non-negative number not above ``limit`` (NaN and inf fail)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and 0 <= value <= limit


def evaluate_patroni_quorum_config(config: PatroniQuorumConfig) -> PatroniQuorumReport:
    """Validate Patroni synchronous strict mode & DR RTO targets for GATE-10 (fails closed on bad data)."""
    violations: list[str] = []
    sync_commit = config.synchronous_commit.strip().lower() if isinstance(config.synchronous_commit, str) else ""
    standbys_ok = (
        isinstance(config.healthy_sync_standbys, int)
        and not isinstance(config.healthy_sync_standbys, bool)
        and config.healthy_sync_standbys >= 1
    )

    if config.synchronous_mode is not True:
        violations.append("PATRONI_SYNC_MODE_DISABLED")
    if config.synchronous_mode_strict is not True:
        violations.append("PATRONI_STRICT_QUORUM_DISABLED")
    if sync_commit not in _SAFE_SYNC_COMMIT:
        violations.append(f"UNSAFE_SYNCHRONOUS_COMMIT:{sync_commit[:16] or 'MISSING'}")
    if not standbys_ok:
        violations.append("NO_HEALTHY_SYNC_STANDBY")

    failover = config.measured_failover_rto_seconds
    if not _finite_within(failover, math.inf):
        violations.append("INVALID_FAILOVER_RTO_MEASUREMENT")
    elif not _finite_within(failover, MAX_EXAM_FAILOVER_RTO_SECONDS):
        violations.append(f"FAILOVER_RTO_EXCEEDED:{failover}s>{MAX_EXAM_FAILOVER_RTO_SECONDS}s")

    pitr = config.measured_pitr_rto_minutes
    if not _finite_within(pitr, math.inf):
        violations.append("INVALID_PITR_RTO_MEASUREMENT")
    elif not _finite_within(pitr, MAX_REGIONAL_PITR_RTO_MINUTES):
        violations.append(f"PITR_RTO_EXCEEDED:{pitr}m>{MAX_REGIONAL_PITR_RTO_MINUTES}m")

    rpo_zero = (
        config.synchronous_mode is True
        and config.synchronous_mode_strict is True
        and sync_commit in _SAFE_SYNC_COMMIT
        and standbys_ok
    )
    return PatroniQuorumReport(
        compliant=len(violations) == 0,
        rpo_zero_guaranteed=rpo_zero,
        violations=tuple(violations),
    )
