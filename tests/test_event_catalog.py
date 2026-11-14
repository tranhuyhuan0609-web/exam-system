"""Tests for app.contracts.event_catalog (all 18 V11.3 Event Bus contracts)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.event_catalog import (
    EVENT_CATALOG,
    AiBudgetThresholdExceededPayload,
    AttemptExpiredPayload,
    AttemptGradedPayload,
    AttemptSubmittedPayload,
    CrawlParserDriftDetectedPayload,
    RightsTakedownExecutedPayload,
    build_catalog_envelope,
    validate_event_payload,
)


def test_catalog_contains_all_18_canonical_events_and_validates_payloads() -> None:
    assert len(EVENT_CATALOG) == 18

    tid = uuid.uuid4()
    aid = uuid.uuid4()
    gid = uuid.uuid4()
    wrong_q = uuid.uuid4()

    graded = AttemptGradedPayload(
        tenant_id=tid,
        attempt_id=aid,
        grade_id=gid,
        revision_no=1,
        total_score=Decimal("8.75"),
        wrong_question_version_ids=(wrong_q,),
    )
    envelope = build_catalog_envelope(
        event_type="attempt.graded",
        aggregate_type="attempt",
        aggregate_id=aid,
        payload=graded,
        correlation_id="corr-exam-001",
    )
    assert envelope.event_type == "attempt.graded"
    assert envelope.tenant_id == tid
    assert envelope.payload["total_score"] == "8.75"
    assert envelope.payload["wrong_question_version_ids"] == [str(wrong_q)]


def test_catalog_rejects_extra_fields_wrong_model_or_unknown_events() -> None:
    tid = uuid.uuid4()
    with pytest.raises(ValidationError):
        validate_event_payload(
            "rights.takedown.executed",
            {
                "tenant_id": str(tid),
                "takedown_case_id": str(uuid.uuid4()),
                "target_entity_type": "QUESTION",
                "target_entity_id": str(uuid.uuid4()),
                "executed_at": datetime.now(UTC).isoformat(),
                "rogue_extra": True,
            },
        )

    wrong_payload = RightsTakedownExecutedPayload(
        tenant_id=tid,
        takedown_case_id=uuid.uuid4(),
        target_entity_type="CONTENT",
        target_entity_id=uuid.uuid4(),
        executed_at=datetime.now(UTC),
    )
    with pytest.raises(TypeError, match="AttemptSubmittedPayload"):
        build_catalog_envelope(
            event_type="attempt.submitted",
            aggregate_type="attempt",
            aggregate_id=uuid.uuid4(),
            payload=wrong_payload,
        )

    assert AttemptSubmittedPayload.__name__ == "AttemptSubmittedPayload"

    # Drift rate below threshold, inverted expiry timestamps, or spend below threshold rejected
    with pytest.raises(ValidationError):
        CrawlParserDriftDetectedPayload(
            tenant_id=tid,
            source_id=uuid.uuid4(),
            adapter_version="v1",
            empty_yield_rate=0.10,
            threshold=0.20,
        )
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        AttemptExpiredPayload(
            tenant_id=tid,
            attempt_id=uuid.uuid4(),
            deadline_at=now,
            auto_submitted_at=now.replace(year=now.year - 1),
        )
    with pytest.raises(ValidationError):
        AiBudgetThresholdExceededPayload(
            tenant_id=tid,
            current_spend_usd=Decimal("5.00"),
            daily_budget_usd=Decimal("10.00"),
            threshold_pct=80,
        )
