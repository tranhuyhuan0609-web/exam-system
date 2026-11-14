"""V11.3 Chapter 10 operational runbooks registry (`RB-01`..`RB-14`) & incident SLA helpers.

Pure stdlib policy helpers in ``app.ops`` providing:
- Structured registry of all 14 production runbooks with owners, severities, and SLAs,
- Response SLA evaluator (`SEV-1 < 5m`, `SEV-2 < 15m`, `SEV-3 < 2h`),
- `RB-05` exam attempt deadline compensation calculator for autosave outages > 60s,
- `RB-14` 72-hour regulatory notification deadline check (Vietnam Law 91/2025/QH15 -> A05).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

SeverityLevel = Literal["SEV-1", "SEV-2", "SEV-3"]

RESPONSE_SLA_MINUTES: dict[SeverityLevel, int] = {
    "SEV-1": 5,
    "SEV-2": 15,
    "SEV-3": 120,
}

PDP_BREACH_NOTIFY_DEADLINE_HOURS = 72


@dataclass(frozen=True)
class RunbookSpec:
    code: str
    title: str
    severity: SeverityLevel
    owner: str
    trigger_signal: str
    immediate_action: str


CANONICAL_RUNBOOKS: tuple[RunbookSpec, ...] = (
    RunbookSpec("RB-01", "Queue Backlog", "SEV-2", "DevOps + DE On-Call", "oldest_queued_job_age > 180s or queued > 500", "Quarantine poison jobs (attempt >= 3) to DLQ and scale congested worker pool"),
    RunbookSpec("RB-02", "Parser Regression", "SEV-2", "DE/AI Lead", "extraction_confidence < 0.80 spike", "Pin FeatureFlag (G01) back to previous stable parser_version within < 10s"),
    RunbookSpec("RB-03", "Crawler Drift", "SEV-3", "DE On-Call", "crawl.parser.drift_detected (PAUSED_DRIFT)", "Do not resume until HTML selector updated and validated >= 95% yield on 10 sample URLs"),
    RunbookSpec("RB-04", "Google Drive Auth/Disconnect", "SEV-3", "BE On-Call", "401/403 REAUTH_REQUIRED from Drive API", "Set source_connections.status = REAUTH_REQUIRED, preserve existing questions, notify teacher"),
    RunbookSpec("RB-05", "Autosave Anomaly", "SEV-1", "Tech Lead + BE + DevOps", "PUT /attempts/{id}/responses 5xx > 0.5% over 1m", "Never emit fake 200 ACK; compensate deadline_at if outage > 60s"),
    RunbookSpec("RB-06", "Redis Outage", "SEV-2", "DevOps On-Call", "Redis Sentinel/Cluster unreachable", "Exam autosave/submit continues on PostgreSQL; relay outbox_events PENDING after Redis recovery"),
    RunbookSpec("RB-07", "Errata & Regrade", "SEV-2", "Tech Lead + Content Lead", "Confirmed wrong source answer key on published question", "Remove from pool, create question_version+1, regrade with grade_reason=ERRATA_REGRADE"),
    RunbookSpec("RB-08", "LLM Provider Outage", "SEV-2", "DE/AI On-Call", "Repeated 429/503 or latency > 15s", "E01 Circuit Breaker OPEN -> rotate key/fallback provider; core reading/exam flows unaffected"),
    RunbookSpec("RB-09", "Legal Takedown", "SEV-1", "Tech Lead + Legal", "Valid takedown request (POST /api/v1/takedown-cases)", "Execute takedown < 30s, purge search_documents/CDN/Signed URLs, preserve anonymized history"),
    RunbookSpec("RB-10", "Auth / Admin Token Leak", "SEV-1", "Tech Lead + DevOps", "Suspicious access or suspected JWT/OAuth key exposure", "Revoke active sessions, rotate keys in KMS/Vault, trace audit_events and revert via before_json"),
    RunbookSpec("RB-11", "Primary DB Failover & PITR", "SEV-1", "DevOps + Tech Lead", "Primary PostgreSQL unreachable or disk failure", "Patroni auto-failover to Sync Standby (< 30s, RPO=0); PgBouncer reroutes automatically"),
    RunbookSpec("RB-12", "MinIO / Object Storage Outage", "SEV-2", "DevOps On-Call", "503 SlowDown or MinIO cluster down", "Serve exam figure images from CDN cache; pause new uploads during node erasure healing"),
    RunbookSpec("RB-13", "AI Budget Spike / Abuse", "SEV-2", "DE/AI + BE On-Call", "ai.budget.threshold_exceeded (80%/100%)", "Auto-block tenant AI calls (429 TENANT_AI_BUDGET_EXCEEDED), inspect ai_traces"),
    RunbookSpec("RB-14", "Personal Data Breach (91/2025/QH15)", "SEV-1", "Tech Lead + Legal / DPO", "Suspected unauthorized access to student/teacher PII", "Isolate < 15m, scope via audit_events, notify A05 Ministry of Public Security within <= 72h"),
)

_RUNBOOK_MAP: dict[str, RunbookSpec] = {rb.code: rb for rb in CANONICAL_RUNBOOKS}


@dataclass(frozen=True)
class SlaEvaluation:
    runbook_code: str
    severity: SeverityLevel
    sla_limit_minutes: int
    acknowledged_minutes: float
    within_sla: bool


@dataclass(frozen=True)
class DeadlineCompensation:
    apply_extension: bool
    extension_seconds: int
    new_deadline: datetime


@dataclass(frozen=True)
class PdpNotificationDeadline:
    breach_detected_at: datetime
    legal_deadline_at: datetime
    within_72h_window: bool
    hours_remaining: float


def get_runbook(code: str) -> RunbookSpec:
    if not isinstance(code, str) or not code.strip():
        raise ValueError("runbook code must be a non-empty string")
    spec = _RUNBOOK_MAP.get(code.strip().upper())
    if spec is None:
        raise ValueError(f"unknown runbook code: {code!r}")
    return spec


def _require_aware_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def evaluate_incident_response_sla(
    *,
    runbook_code: str,
    acknowledged_minutes: float,
) -> SlaEvaluation:
    if (
        isinstance(acknowledged_minutes, bool)
        or not isinstance(acknowledged_minutes, (int, float))
        or not math.isfinite(acknowledged_minutes)
        or acknowledged_minutes < 0
    ):
        raise ValueError("acknowledged_minutes must be a finite non-negative number")
    spec = get_runbook(runbook_code)
    limit = RESPONSE_SLA_MINUTES[spec.severity]
    return SlaEvaluation(
        runbook_code=spec.code,
        severity=spec.severity,
        sla_limit_minutes=limit,
        acknowledged_minutes=float(acknowledged_minutes),
        within_sla=acknowledged_minutes <= limit,
    )


def compute_rb05_deadline_compensation(
    *,
    current_deadline: datetime,
    outage_duration_seconds: int,
    grace_buffer_seconds: int = 30,
) -> DeadlineCompensation:
    """RB-05: compensate active exam deadline when autosave infrastructure outage > 60s."""
    deadline = _require_aware_datetime(current_deadline, "current_deadline")
    for name, val in (
        ("outage_duration_seconds", outage_duration_seconds),
        ("grace_buffer_seconds", grace_buffer_seconds),
    ):
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if outage_duration_seconds <= 60:
        return DeadlineCompensation(
            apply_extension=False,
            extension_seconds=0,
            new_deadline=deadline,
        )
    ext = outage_duration_seconds + grace_buffer_seconds
    return DeadlineCompensation(
        apply_extension=True,
        extension_seconds=ext,
        new_deadline=deadline + timedelta(seconds=ext),
    )


def evaluate_rb14_breach_notification_window(
    *,
    breach_detected_at: datetime,
    current_or_notified_at: datetime,
) -> PdpNotificationDeadline:
    """RB-14: Vietnam PDP Law 91/2025/QH15 requires A05 notification within 72 hours."""
    detected = _require_aware_datetime(breach_detected_at, "breach_detected_at")
    now = _require_aware_datetime(current_or_notified_at, "current_or_notified_at")
    if now < detected:
        raise ValueError("current_or_notified_at cannot precede breach_detected_at")

    deadline = detected + timedelta(hours=PDP_BREACH_NOTIFY_DEADLINE_HOURS)
    remaining = round((deadline - now).total_seconds() / 3600.0, 2)
    return PdpNotificationDeadline(
        breach_detected_at=detected,
        legal_deadline_at=deadline,
        within_72h_window=now <= deadline,
        hours_remaining=remaining,
    )
