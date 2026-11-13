"""Tests for G04 analytics rollups."""

from __future__ import annotations

from datetime import date, datetime

import pytest

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


T1 = "t1"
D0 = date(2026, 11, 13)


def _rollup(events, tenant="t1", metric_date=D0):
    return compute_daily_metric_rollup(tenant_id=tenant, metric_date=metric_date, events=tuple(events))


def test_replayed_event_id_is_counted_once() -> None:
    # Regression: event_id was never used, so a replayed event double-counted.
    ev = AnalyticsEvent("e1", T1, D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=100)
    ok = AnalyticsEvent("e2", T1, D0, AnalyticsEventType.PIPELINE_AUTO_VERIFIED, count=50)
    rollup = _rollup([ev, ev, ok, ok, ev])
    assert rollup.extracted_total == 100
    assert rollup.auto_verified_total == 50
    assert rollup.pipeline_yield_rate == 0.5


def test_replayed_mastery_and_learner_events_do_not_skew_averages() -> None:
    d1 = AnalyticsEvent("m1", T1, D0, AnalyticsEventType.MASTERY_DELTA, pre_mastery=0.2, post_mastery=0.6)
    d2 = AnalyticsEvent("m2", T1, D0, AnalyticsEventType.MASTERY_DELTA, pre_mastery=0.5, post_mastery=0.6)
    assert _rollup([d1, d1, d1, d2]).avg_mastery_gain == 0.25


def test_same_event_id_in_another_tenant_is_not_a_replay() -> None:
    a = AnalyticsEvent("e1", "t1", D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=10)
    b = AnalyticsEvent("e1", "t2", D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=99)
    assert _rollup([a, b], tenant="t1").extracted_total == 10
    assert _rollup([a, b], tenant="t2").extracted_total == 99


def test_datetime_event_date_is_reduced_to_its_date() -> None:
    # Regression: a datetime event_date raised TypeError when compared with a date.
    ev = AnalyticsEvent(
        "e1", T1, datetime(2026, 11, 13, 23, 59), AnalyticsEventType.PRACTICE_COMPLETED, learner_id="s1"
    )
    rollup = _rollup([ev], metric_date=datetime(2026, 11, 13, 0, 1))
    assert rollup.dau_count == 1 and rollup.wlau_count == 1


def test_invalid_event_date_type_fails_closed() -> None:
    ev = AnalyticsEvent("e1", T1, "2026-11-13", AnalyticsEventType.PRACTICE_COMPLETED, learner_id="s1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _rollup([ev])


@pytest.mark.parametrize("tenant", ["", "   "])
def test_empty_tenant_is_rejected(tenant: str) -> None:
    with pytest.raises(ValueError):
        _rollup([], tenant=tenant)


def test_empty_event_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        _rollup([AnalyticsEvent("", T1, D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=1)])


def test_no_events_gives_zero_rates_without_division_errors() -> None:
    rollup = _rollup([])
    assert (rollup.dau_count, rollup.pipeline_yield_rate, rollup.manual_edit_rate, rollup.avg_mastery_gain) == (
        0, 0.0, 0.0, 0.0,
    )


def test_wlau_window_boundaries_are_inclusive_of_six_days_back() -> None:
    inside = AnalyticsEvent("a", T1, date(2026, 11, 7), AnalyticsEventType.EXAM_SUBMITTED, learner_id="s1")
    outside = AnalyticsEvent("b", T1, date(2026, 11, 6), AnalyticsEventType.EXAM_SUBMITTED, learner_id="s2")
    future = AnalyticsEvent("c", T1, date(2026, 11, 14), AnalyticsEventType.EXAM_SUBMITTED, learner_id="s3")
    rollup = _rollup([inside, outside, future])
    assert rollup.wlau_count == 1 and rollup.dau_count == 0


def test_negative_counts_are_floored_and_non_integer_counts_rejected() -> None:
    neg = AnalyticsEvent("a", T1, D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=-5)
    assert _rollup([neg]).extracted_total == 0
    bad = AnalyticsEvent("b", T1, D0, AnalyticsEventType.PIPELINE_EXTRACTED, count=float("nan"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _rollup([bad])


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_mastery_is_rejected(value: float) -> None:
    ev = AnalyticsEvent("a", T1, D0, AnalyticsEventType.MASTERY_DELTA, pre_mastery=0.1, post_mastery=value)
    with pytest.raises(ValueError):
        _rollup([ev])


def test_events_without_learner_do_not_count_as_active_users() -> None:
    ev = AnalyticsEvent("a", T1, D0, AnalyticsEventType.PRACTICE_COMPLETED, learner_id=None)
    assert _rollup([ev]).dau_count == 0
