"""Pure FSRS-5 spaced repetition scheduler (module D04).

No I/O and no system clock reads; ``reviewed_at`` is always passed explicitly.
Local review dates are computed in ``Asia/Ho_Chi_Minh``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from enum import IntEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEDULER_VERSION = "fsrs-5.0"


def _resolve_vn_tz() -> tzinfo:
    try:
        return ZoneInfo("Asia/Ho_Chi_Minh")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


VN_TZ: tzinfo = _resolve_vn_tz()

# Standard 19 FSRS-5 parameters (W[0] .. W[18]).
FSRS_V5_DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.40255,   # w0: S0(Again)
    1.18385,   # w1: S0(Hard)
    3.17300,   # w2: S0(Good)
    15.69105,  # w3: S0(Easy)
    7.19490,   # w4: D0 base
    0.53450,   # w5: D0 exponent
    1.46040,   # w6: D delta
    0.00460,   # w7: D mean reversion weight
    1.54575,   # w8: Recall stability base
    0.11920,   # w9: Recall stability S power
    1.01925,   # w10: Recall stability R factor
    1.93950,   # w11: Forget stability base
    0.11000,   # w12: Forget stability D power
    0.29605,   # w13: Forget stability S power
    2.26980,   # w14: Forget stability R factor
    0.23150,   # w15: Hard penalty
    2.98980,   # w16: Easy bonus
    0.51655,   # w17: Short-term stability factor
    0.66210,   # w18: Short-term stability offset
)

FSRS_DECAY = -0.5
FSRS_FACTOR = 19.0 / 81.0
MIN_STABILITY = 0.1
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0


class FSRSRating(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


RatingValue = Literal[1, 2, 3, 4]


@dataclass(frozen=True)
class FSRSState:
    """Immutable memory state of a flashcard under FSRS-5."""

    stability: float
    difficulty: float
    reps: int
    lapses: int
    last_review_at: datetime | None = None
    scheduler_version: str = SCHEDULER_VERSION


@dataclass(frozen=True)
class FSRSReviewResult:
    """Deterministic output of a single FSRS-5 review step."""

    state: FSRSState
    interval_days: int
    retrievability: float
    next_review_at: datetime
    next_review_date: date


def _clamp_difficulty(value: float) -> float:
    return min(MAX_DIFFICULTY, max(MIN_DIFFICULTY, value))


def _init_difficulty(rating: int, weights: tuple[float, ...], *, clamp: bool = True) -> float:
    d0 = weights[4] - math.exp(weights[5] * (rating - 1)) + 1.0
    return _clamp_difficulty(d0) if clamp else d0


def _next_difficulty(d: float, rating: int, weights: tuple[float, ...]) -> float:
    delta_d = -weights[6] * (rating - 3)
    d_damped = d + delta_d * ((10.0 - d) / 9.0)
    d_easy_ref = _init_difficulty(FSRSRating.EASY, weights, clamp=False)
    new_d = weights[7] * d_easy_ref + (1.0 - weights[7]) * d_damped
    return _clamp_difficulty(new_d)


def compute_retrievability(elapsed_days: float, stability: float) -> float:
    """Compute recall probability R(t, S) using the FSRS-5 power forgetting curve."""
    if stability <= 0.0:
        return 0.0
    return (1.0 + FSRS_FACTOR * (max(0.0, elapsed_days) / stability)) ** FSRS_DECAY


def next_interval_days(stability: float, request_retention: float = 0.90) -> int:
    """Compute the next review interval in whole days (minimum 1)."""
    if not (0.0 < request_retention < 1.0):
        raise ValueError("request_retention must be strictly between 0 and 1")
    raw_interval = (stability / FSRS_FACTOR) * ((request_retention ** (1.0 / FSRS_DECAY)) - 1.0)
    return max(1, round(raw_interval))


def step_fsrs_v5(
    state: FSRSState | None,
    rating: RatingValue | FSRSRating,
    elapsed_days: float,
    *,
    weights: tuple[float, ...] = FSRS_V5_DEFAULT_WEIGHTS,
    request_retention: float = 0.90,
) -> tuple[FSRSState, int, float]:
    """Advance FSRS-5 state by one review given ``elapsed_days``."""
    if len(weights) != 19:
        raise ValueError(f"FSRS-5 requires 19 weights, got {len(weights)}")
    rating_int = int(rating)
    if rating_int not in (1, 2, 3, 4):
        raise ValueError(f"rating must be 1, 2, 3, or 4; got {rating!r}")

    if state is None or state.reps == 0:
        init_s = max(MIN_STABILITY, weights[rating_int - 1])
        init_d = _init_difficulty(rating_int, weights, clamp=True)
        interval = next_interval_days(init_s, request_retention)
        next_state = FSRSState(
            stability=init_s,
            difficulty=init_d,
            reps=1,
            lapses=1 if rating_int == FSRSRating.AGAIN else 0,
            last_review_at=None,
            scheduler_version=SCHEDULER_VERSION,
        )
        return next_state, interval, 1.0

    prev_d = state.difficulty
    prev_s = state.stability
    new_d = _next_difficulty(prev_d, rating_int, weights)

    if elapsed_days < 1.0:
        factor = math.exp(weights[17] * (rating_int - 3 + weights[18]))
        if rating_int in (FSRSRating.HARD, FSRSRating.GOOD, FSRSRating.EASY):
            factor = max(factor, 1.0)
        new_s = max(MIN_STABILITY, prev_s * factor)
        r = compute_retrievability(elapsed_days, prev_s)
        lapses = state.lapses + (1 if rating_int == FSRSRating.AGAIN else 0)
        interval = next_interval_days(new_s, request_retention)
        next_state = FSRSState(
            stability=new_s,
            difficulty=new_d,
            reps=state.reps + 1,
            lapses=lapses,
            last_review_at=state.last_review_at,
            scheduler_version=SCHEDULER_VERSION,
        )
        return next_state, interval, r

    r = compute_retrievability(elapsed_days, prev_s)

    if rating_int == FSRSRating.AGAIN:
        s_forget = (
            weights[11]
            * (prev_d ** (-weights[12]))
            * (((prev_s + 1.0) ** weights[13]) - 1.0)
            * math.exp(weights[14] * (1.0 - r))
        )
        short_term_cap = prev_s / math.exp(weights[17] * weights[18])
        new_s = max(MIN_STABILITY, min(s_forget, short_term_cap))
        lapses = state.lapses + 1
    else:
        hard_penalty = weights[15] if rating_int == FSRSRating.HARD else 1.0
        easy_bonus = weights[16] if rating_int == FSRSRating.EASY else 1.0
        inc = (
            math.exp(weights[8])
            * (11.0 - prev_d)
            * (prev_s ** (-weights[9]))
            * (math.exp(weights[10] * (1.0 - r)) - 1.0)
            * hard_penalty
            * easy_bonus
        )
        new_s = max(MIN_STABILITY, prev_s * (1.0 + inc))
        lapses = state.lapses

    interval = next_interval_days(new_s, request_retention)
    next_state = FSRSState(
        stability=new_s,
        difficulty=new_d,
        reps=state.reps + 1,
        lapses=lapses,
        last_review_at=state.last_review_at,
        scheduler_version=SCHEDULER_VERSION,
    )
    return next_state, interval, r


def review_card(
    state: FSRSState | None,
    rating: RatingValue | FSRSRating,
    reviewed_at: datetime,
    *,
    weights: tuple[float, ...] = FSRS_V5_DEFAULT_WEIGHTS,
    request_retention: float = 0.90,
) -> FSRSReviewResult:
    """Review a card at an explicit ``reviewed_at`` timestamp and schedule next VN date."""
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware")

    reviewed_vn = reviewed_at.astimezone(VN_TZ)
    if state is None or state.reps == 0 or state.last_review_at is None:
        elapsed_days = 0.0
    else:
        if state.last_review_at.tzinfo is None or state.last_review_at.utcoffset() is None:
            raise ValueError("state.last_review_at must be timezone-aware")
        elapsed_days = max(0.0, (reviewed_at - state.last_review_at).total_seconds() / 86400.0)

    next_state, interval, r = step_fsrs_v5(
        state,
        rating,
        elapsed_days,
        weights=weights,
        request_retention=request_retention,
    )
    final_state = FSRSState(
        stability=next_state.stability,
        difficulty=next_state.difficulty,
        reps=next_state.reps,
        lapses=next_state.lapses,
        last_review_at=reviewed_vn,
        scheduler_version=SCHEDULER_VERSION,
    )
    next_review_at = reviewed_vn + timedelta(days=interval)
    return FSRSReviewResult(
        state=final_state,
        interval_days=interval,
        retrievability=r,
        next_review_at=next_review_at,
        next_review_date=next_review_at.date(),
    )
