"""G04 Learning & Operational Analytics Warehouse daily rollup metrics (WLAU, Pipeline Yield, Mastery Gain)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

_LEARNING_ACTIVE_EVENT_KINDS: frozenset[str] = frozenset(
    {"PRACTICE_COMPLETED", "EXAM_SUBMITTED", "FLASHCARD_REVIEWED", "ERROR_REMEDIATED"}
)


class AnalyticsEventType(StrEnum):
    PRACTICE_COMPLETED = "PRACTICE_COMPLETED"
    EXAM_SUBMITTED = "EXAM_SUBMITTED"
    FLASHCARD_REVIEWED = "FLASHCARD_REVIEWED"
    ERROR_REMEDIATED = "ERROR_REMEDIATED"
    PIPELINE_EXTRACTED = "PIPELINE_EXTRACTED"
    PIPELINE_AUTO_VERIFIED = "PIPELINE_AUTO_VERIFIED"
    PIPELINE_MANUAL_EDITED = "PIPELINE_MANUAL_EDITED"
    MASTERY_DELTA = "MASTERY_DELTA"


@dataclass(frozen=True)
class AnalyticsEvent:
    event_id: str
    tenant_id: str
    event_date: date
    event_type: AnalyticsEventType
    learner_id: str | None = None
    count: int = 1
    pre_mastery: float | None = None
    post_mastery: float | None = None


@dataclass(frozen=True)
class DailyMetricRollup:
    tenant_id: str
    metric_date: date
    dau_count: int
    wlau_count: int
    extracted_total: int
    auto_verified_total: int
    manual_edited_total: int
    pipeline_yield_rate: float
    manual_edit_rate: float
    avg_mastery_gain: float


def compute_daily_metric_rollup(
    *,
    tenant_id: str,
    metric_date: date,
    events: tuple[AnalyticsEvent, ...],
) -> DailyMetricRollup:
    """Aggregate normalized events into read-only DailyMetricRollup KPIs without OLTP hits."""
    if not tenant_id.strip():
        raise ValueError("tenant_id is required")

    wlau_start = metric_date - timedelta(days=6)
    dau_learners: set[str] = set()
    wlau_learners: set[str] = set()

    extracted_total = 0
    auto_verified_total = 0
    manual_edited_total = 0
    mastery_deltas: list[float] = []

    for ev in events:
        if ev.tenant_id != tenant_id:
            continue

        if ev.event_type.value in _LEARNING_ACTIVE_EVENT_KINDS and ev.learner_id:
            if wlau_start <= ev.event_date <= metric_date:
                wlau_learners.add(ev.learner_id)
            if ev.event_date == metric_date:
                dau_learners.add(ev.learner_id)

        if ev.event_date != metric_date:
            continue

        if ev.event_type == AnalyticsEventType.PIPELINE_EXTRACTED:
            extracted_total += max(0, ev.count)
        elif ev.event_type == AnalyticsEventType.PIPELINE_AUTO_VERIFIED:
            auto_verified_total += max(0, ev.count)
        elif ev.event_type == AnalyticsEventType.PIPELINE_MANUAL_EDITED:
            manual_edited_total += max(0, ev.count)
        elif (
            ev.event_type == AnalyticsEventType.MASTERY_DELTA
            and ev.pre_mastery is not None
            and ev.post_mastery is not None
        ):
            mastery_deltas.append(ev.post_mastery - ev.pre_mastery)

    yield_rate = round(auto_verified_total / extracted_total, 4) if extracted_total > 0 else 0.0
    edit_rate = round(manual_edited_total / extracted_total, 4) if extracted_total > 0 else 0.0
    avg_gain = round(sum(mastery_deltas) / len(mastery_deltas), 4) if mastery_deltas else 0.0

    return DailyMetricRollup(
        tenant_id=tenant_id,
        metric_date=metric_date,
        dau_count=len(dau_learners),
        wlau_count=len(wlau_learners),
        extracted_total=extracted_total,
        auto_verified_total=auto_verified_total,
        manual_edited_total=manual_edited_total,
        pipeline_yield_rate=yield_rate,
        manual_edit_rate=edit_rate,
        avg_mastery_gain=avg_gain,
    )
