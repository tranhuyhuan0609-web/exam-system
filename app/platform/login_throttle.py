"""Brute-force protection for the API login: failed attempts counted per client IP and per normalised e-mail.

Same rule as the HTML login (`app/routers/auth.py`): 10 failures per IP or 5 per e-mail inside a 5-minute window
block further attempts until the oldest failure leaves the window. A success clears the counters of that e-mail and IP.
The counters live in the process: with several API workers each one counts alone (a Redis counter is the next step).
Stdlib only, the clock is injectable so a test needs no sleeping.
"""

from __future__ import annotations

import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

MAX_FAILED_PER_IP = 10
MAX_FAILED_PER_EMAIL = 5
WINDOW_SECONDS = 300.0


def normalise_email(email: str) -> str:
    """NFKC, no whitespace, lower case: the same identity the blind index uses, so variants share one counter."""
    return "".join(unicodedata.normalize("NFKC", email).split()).lower()


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int = 0


class LoginThrottle:
    def __init__(
        self,
        *,
        max_per_ip: int = MAX_FAILED_PER_IP,
        max_per_email: int = MAX_FAILED_PER_EMAIL,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_ip, self._max_email, self._window, self._clock = max_per_ip, max_per_email, window_seconds, clock
        self._by_ip: dict[str, list[float]] = defaultdict(list)
        self._by_email: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _fresh(self, bucket: dict[str, list[float]], key: str, now: float) -> list[float]:
        recent = [t for t in bucket[key] if now - t < self._window]
        if recent:
            bucket[key] = recent
        else:
            bucket.pop(key, None)  # no empty entry stays behind for every address that ever failed once
        return recent

    def check(self, ip: str, email: str) -> ThrottleDecision:
        now, key = self._clock(), normalise_email(email)
        with self._lock:
            waits = []
            for bucket, k, limit in ((self._by_ip, ip, self._max_ip), (self._by_email, key, self._max_email)):
                recent = self._fresh(bucket, k, now)
                if len(recent) >= limit:
                    waits.append(self._window - (now - recent[len(recent) - limit]))
        if not waits:
            return ThrottleDecision(True)
        return ThrottleDecision(False, max(1, int(max(waits)) + 1))

    def record_failure(self, ip: str, email: str) -> None:
        now, key = self._clock(), normalise_email(email)
        with self._lock:
            self._by_ip[ip].append(now)
            self._by_email[key].append(now)

    def clear(self, ip: str, email: str) -> None:
        with self._lock:
            self._by_ip.pop(ip, None)
            self._by_email.pop(normalise_email(email), None)


login_throttle = LoginThrottle()
