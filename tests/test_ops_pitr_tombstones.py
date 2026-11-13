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

    plan = reapply_tombstones_after_pitr_restore(restored_rows=restored, worm_tombstones=tombstones)
    # the scrub job runs, then the rows are re-read from the database for verification
    reread = [
        RestoredSubjectRow(r.tenant_id, r.subject_id, r.has_active_pii, r.pii_fields_present)
        for r in plan.restored_rows
    ]
    report = reapply_tombstones_after_pitr_restore(
        restored_rows=restored,
        worm_tombstones=tombstones,
        post_scrub_rows=reread,
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


# --- regression + boundary tests --------------------------------------------------------------

import math  # noqa: E402

import pytest  # noqa: E402

from app.ops.pitr_tombstones import (  # noqa: E402
    PitrTombstoneError,
)

_CERT = "a" * 64
_WHEN = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _tomb(tenant="t1", subject="u1", cert=_CERT) -> ErasureTombstone:
    return ErasureTombstone(tenant_id=tenant, subject_id=subject, erased_at=_WHEN, certificate_sha256=cert)


def _row(tenant="t1", subject="u1", pii=True, fields=("email",)) -> RestoredSubjectRow:
    return RestoredSubjectRow(tenant_id=tenant, subject_id=subject, has_active_pii=pii, pii_fields_present=fields)


def test_without_post_scrub_rows_the_drill_cannot_pass() -> None:
    """Old behaviour: output rows were forced clean, so `passed` was always True."""
    report = reapply_tombstones_after_pitr_restore(restored_rows=[_row()], worm_tombstones=[_tomb()])
    assert report.passed is False
    assert report.verified_against_post_scrub_rows is False
    assert report.resurrected_pii_found == 1
    assert report.tombstones_applied_count == 1


def test_post_scrub_rows_with_remaining_pii_fail_the_gate() -> None:
    report = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row()], worm_tombstones=[_tomb()], post_scrub_rows=[_row()]
    )
    assert report.passed is False
    assert report.resurrected_pii_remaining == 1
    assert report.verified_against_post_scrub_rows is True


def test_post_scrub_rows_clean_pass_and_other_subjects_may_keep_pii() -> None:
    clean = _row(pii=False, fields=())
    report = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row(), _row(subject="u2")],
        worm_tombstones=[_tomb()],
        post_scrub_rows=[clean, _row(subject="u2")],
    )
    assert report.passed is True and report.resurrected_pii_remaining == 0


@pytest.mark.parametrize("variant", ["T1", " t1 ", "T1\t"])
def test_tenant_id_matching_is_case_and_whitespace_insensitive(variant: str) -> None:
    plan = reapply_tombstones_after_pitr_restore(restored_rows=[_row(tenant=variant)], worm_tombstones=[_tomb()])
    assert plan.tombstones_applied_count == 1
    assert plan.restored_rows[0].has_active_pii is False
    leftover = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row(tenant=variant)], worm_tombstones=[_tomb()], post_scrub_rows=[_row(tenant=variant)]
    )
    assert leftover.passed is False and leftover.resurrected_pii_remaining == 1


def test_uuid_case_mismatch_between_tombstone_and_row() -> None:
    uid = "0F8FAD5B-D9CB-469F-A165-70867728950E"
    plan = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row(subject=uid.lower())], worm_tombstones=[_tomb(subject=uid)]
    )
    assert plan.tombstones_applied_count == 1


def test_same_subject_in_another_tenant_is_not_scrubbed() -> None:
    plan = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row(tenant="t2")], worm_tombstones=[_tomb(tenant="t1")]
    )
    assert plan.tombstones_applied_count == 0
    assert plan.restored_rows[0].has_active_pii is True


def test_pii_fields_alone_count_as_pii_and_already_clean_rows_are_counted() -> None:
    plan = reapply_tombstones_after_pitr_restore(
        restored_rows=[_row(pii=False, fields=("phone",)), _row(subject="u3", pii=False, fields=())],
        worm_tombstones=[_tomb(), _tomb(subject="u3")],
    )
    assert plan.tombstones_applied_count == 1
    assert plan.already_clean_count == 1


def test_empty_inputs() -> None:
    plan = reapply_tombstones_after_pitr_restore(restored_rows=[], worm_tombstones=[], post_scrub_rows=[])
    assert plan.passed is True and plan.restored_rows == ()


