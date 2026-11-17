"""Brute-force protection for the API login: failed attempts counted per client IP and per normalised e-mail.

Same rule as the HTML login (`app/routers/auth.py`): 10 failures per IP or 5 per e-mail inside a 5-minute window
block further attempts until the oldest failure leaves the window. A success clears the counter of that e-mail
only: the address is shared (a school, a NAT), so one valid sign-in must not wipe the failures made from it.
`LoginThrottle` keeps the counters in the process (dev, tests, one worker). With several API workers use
`RedisLoginThrottle` (`LOGIN_THROTTLE_BACKEND=redis`): the counters are sorted sets in Redis, so every worker sees every
failure; `get_login_throttle()` picks one from the settings. If Redis cannot be reached a worker counts alone in its own
memory for that call (degraded, never fail-open) and tries Redis again after a short pause. The clock is injectable
so a test needs no sleeping.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

log = logging.getLogger(__name__)

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

    def clear_email(self, email: str) -> None:
        """After a successful sign-in: forget the failures of that e-mail; the per-address count is left alone."""
        with self._lock:
            self._by_email.pop(normalise_email(email), None)


login_throttle = LoginThrottle()


class AsyncLoginThrottle(Protocol):
    """What the sign-in route needs, whichever store holds the counters."""

    async def check(self, ip: str, email: str) -> ThrottleDecision: ...

    async def record_failure(self, ip: str, email: str) -> None: ...

    async def clear_email(self, email: str) -> None: ...


class MemoryLoginThrottle:
    """`LoginThrottle` behind the async interface."""

    def __init__(self, throttle: LoginThrottle) -> None:
        self._throttle = throttle

    async def check(self, ip: str, email: str) -> ThrottleDecision:
        return self._throttle.check(ip, email)

    async def record_failure(self, ip: str, email: str) -> None:
        self._throttle.record_failure(ip, email)

    async def clear_email(self, email: str) -> None:
        self._throttle.clear_email(email)


# A sorted set never needs more than the failures that can still block, with room for a burst of parallel attempts.
_MAX_SET_ENTRIES = 100


class RedisLoginThrottle:
    """The same rule as `LoginThrottle`, with the failures of every worker in Redis (one sorted set per IP / e-mail).

    Members are unique ids scored by the wall-clock time of the failure (monotonic clocks differ per process). The
    e-mail is stored only as a hash of its normalised form. Each key expires with the window, so idle addresses leave
    no trace. A Redis error never blocks or admits by itself: the call is answered from `fallback` (this worker's
    memory) and Redis is left alone for `retry_after_error_seconds`.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        fallback: AsyncLoginThrottle,
        max_per_ip: int = MAX_FAILED_PER_IP,
        max_per_email: int = MAX_FAILED_PER_EMAIL,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.time,
        key_prefix: str = "ulp:login",
        retry_after_error_seconds: float = 5.0,
        client: Any = None,
    ) -> None:
        self._url, self._fallback = redis_url, fallback
        self._max_ip, self._max_email, self._window, self._clock = max_per_ip, max_per_email, window_seconds, clock
        self._prefix, self._pause = key_prefix, retry_after_error_seconds
        self._injected = client
        self._client: Any = client
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._paused_until = 0.0

    def _redis(self) -> Any:
        """One client per event loop: an asyncio connection cannot be used from another loop."""
        if self._injected is not None:
            return self._injected
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop:
            from redis.asyncio import Redis

            self._client = Redis.from_url(
                self._url, socket_timeout=0.25, socket_connect_timeout=0.25, decode_responses=True
            )
            self._client_loop = loop
        return self._client

    def _email_key(self, email: str) -> str:
        return f"{self._prefix}:email:{hashlib.sha256(normalise_email(email).encode()).hexdigest()}"

    def _keys(self, ip: str, email: str) -> tuple[tuple[str, int], tuple[str, int]]:
        return (f"{self._prefix}:ip:{ip}", self._max_ip), (self._email_key(email), self._max_email)

    def _available(self) -> bool:
        return self._clock() >= self._paused_until

    def _failed(self, exc: Exception) -> None:
        self._paused_until = self._clock() + self._pause
        log.warning("login throttle: Redis unavailable (%s), counting in this worker's memory", type(exc).__name__)

    async def check(self, ip: str, email: str) -> ThrottleDecision:
        if not self._available():
            return await self._fallback.check(ip, email)
        now = self._clock()
        try:
            r = self._redis()
            waits = []
            for key, limit in self._keys(ip, email):
                async with r.pipeline(transaction=False) as pipe:
                    pipe.zremrangebyscore(key, "-inf", now - self._window)
                    pipe.zrange(key, 0, -1, withscores=True)
                    _, recent = await pipe.execute()
                if len(recent) >= limit:
                    oldest_that_counts = recent[len(recent) - limit][1]
                    waits.append(self._window - (now - oldest_that_counts))
        except Exception as exc:  # noqa: BLE001  (redis.RedisError, OSError, TimeoutError, bad URL ...)
            self._failed(exc)
            return await self._fallback.check(ip, email)
        if not waits:
            return ThrottleDecision(True)
        return ThrottleDecision(False, max(1, int(max(waits)) + 1))

    async def record_failure(self, ip: str, email: str) -> None:
        # Always keep this worker's own count too: it is what protects the sign-in while Redis is away.
        await self._fallback.record_failure(ip, email)
        if not self._available():
            return
        now = self._clock()
        try:
            r = self._redis()
            async with r.pipeline(transaction=True) as pipe:
                for key, _ in self._keys(ip, email):
                    pipe.zadd(key, {uuid.uuid4().hex: now})
                    pipe.zremrangebyscore(key, "-inf", now - self._window)
                    pipe.zremrangebyrank(key, 0, -(_MAX_SET_ENTRIES + 1))
                    pipe.expire(key, int(self._window) + 1)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)

    async def clear_email(self, email: str) -> None:
        await self._fallback.clear_email(email)
        if not self._available():
            return
        try:
            await self._redis().delete(self._email_key(email))
        except Exception as exc:  # noqa: BLE001
            self._failed(exc)


_memory_backend = MemoryLoginThrottle(login_throttle)
_backend_cache: dict[tuple[str, str], AsyncLoginThrottle] = {}


def get_login_throttle() -> AsyncLoginThrottle:
    """The store the settings ask for (cached); in-memory unless `LOGIN_THROTTLE_BACKEND=redis`."""
    from app.config import get_settings

    settings = get_settings()
    if settings.login_throttle_backend.lower() != "redis":
        return _memory_backend
    key = ("redis", settings.redis_url)
    if key not in _backend_cache:
        _backend_cache[key] = RedisLoginThrottle(settings.redis_url, fallback=_memory_backend)
    return _backend_cache[key]
