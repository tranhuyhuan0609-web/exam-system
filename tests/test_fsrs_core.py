"""Unit tests for D04 FSRS-5 spaced repetition core (pure, no conftest)."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import fsrs
import pytest

from app.modules.flashcards.public import (
    FSRS_V5_DEFAULT_WEIGHTS,
    SCHEDULER_VERSION,
    VN_TZ,
    FSRSRating,
    FSRSState,
    compute_retrievability,
    review_card,
)

_REF_RATING_MAP = {
    1: fsrs.Rating.Again,
    2: fsrs.Rating.Hard,
    3: fsrs.Rating.Good,
    4: fsrs.Rating.Easy,
}


def _make_reference_scheduler() -> fsrs.Scheduler:
    # py-fsrs v6 uses 21 weights; w19=0.0 and w20=0.5 reduce it to FSRS-5 math.
    return fsrs.Scheduler(
        parameters=FSRS_V5_DEFAULT_WEIGHTS + (0.0, 0.5),
        desired_retention=0.90,
        learning_steps=(),
        relearning_steps=(),
        enable_fuzzing=False,
    )


@pytest.mark.parametrize("seed_idx", range(220))
def test_matches_reference_py_fsrs_on_random_sequences(seed_idx: int) -> None:
    rng = random.Random(20261113 + seed_idx)
    ref_scheduler = _make_reference_scheduler()
    ref_card = fsrs.Card()
    state: FSRSState | None = None

    now = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    steps = rng.randint(3, 12)

    for step in range(steps):
        if step > 0:
            # Mix same-day (0d), on-time, and late reviews.
            delta_days = rng.choice([0, 1, 2, 3, 5, 7, 14, 30, 60])
            now = now + timedelta(days=delta_days)

        rating = rng.choice((1, 2, 3, 4))
        result = review_card(state, rating, now)
        state = result.state

        ref_card, _ = ref_scheduler.review_card(ref_card, _REF_RATING_MAP[rating], now)
        ref_interval = (ref_card.due - now).days

        assert state.scheduler_version == SCHEDULER_VERSION
        assert state.stability == pytest.approx(ref_card.stability, rel=1e-9, abs=1e-9)
        assert state.difficulty == pytest.approx(ref_card.difficulty, rel=1e-9, abs=1e-9)
        assert result.interval_days == ref_interval


def test_vietnam_timezone_next_review_date() -> None:
    # 2026-11-13 20:00 UTC is already 2026-11-14 03:00 in Asia/Ho_Chi_Minh (+07:00).
    reviewed_utc = datetime(2026, 11, 13, 20, 0, 0, tzinfo=UTC)
    result = review_card(None, FSRSRating.AGAIN, reviewed_utc)

    assert result.interval_days == 1
    assert result.next_review_at.tzinfo == VN_TZ
    assert result.next_review_date == date(2026, 11, 15)


def test_determinism_and_naive_datetime_rejected() -> None:
    t = datetime(2026, 5, 10, 14, 30, tzinfo=VN_TZ)
    r1 = review_card(None, FSRSRating.GOOD, t)
    r2 = review_card(None, FSRSRating.GOOD, t)
    assert r1 == r2

    with pytest.raises(ValueError, match="timezone-aware"):
        review_card(None, FSRSRating.GOOD, datetime(2026, 5, 10, 14, 30))


def test_edge_cases_new_card_repeated_lapses_and_very_late_review() -> None:
    t = datetime(2026, 1, 1, 8, 0, tzinfo=VN_TZ)

    # New card across all 4 ratings: stability strictly increases with rating.
    s_by_rating = [review_card(None, r, t).state.stability for r in (1, 2, 3, 4)]
    assert s_by_rating == sorted(s_by_rating)
    assert len(set(s_by_rating)) == 4

    # Repeated Again: lapses increment, stability stays >= 0.1, difficulty capped <= 10.0.
    state: FSRSState | None = None
    for i in range(6):
        res = review_card(state, FSRSRating.AGAIN, t + timedelta(days=i))
        state = res.state
    assert state is not None
    assert state.reps == 6
    assert state.lapses == 6
    assert state.stability >= 0.1
    assert 1.0 <= state.difficulty <= 10.0

    # Very late review (365 days later) with Good.
    late_res = review_card(state, FSRSRating.GOOD, t + timedelta(days=370))
    assert late_res.retrievability < 0.2
    assert late_res.state.stability > state.stability
    assert late_res.interval_days >= 1


def test_compute_retrievability_boundary() -> None:
    assert compute_retrievability(0.0, 0.0) == 0.0
    assert compute_retrievability(0.0, 10.0) == pytest.approx(1.0)
    assert compute_retrievability(10.0, 10.0) == pytest.approx(0.90, abs=1e-9)
