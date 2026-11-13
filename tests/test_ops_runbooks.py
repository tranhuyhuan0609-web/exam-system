"""Tests for V11.3 Chapter 10 14 Operational Runbooks & SLA evaluators (P62)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.ops.runbooks import (
    CANONICAL_RUNBOOKS,
    compute_rb05_deadline_compensation,
    evaluate_incident_response_sla,
    evaluate_rb14_breach_notification_window,
)


def test_all_14_canonical_runbooks_present_and_sla_enforced() -> None:
    assert len(CANONICAL_RUNBOOKS) == 14
    assert {r.code for r in CANONICAL_RUNBOOKS} == {f"RB-{i:02d}" for i in range(1, 15)}

    sev1_ok = evaluate_incident_response_sla(runbook_code="RB-05", acknowledged_minutes=4.5)
    assert sev1_ok.severity == "SEV-1"
    assert sev1_ok.sla_limit_minutes == 5
    assert sev1_ok.within_sla is True

    sev1_breach = evaluate_incident_response_sla(runbook_code="RB-11", acknowledged_minutes=6.0)
    assert sev1_breach.within_sla is False

    sev3_ok = evaluate_incident_response_sla(runbook_code="RB-03", acknowledged_minutes=90.0)
    assert sev3_ok.severity == "SEV-3"
    assert sev3_ok.within_sla is True


def test_rb05_deadline_extension_and_rb14_72h_pdp_window() -> None:
    dl = datetime(2026, 11, 13, 14, 0, 0, tzinfo=timezone.utc)
    no_ext = compute_rb05_deadline_compensation(current_deadline=dl, outage_duration_seconds=45)
    assert no_ext.apply_extension is False
    assert no_ext.new_deadline == dl

    with_ext = compute_rb05_deadline_compensation(
        current_deadline=dl,
        outage_duration_seconds=120,
        grace_buffer_seconds=30,
    )
    assert with_ext.apply_extension is True
    assert with_ext.extension_seconds == 150
    assert with_ext.new_deadline == dl + timedelta(seconds=150)

    detected = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)
    on_time = evaluate_rb14_breach_notification_window(
        breach_detected_at=detected,
        current_or_notified_at=detected + timedelta(hours=70),
    )
    assert on_time.within_72h_window is True
    assert on_time.hours_remaining == 2.0

    late = evaluate_rb14_breach_notification_window(
        breach_detected_at=detected,
        current_or_notified_at=detected + timedelta(hours=73),
    )
    assert late.within_72h_window is False
    assert late.hours_remaining == -1.0
