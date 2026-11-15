"""Database service layer for G04 Learning & Operational Analytics Warehouse."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.analytics.internal.models import AnalyticsEventRecord, DailyMetricRollupRecord
from app.modules.analytics.internal.rollups import (
    AnalyticsEvent,
    AnalyticsEventType,
    DailyMetricRollup,
    compute_daily_metric_rollup,
)

METRIC_DAU = "DAU"
METRIC_WLAU = "WLAU"
METRIC_EXTRACTED_TOTAL = "EXTRACTED_TOTAL"
METRIC_AUTO_VERIFIED_TOTAL = "AUTO_VERIFIED_TOTAL"
METRIC_MANUAL_EDITED_TOTAL = "MANUAL_EDITED_TOTAL"
METRIC_PIPELINE_YIELD_RATE = "PIPELINE_YIELD_RATE"
METRIC_MANUAL_EDIT_RATE = "MANUAL_EDIT_RATE"
METRIC_AVG_MASTERY_GAIN = "AVG_MASTERY_GAIN"


class AnalyticsValidationError(ValueError):
    """Raised when analytics event or KPI query parameters fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnalyticsEventView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    event_name: str
    dimensions_json: dict[str, Any]
    metrics_json: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformKpisView:
    tenant_id: uuid.UUID
    metric_date: date | None
    dau_count: int
    wlau_count: int
    extracted_total: int
    auto_verified_total: int
    manual_edited_total: int
    pipeline_yield_rate: float
    manual_edit_rate: float
    avg_mastery_gain: float


@dataclass(frozen=True, slots=True)
class PipelineYieldDayView:
    tenant_id: uuid.UUID
    metric_date: date
    extracted_total: int
    auto_verified_total: int
    manual_edited_total: int
    pipeline_yield_rate: float
    manual_edit_rate: float


async def record_analytics_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    learner_id: uuid.UUID | None = None,
    count: int = 1,
    pre_mastery: float | None = None,
    post_mastery: float | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    extra_dimensions: dict[str, Any] | None = None,
) -> AnalyticsEventView:
    """Persist a normalized analytics event inside tenant RLS for later rollup."""
    try:
        parsed_type = AnalyticsEventType(event_type.strip().upper())
    except ValueError as exc:
        raise AnalyticsValidationError(
            "INVALID_EVENT_TYPE",
            f"Unsupported analytics event_type: {event_type!r}",
        ) from exc

    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise AnalyticsValidationError("INVALID_COUNT", "count must be a non-negative integer")
    for label, val in (("pre_mastery", pre_mastery), ("post_mastery", post_mastery)):
        if val is not None and (isinstance(val, bool) or not math.isfinite(float(val))):
            raise AnalyticsValidationError("INVALID_MASTERY", f"{label} must be a finite number")

    effective_event_id = event_id.strip() if isinstance(event_id, str) and event_id.strip() else str(uuid.uuid4())
    dims: dict[str, Any] = dict(extra_dimensions or {})
    dims["event_id"] = effective_event_id
    if learner_id is not None:
        dims["learner_id"] = str(learner_id)

    metrics: dict[str, Any] = {"count": count}
    if pre_mastery is not None:
        metrics["pre_mastery"] = float(pre_mastery)
    if post_mastery is not None:
        metrics["post_mastery"] = float(post_mastery)

    if occurred_at is not None and occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)

    record = AnalyticsEventRecord(
        tenant_id=tenant_id,
        event_name=parsed_type.value,
        dimensions_json=dims,
        metrics_json=metrics,
        occurred_at=occurred_at or datetime.now(tz=UTC),
    )
    session.add(record)
    await session.flush()
    return AnalyticsEventView(
        id=record.id,
        tenant_id=record.tenant_id,
        event_name=record.event_name,
        dimensions_json=dict(record.dimensions_json),
        metrics_json=dict(record.metrics_json),
        occurred_at=record.occurred_at,
    )


