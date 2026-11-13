"""Provider API key ring with O(1) rotation, cooldown tracking, 401 eviction, and strict secret redaction."""

from __future__ import annotations

import heapq
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class KeyStatus(StrEnum):
    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class ApiKeyInput:
    """Initial key descriptor supplied when constructing `ProviderKeyRing`."""

    key_id: str
    secret: str = field(repr=False)

    def __repr__(self) -> str:
        return f"ApiKeyInput(key_id={self.key_id!r}, secret='[REDACTED]')"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(slots=True)
class _KeySlot:
    key_id: str
    _secret: str
    status: KeyStatus = KeyStatus.HEALTHY
    cooldown_until: float = 0.0

    def __repr__(self) -> str:
        return f"KeySlot(key_id={self.key_id!r}, status={self.status.value!r})"

    def __str__(self) -> str:
        return self.__repr__()


class KeyAcquisitionResult:
    """
    Result of asking `ProviderKeyRing` for an API key.

    Guarantees raw secret values NEVER appear in `repr()` or `str()`.
    """

    __slots__ = ("acquired", "key_id", "wait_seconds", "exhausted", "_secret_getter")

    def __init__(
        self,
        *,
        acquired: bool,
        key_id: str | None = None,
        wait_seconds: float | None = None,
        exhausted: bool = False,
        secret_getter: Callable[[], str] | None = None,
    ) -> None:
        self.acquired = acquired
        self.key_id = key_id
        self.wait_seconds = wait_seconds
        self.exhausted = exhausted
        self._secret_getter = secret_getter

    def get_secret(self) -> str:
        """Retrieve the raw API secret only when actually signing an outbound request."""
        if not self.acquired or self._secret_getter is None:
            raise LookupError(f"No key secret available (key_id={self.key_id!r}, acquired={self.acquired})")
        return self._secret_getter()

    def __repr__(self) -> str:
        return (
            f"KeyAcquisitionResult(acquired={self.acquired!r}, key_id={self.key_id!r}, "
            f"wait_seconds={self.wait_seconds!r}, exhausted={self.exhausted!r}, secret='[REDACTED]')"
        )

    def __str__(self) -> str:
        return self.__repr__()


class ProviderKeyRing:
    """
    O(1) rotating key pool with cooldown heap and permanent eviction on auth errors (401).

    Never calls `time.sleep`; when all non-disabled keys are in cooldown, `acquire()`
    returns `KeyAcquisitionResult(acquired=False, wait_seconds=<float>)`.
    """

    def __init__(
        self,
        keys: Sequence[ApiKeyInput],
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not keys:
            raise ValueError("ProviderKeyRing requires at least one key")
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._slots: dict[str, _KeySlot] = {}
        self._ready: deque[str] = deque()
        # Key ids currently in `_ready`: a key leaves the deque lazily, so it must not be queued twice.
        self._queued: set[str] = set()
        self._cooldown_heap: list[tuple[float, str]] = []

        for item in keys:
            if not item.key_id or not item.secret:
                raise ValueError("key_id and secret must be non-empty")
            if item.key_id in self._slots:
                raise ValueError(f"Duplicate key_id: {item.key_id!r}")
            slot = _KeySlot(key_id=item.key_id, _secret=item.secret)
            self._slots[item.key_id] = slot
            self._ready.append(item.key_id)
            self._queued.add(item.key_id)

    def __repr__(self) -> str:
        with self._lock:
            statuses = {k: s.status.value for k, s in self._slots.items()}
        return f"ProviderKeyRing(keys={statuses!r})"

    def __str__(self) -> str:
        return self.__repr__()

    def _wake_expired_cooldowns(self, now: float) -> None:
        while self._cooldown_heap and self._cooldown_heap[0][0] <= now:
            until, key_id = heapq.heappop(self._cooldown_heap)
            slot = self._slots.get(key_id)
            if slot is None or slot.status != KeyStatus.COOLDOWN:
                continue
            # Ignore stale heap entries superseded by a newer cooldown timestamp.
            if until < slot.cooldown_until:
                continue
            slot.status = KeyStatus.HEALTHY
            slot.cooldown_until = 0.0
            if key_id not in self._queued:
                self._ready.append(key_id)
                self._queued.add(key_id)

    def acquire(self) -> KeyAcquisitionResult:
        """
        Return the next healthy key in O(1) round-robin order.

        If all non-disabled keys are in cooldown, returns `acquired=False` with `wait_seconds`.
        If all keys are permanently disabled (e.g. 401), returns `acquired=False, exhausted=True`.
        """
        with self._lock:
            now = self._clock()
            self._wake_expired_cooldowns(now)

            while self._ready:
                key_id = self._ready.popleft()
                slot = self._slots[key_id]
                if slot.status == KeyStatus.HEALTHY:
                    # Round-robin rotation: push back to tail in O(1)
                    self._ready.append(key_id)
                    secret_value = slot._secret
                    return KeyAcquisitionResult(
                        acquired=True,
                        key_id=key_id,
                        secret_getter=lambda s=secret_value: s,
                    )
                self._queued.discard(key_id)

            # No healthy keys available right now; inspect active cooldown heap.
            while self._cooldown_heap:
                until, key_id = self._cooldown_heap[0]
                slot = self._slots[key_id]
                if slot.status != KeyStatus.COOLDOWN or until < slot.cooldown_until:
                    heapq.heappop(self._cooldown_heap)
                    continue
                wait = max(0.0, until - now)
                return KeyAcquisitionResult(
                    acquired=False,
                    wait_seconds=wait,
                    exhausted=False,
                )

            return KeyAcquisitionResult(
                acquired=False,
                wait_seconds=None,
                exhausted=True,
            )

    def mark_rate_limited(self, key_id: str, *, cooldown_seconds: float) -> None:
        """Put a key (429 response) into cooldown until `now + cooldown_seconds`."""
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        with self._lock:
            slot = self._slots.get(key_id)
            if slot is None:
                raise KeyError(f"Unknown key_id: {key_id!r}")
            if slot.status == KeyStatus.DISABLED:
                return
            until = self._clock() + cooldown_seconds
            slot.status = KeyStatus.COOLDOWN
            slot.cooldown_until = until
            heapq.heappush(self._cooldown_heap, (until, key_id))

    def mark_permanently_invalid(self, key_id: str, *, status_code: int = 401) -> None:
        """Permanently disable a key (e.g. HTTP 401 unauthorized)."""
        if status_code not in (401, 403):
            raise ValueError(f"Expected permanent auth status code 401/403, got {status_code}")
        with self._lock:
            slot = self._slots.get(key_id)
            if slot is None:
                raise KeyError(f"Unknown key_id: {key_id!r}")
            slot.status = KeyStatus.DISABLED
            slot.cooldown_until = 0.0

    def status_of(self, key_id: str) -> KeyStatus:
        with self._lock:
            now = self._clock()
            self._wake_expired_cooldowns(now)
            return self._slots[key_id].status

    def healthy_count(self) -> int:
        with self._lock:
            self._wake_expired_cooldowns(self._clock())
            return sum(1 for s in self._slots.values() if s.status == KeyStatus.HEALTHY)
