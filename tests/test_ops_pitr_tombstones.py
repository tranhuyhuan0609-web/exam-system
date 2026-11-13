"""Tests for GATE-10 post-PITR tombstone replay and Patroni strict quorum audit (P64)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.ops.pitr_tombstones import (
    ErasureTombstone,
    PatroniQuorumConfig,
    RestoredSubjectRow,
    evaluate_patroni_quorum_config,
    reapply_tombstones_after_pitr_restore,
)


def test_pitr_replay_eliminates_resurrected_pii_and_keeps_active_users() -> None:
    tombstones = [
        ErasureTombstone(
            tenant_id="t1",
            subject_id="erased-user-1",
            erased_at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc),
            certificate_sha256="c" * 64,
        )
    ]
    restored = [
        RestoredSubjectRow(
            tenant_id="t1",
            subject_id="erased-user-1",
            has_active_pii=True,
            pii_fields_present=("email", "phone"),
        ),
        RestoredSubjectRow(
            tenant_id="t1",
            subject_id="active-user-2",
            has_active_pii=True,
            pii_fields_present=("email",),
        ),
    ]

    report = reapply_tombstones_after_pitr_restore(
        restored_rows=restored,
        worm_tombstones=tombstones,
    )
    assert report.passed is True
    assert report.tombstones_applied_count == 1
    assert report.resurrected_pii_remaining == 0

    erased_out = next(r for r in report.restored_rows if r.subject_id == "erased-user-1")
    active_out = next(r for r in report.restored_rows if r.subject_id == "active-user-2")
    assert erased_out.has_active_pii is False
    assert erased_out.pii_fields_present == ()
    assert erased_out.scrubbed_by_certificate == "c" * 64
    assert active_out.has_active_pii is True
    assert active_out.scrubbed_by_certificate is None


def test_patroni_quorum_check_enforces_strict_rpo_zero_and_rto_bounds() -> None:
    good = evaluate_patroni_quorum_config(
        PatroniQuorumConfig(
            synchronous_mode=True,
            synchronous_mode_strict=True,
            synchronous_commit="on",
            healthy_sync_standbys=1,
            measured_failover_rto_seconds=18.5,
            measured_pitr_rto_minutes=75.0,
        )
    )
    assert good.compliant is True
    assert good.rpo_zero_guaranteed is True

    bad = evaluate_patroni_quorum_config(
        PatroniQuorumConfig(
            synchronous_mode=True,
            synchronous_mode_strict=False,
            synchronous_commit="off",
            healthy_sync_standbys=0,
            measured_failover_rto_seconds=45.0,
            measured_pitr_rto_minutes=150.0,
        )
    )
    assert bad.compliant is False
    assert bad.rpo_zero_guaranteed is False
    assert "PATRONI_STRICT_QUORUM_DISABLED" in bad.violations
    assert "NO_HEALTHY_SYNC_STANDBY" in bad.violations
    assert any(v.startswith("FAILOVER_RTO_EXCEEDED") for v in bad.violations)
    assert any(v.startswith("PITR_RTO_EXCEEDED") for v in bad.violations)
