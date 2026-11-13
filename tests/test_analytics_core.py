"""Tests for G04 analytics rollups."""

from __future__ import annotations

from datetime import date

from app.modules.analytics.public import (
    AnalyticsEvent,
    AnalyticsEventType,
    compute_daily_metric_rollup,
)


def test_g04_daily_rollup_computes_wlau_pipeline_yield_and_mastery_gain() -> None:
    d0 = date(2026, 11, 13)
    events = (
        # WLAU inside [d0 - 6d, d0]
        AnalyticsEvent("e1", "t1", date(2026, 11, 7), AnalyticsEventType.PRACTICE_COMPLETED, learner_id="s1"),
        AnalyticsEvent("e2", "t1", date(2026, 11, 10), AnalyticsEventType.EXAM_SUBMITTED, learner_id="s2"),
        AnalyticsEvent("e3", "t1", d0, AnalyticsEventType.FLASHCARD_REVIEWED, learner_id="s2"),
        AnalyticsEvent("e4", "t1", d0, AnalyticsEventType.ERROR_REMEDIATED, learner_id="s3"),
        # Outside 7-day window -> excluded from WLAU
        AnalyticsEvent("e5", "t1", date(2026, 11, 6), AnalyticsEventType.PRACTICE_COMPLETED, learner_id="s99"),
        # Cross-tenant ignored
        AnalyticsEvent("e6", "t2", d0, AnalyticsEventType.PRACTICE_COMPLETED, learner_id="other"),
        # Pipeline yield & edit rate on d0
        AnalyticsEvent("e7", "t1", d0, AnalyticsEventType.PIPELINE_EXTRACTED, count=100),
        AnalyticsEvent("e8", "t1", d0, AnalyticsEventType.PIPELINE_AUTO_VERIFIED, count=82),
        AnalyticsEvent("e9", "t1", d0, AnalyticsEventType.PIPELINE_MANUAL_EDITED, count=18),
        # Mastery gain on d0
        AnalyticsEvent("e10", "t1", d0, AnalyticsEventType.MASTERY_DELTA, pre_mastery=0.40, post_mastery=0.65),
        AnalyticsEvent("e11", "t1", d0, AnalyticsEventType.MASTERY_DELTA, pre_mastery=0.50, post_mastery=0.65),
    )

    rollup = compute_daily_metric_rollup(tenant_id="t1", metric_date=d0, events=events)
    assert rollup.dau_count == 2  # s2, s3
    assert rollup.wlau_count == 3  # s1, s2, s3
    assert rollup.pipeline_yield_rate == 0.82
    assert rollup.manual_edit_rate == 0.18
    assert rollup.avg_mastery_gain == 0.2