async def run_daily_metric_rollup(
    session: AsyncSession,
    *,
    metric_date: date,
    tenant_ids: Sequence[uuid.UUID] | None = None,
) -> tuple[DailyMetricRollup, ...]:
    """Compute and upsert daily KPI rollups inside `ulp_system_scheduler` (`app.system_job = 'metric_rollup'`)."""
    window_start = datetime.combine(metric_date - timedelta(days=6), datetime.min.time(), tzinfo=UTC)
    window_end = datetime.combine(metric_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)

    stmt = (
        select(AnalyticsEventRecord)
        .where(
            AnalyticsEventRecord.occurred_at >= window_start,
            AnalyticsEventRecord.occurred_at < window_end,
        )
        .order_by(AnalyticsEventRecord.tenant_id.asc(), AnalyticsEventRecord.occurred_at.asc())
    )
    if tenant_ids is not None:
        stmt = stmt.where(AnalyticsEventRecord.tenant_id.in_(list(tenant_ids)))

    rows = list((await session.execute(stmt)).scalars())
    events_by_tenant: dict[uuid.UUID, list[AnalyticsEvent]] = {}
    if tenant_ids is not None:
        for tid in tenant_ids:
            events_by_tenant[tid] = []

    for row in rows:
        try:
            ev_type = AnalyticsEventType(row.event_name)
        except ValueError:
            continue
        dims = row.dimensions_json or {}
        metrics = row.metrics_json or {}
        ev_id = str(dims.get("event_id") or row.id)
        learner_raw = dims.get("learner_id")
        learner_str = str(learner_raw) if learner_raw is not None else None
        raw_count = metrics.get("count", 1)
        count_int = int(raw_count) if not isinstance(raw_count, bool) else 1
        pre_val = float(metrics["pre_mastery"]) if metrics.get("pre_mastery") is not None else None
        post_val = float(metrics["post_mastery"]) if metrics.get("post_mastery") is not None else None

        events_by_tenant.setdefault(row.tenant_id, []).append(
            AnalyticsEvent(
                event_id=ev_id,
                tenant_id=str(row.tenant_id),
                event_date=row.occurred_at.astimezone(UTC).date(),
                event_type=ev_type,
                learner_id=learner_str,
                count=count_int,
                pre_mastery=pre_val,
                post_mastery=post_val,
            )
        )

    results: list[DailyMetricRollup] = []
    for tid, ev_list in sorted(events_by_tenant.items(), key=lambda kv: str(kv[0])):
        rollup = compute_daily_metric_rollup(
            tenant_id=str(tid),
            metric_date=metric_date,
            events=tuple(ev_list),
        )
        metric_pairs = (
            (METRIC_DAU, Decimal(rollup.dau_count)),
            (METRIC_WLAU, Decimal(rollup.wlau_count)),
            (METRIC_EXTRACTED_TOTAL, Decimal(rollup.extracted_total)),
            (METRIC_AUTO_VERIFIED_TOTAL, Decimal(rollup.auto_verified_total)),
            (METRIC_MANUAL_EDITED_TOTAL, Decimal(rollup.manual_edited_total)),
            (METRIC_PIPELINE_YIELD_RATE, Decimal(f"{rollup.pipeline_yield_rate:.4f}")),
            (METRIC_MANUAL_EDIT_RATE, Decimal(f"{rollup.manual_edit_rate:.4f}")),
            (METRIC_AVG_MASTERY_GAIN, Decimal(f"{rollup.avg_mastery_gain:.4f}")),
        )
        for metric_code, metric_val in metric_pairs:
            upsert = (
                pg_insert(DailyMetricRollupRecord)
                .values(
                    metric_date=metric_date,
                    tenant_id=tid,
                    metric_code=metric_code,
                    metric_value=metric_val,
                )
                .on_conflict_do_update(
                    index_elements=["metric_date", "tenant_id", "metric_code"],
                    set_={"metric_value": metric_val},
                )
            )
            await session.execute(upsert)
        results.append(rollup)

    await session.flush()
    return tuple(results)