@pytest.mark.parametrize("cert", ["", "short", "g" * 64, "a" * 63, "a" * 65, None])
def test_malformed_tombstone_certificates_are_rejected(cert) -> None:
    with pytest.raises(PitrTombstoneError):
        reapply_tombstones_after_pitr_restore(restored_rows=[_row()], worm_tombstones=[_tomb(cert=cert)])


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_blank_identifiers_are_rejected_on_rows_and_tombstones(bad) -> None:
    with pytest.raises(PitrTombstoneError):
        reapply_tombstones_after_pitr_restore(restored_rows=[_row(subject=bad)], worm_tombstones=[_tomb()])
    with pytest.raises(PitrTombstoneError):
        reapply_tombstones_after_pitr_restore(restored_rows=[_row()], worm_tombstones=[_tomb(tenant=bad)])


def test_error_message_does_not_echo_certificate_or_ids() -> None:
    with pytest.raises(PitrTombstoneError) as info:
        reapply_tombstones_after_pitr_restore(restored_rows=[], worm_tombstones=[_tomb(cert="SECRET-CERT")])
    assert "SECRET-CERT" not in str(info.value)


# --- Patroni -------------------------------------------------------------------------------


def _cfg(**kw) -> PatroniQuorumConfig:
    base = dict(
        synchronous_mode=True, synchronous_mode_strict=True, synchronous_commit="on",
        healthy_sync_standbys=1, measured_failover_rto_seconds=18.5, measured_pitr_rto_minutes=75.0,
    )
    base.update(kw)
    return PatroniQuorumConfig(**base)


def test_nan_and_infinite_rto_values_are_never_compliant() -> None:
    for field in ("measured_failover_rto_seconds", "measured_pitr_rto_minutes"):
        for bad in (math.nan, math.inf, -math.inf, -1.0):
            report = evaluate_patroni_quorum_config(_cfg(**{field: bad}))
            assert report.compliant is False
            assert any(v.startswith("INVALID_") for v in report.violations)
            assert report.rpo_zero_guaranteed is True  # RPO depends on replication settings, not RTO


def test_rto_boundaries_are_inclusive() -> None:
    assert evaluate_patroni_quorum_config(_cfg(measured_failover_rto_seconds=30, measured_pitr_rto_minutes=120)).compliant
    over = evaluate_patroni_quorum_config(_cfg(measured_failover_rto_seconds=30.001, measured_pitr_rto_minutes=120.001))
    assert [v.split(":")[0] for v in over.violations] == ["FAILOVER_RTO_EXCEEDED", "PITR_RTO_EXCEEDED"]
    assert evaluate_patroni_quorum_config(_cfg(measured_failover_rto_seconds=0, measured_pitr_rto_minutes=0)).compliant


@pytest.mark.parametrize("value", ["off", "local", "remote_write", "", "  ", None, 5])
def test_unsafe_synchronous_commit_values(value) -> None:
    report = evaluate_patroni_quorum_config(_cfg(synchronous_commit=value))
    assert report.compliant is False and report.rpo_zero_guaranteed is False
    assert any(v.startswith("UNSAFE_SYNCHRONOUS_COMMIT") for v in report.violations)


@pytest.mark.parametrize("value", ["on", "ON", " remote_apply "])
def test_safe_synchronous_commit_values(value: str) -> None:
    assert evaluate_patroni_quorum_config(_cfg(synchronous_commit=value)).compliant is True


def test_sync_mode_flags_must_be_literally_true() -> None:
    assert evaluate_patroni_quorum_config(_cfg(synchronous_mode=False)).violations == ("PATRONI_SYNC_MODE_DISABLED",)
    assert evaluate_patroni_quorum_config(_cfg(synchronous_mode="yes")).compliant is False  # type: ignore[arg-type]
    assert evaluate_patroni_quorum_config(_cfg(synchronous_mode_strict=None)).compliant is False  # type: ignore[arg-type]


@pytest.mark.parametrize("standbys", [0, -1, True, 1.5, None])
def test_healthy_standby_count_must_be_a_positive_integer(standbys) -> None:
    report = evaluate_patroni_quorum_config(_cfg(healthy_sync_standbys=standbys))
    assert "NO_HEALTHY_SYNC_STANDBY" in report.violations and report.rpo_zero_guaranteed is False


def test_non_numeric_rto_is_invalid() -> None:
    report = evaluate_patroni_quorum_config(_cfg(measured_failover_rto_seconds="10"))  # type: ignore[arg-type]
    assert "INVALID_FAILOVER_RTO_MEASUREMENT" in report.violations
