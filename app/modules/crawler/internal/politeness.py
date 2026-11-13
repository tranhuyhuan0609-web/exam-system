"""Per-domain politeness rate limiter with injected monotonic clock (module A02).

Never calls `time.sleep`; callers read `wait_seconds` to schedule deferred execution.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MIN_INTERVAL_SEC = 3.0


@dataclass(frozen=True, slots=True)
class PolitenessDecision:
    """Result of checking/acquiring per-domain crawl rate allowance."""

    domain: str
    allowed_now: bool
    wait_seconds: float


class DomainPolitenessLimiter:
    """Tracks per-domain request timestamps using an injected monotonic clock."""

    def __init__(
        self,
        *,
        min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(min_interval_sec) or min_interval_sec < 0:
            raise ValueError(f"min_interval_sec must be finite and >= 0, got {min_interval_sec!r}")
        self._min_interval_sec = min_interval_sec
        self._clock = clock
        self._last_request_ts: dict[str, float] = {}

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        clean = domain.strip().lower().rstrip(".")
        if not clean:
            raise ValueError("domain must not be empty")
        return clean

    def wait_seconds_for(self, domain: str) -> float:
        """Compute seconds remaining before `domain` can be crawled again without advancing state."""
        key = self._normalize_domain(domain)
        last = self._last_request_ts.get(key)
        if last is None:
            return 0.0
        elapsed = self._clock() - last
        if elapsed >= self._min_interval_sec:
            return 0.0
        return max(0.0, self._min_interval_sec - elapsed)

    def try_acquire(self, domain: str) -> PolitenessDecision:
        """Reserve a crawl slot for `domain` if elapsed time >= min_interval_sec."""
        key = self._normalize_domain(domain)
        now = self._clock()
        last = self._last_request_ts.get(key)
        if last is None or (now - last) >= self._min_interval_sec:
            self._last_request_ts[key] = now
            return PolitenessDecision(domain=key, allowed_now=True, wait_seconds=0.0)

        remaining = max(0.0, self._min_interval_sec - (now - last))
        return PolitenessDecision(domain=key, allowed_now=False, wait_seconds=remaining)