async def get_platform_kpis(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    metric_date: date | None = None,
) -> PlatformKpisView:
    """Read pre-aggregated KPI rollups ONLY from `daily_metric_rollups` (never touches OLTP tables)."""
    target_date = metric_date
    if target_date is None:
        target_date = (
            await session.execute(
                text(
                    "SELECT MAX(metric_date) FROM daily_metric_rollups WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one_or_none()

    if target_date is None:
        return PlatformKpisView(
            tenant_id=tenant_id,
            metric_date=None,
            dau_count=0,
            wlau_count=0,
            extracted_total=0,
            auto_verified_total=0,
            manual_edited_total=0,
            pipeline_yield_rate=0.0,
            manual_edit_rate=0.0,
            avg_mastery_gain=0.0,
        )

    rows = (
        await session.execute(
            select(DailyMetricRollupRecord).where(
                DailyMetricRollupRecord.tenant_id == tenant_id,
                DailyMetricRollupRecord.metric_date == target_date,
            )
        )
    ).scalars()
    by_code: dict[str, Decimal] = {r.metric_code: Decimal(r.metric_value) for r in rows}

    return PlatformKpisView(
        tenant_id=tenant_id,
        metric_date=target_date,
        dau_count=int(by_code.get(METRIC_DAU, Decimal(0))),
        wlau_count=int(by_code.get(METRIC_WLAU, Decimal(0))),
        extracted_total=int(by_code.get(METRIC_EXTRACTED_TOTAL, Decimal(0))),
        auto_verified_total=int(by_code.get(METRIC_AUTO_VERIFIED_TOTAL, Decimal(0))),
        manual_edited_total=int(by_code.get(METRIC_MANUAL_EDITED_TOTAL, Decimal(0))),
        pipeline_yield_rate=float(by_code.get(METRIC_PIPELINE_YIELD_RATE, Decimal(0))),
        manual_edit_rate=float(by_code.get(METRIC_MANUAL_EDIT_RATE, Decimal(0))),
        avg_mastery_gain=float(by_code.get(METRIC_AVG_MASTERY_GAIN, Decimal(0))),
    )


async def get_pipeline_yield(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[PipelineYieldDayView, ...]:
    """Query daily extraction/auto-verify/edit yield rollups strictly from `daily_metric_rollups`."""
    if start_date is not None and end_date is not None and start_date > end_date:
        raise AnalyticsValidationError("INVALID_DATE_RANGE", "start_date must not follow end_date")

    codes = (
        METRIC_EXTRACTED_TOTAL,
        METRIC_AUTO_VERIFIED_TOTAL,
        METRIC_MANUAL_EDITED_TOTAL,
        METRIC_PIPELINE_YIELD_RATE,
        METRIC_MANUAL_EDIT_RATE,
    )
    stmt = (
        select(DailyMetricRollupRecord)
        .where(
            DailyMetricRollupRecord.tenant_id == tenant_id,
            DailyMetricRollupRecord.metric_code.in_(codes),
        )
        .order_by(DailyMetricRollupRecord.metric_date.asc(), DailyMetricRollupRecord.metric_code.asc())
    )
    if start_date is not None:
        stmt = stmt.where(DailyMetricRollupRecord.metric_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(DailyMetricRollupRecord.metric_date <= end_date)

    by_day: dict[date, dict[str, Decimal]] = {}
    for row in (await session.execute(stmt)).scalars():
        by_day.setdefault(row.metric_date, {})[row.metric_code] = Decimal(row.metric_value)

    views: list[PipelineYieldDayView] = []
    for day, metrics in sorted(by_day.items()):
        views.append(
            PipelineYieldDayView(
                tenant_id=tenant_id,
                metric_date=day,
                extracted_total=int(metrics.get(METRIC_EXTRACTED_TOTAL, Decimal(0))),
                auto_verified_total=int(metrics.get(METRIC_AUTO_VERIFIED_TOTAL, Decimal(0))),
                manual_edited_total=int(metrics.get(METRIC_MANUAL_EDITED_TOTAL, Decimal(0))),
                pipeline_yield_rate=float(metrics.get(METRIC_PIPELINE_YIELD_RATE, Decimal(0))),
                manual_edit_rate=float(metrics.get(METRIC_MANUAL_EDIT_RATE, Decimal(0))),
            )
        )
    return tuple(views)
