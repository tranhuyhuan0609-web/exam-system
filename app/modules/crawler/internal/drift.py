"""Sliding-window parser empty-yield drift monitor (module A02)."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

DEFAULT_DRIFT_WINDOW_SIZE = 30
DEFAULT_DRIFT_MIN_SAMPLES = 10
DEFAULT_DRIFT_MAX_EMPTY_RATE = 0.15


@dataclass(frozen=True, slots=True)
class DriftStatus:
    """Snapshot produced after recording one crawled page extraction yield."""

    is_drifted: bool
    empty_rate: float
    sample_count: int
    empty_count: int
    reason_code: str | None


class ParserDriftMonitor:
    """Monitors recent extraction counts over a fixed sliding window."""

    def __init__(
        self,
        *,
        window_size: int = DEFAULT_DRIFT_WINDOW_SIZE,
        min_samples: int = DEFAULT_DRIFT_MIN_SAMPLES,
        max_empty_rate: float = DEFAULT_DRIFT_MAX_EMPTY_RATE,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if not math.isfinite(max_empty_rate) or not (0.0 <= max_empty_rate <= 1.0):
            raise ValueError("max_empty_rate must be finite and within [0.0, 1.0]")

        self._window_size = window_size
        self._min_samples = min_samples
        self._max_empty_rate = max_empty_rate
        self._recent_empty_flags: deque[int] = deque(maxlen=window_size)

    @property
    def window_size(self) -> int:
        return self._window_size

    @property
    def min_samples(self) -> int:
        return self._min_samples

    @property
    def max_empty_rate(self) -> float:
        return self._max_empty_rate

    def record_page_yield(self, extracted_count: int) -> DriftStatus:
        """Record extracted item count for one page and evaluate parser drift."""
        if extracted_count < 0:
            raise ValueError("extracted_count must be non-negative")

        self._recent_empty_flags.append(1 if extracted_count == 0 else 0)
        samples = len(self._recent_empty_flags)
        empty_count = sum(self._recent_empty_flags)

        if samples < self._min_samples:
            return DriftStatus(
                is_drifted=False,
                empty_rate=0.0,
                sample_count=samples,
                empty_count=empty_count,
                reason_code="INSUFFICIENT_SAMPLES",
            )

        empty_rate = round(empty_count / samples, 3)
        is_drifted = (empty_count / samples) > self._max_empty_rate
        return DriftStatus(
            is_drifted=is_drifted,
            empty_rate=empty_rate,
            sample_count=samples,
            empty_count=empty_count,
            reason_code="PARSER_DRIFT_DETECTED" if is_drifted else None,
        )
