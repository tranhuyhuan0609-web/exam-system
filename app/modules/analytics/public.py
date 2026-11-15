"""Public interface for module analytics (G04)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from app.modules.analytics.internal.rollups import (
    AnalyticsEvent,
    AnalyticsEventType,
    DailyMetricRollup,
    compute_daily_metric_rollup,
)

if TYPE_CHECKING:
    from app.modules.analytics.internal.models import AnalyticsEventRecord, DailyMetricRollupRecord
    from app.modules.analytics.internal.service import (
        AnalyticsEventView,
        AnalyticsValidationError,
        PipelineYieldDayView,
        PlatformKpisView,
        get_pipeline_yield,
        get_platform_kpis,
        record_analytics_event,
        run_daily_metric_rollup,
    )
    from app.modules.analytics.router import router

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "AnalyticsEventRecord": ("app.modules.analytics.internal.models", "AnalyticsEventRecord"),
    "DailyMetricRollupRecord": ("app.modules.analytics.internal.models", "DailyMetricRollupRecord"),
    "AnalyticsEventView": ("app.modules.analytics.internal.service", "AnalyticsEventView"),
    "AnalyticsValidationError": ("app.modules.analytics.internal.service", "AnalyticsValidationError"),
    "PipelineYieldDayView": ("app.modules.analytics.internal.service", "PipelineYieldDayView"),
    "PlatformKpisView": ("app.modules.analytics.internal.service", "PlatformKpisView"),
    "get_pipeline_yield": ("app.modules.analytics.internal.service", "get_pipeline_yield"),
    "get_platform_kpis": ("app.modules.analytics.internal.service", "get_platform_kpis"),
    "record_analytics_event": ("app.modules.analytics.internal.service", "record_analytics_event"),
    "run_daily_metric_rollup": ("app.modules.analytics.internal.service", "run_daily_metric_rollup"),
    "router": ("app.modules.analytics.router", "router"),
}

__all__ = [
    "AnalyticsEvent",
    "AnalyticsEventRecord",
    "AnalyticsEventType",
    "AnalyticsEventView",
    "AnalyticsValidationError",
    "DailyMetricRollup",
    "DailyMetricRollupRecord",
    "PipelineYieldDayView",
    "PlatformKpisView",
    "compute_daily_metric_rollup",
    "get_pipeline_yield",
    "get_platform_kpis",
    "record_analytics_event",
    "router",
    "run_daily_metric_rollup",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr_name = target
    value = getattr(import_module(module_path), attr_name)
    globals()[name] = value
    return value
