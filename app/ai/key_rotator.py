"""Gemini API Key Rotator & Pool Management.

Supports rotating multiple Gemini API keys with:
1. Round-Robin load distribution across multiple API keys.
2. Automatic Failover to the next key when a key hits 429 / RESOURCE_EXHAUSTED / quota limit.
3. Cooldown & Quota Tracking per key so exhausted keys are temporarily parked, allowing remaining keys to work.
4. Key masking for secure logging (e.g. AIzaSy...7X).
"""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)


def mask_key(key: str) -> str:
    """Mask key for safe logging, e.g. 'AIzaSyAB...1234'."""
    if not key:
        return "<empty>"
    if len(key) <= 12:
        return f"{key[:3]}...{key[-2:]}"
    return f"{key[:8]}...{key[-4:]}"


def parse_keys(raw: str | list[str] | None) -> list[str]:
    """Parse string or list of strings into clean, unique, non-empty API keys."""
    if not raw:
        return []
    if isinstance(raw, list):
        items: list[str] = []
        for x in raw:
            items.extend(parse_keys(x))
        return list(dict.fromkeys(items))

    # Split by comma, semicolon, space, or newline
    tokens = [k.strip() for k in re.split(r"[,;\s\n]+", str(raw)) if k.strip()]
    return list(dict.fromkeys(tokens))


class GeminiKeyPool:
    """Manages a pool of Gemini API keys, providing round-robin selection and quota failover."""

    def __init__(self, api_keys: str | list[str]):
        self._keys = parse_keys(api_keys)
        self._clients: dict[str, Any] = {}
        self._exhausted_until: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._index = 0

    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    def is_empty(self) -> bool:
        return len(self._keys) == 0

    def get_client(self, api_key: str):
        """Get or initialize a cached genai.Client for a specific API key (with 1 attempt so key rotation is instant)."""
        from google import genai
        from google.genai import types

        if api_key not in self._clients:
            self._clients[api_key] = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=45_000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._clients[api_key]

    async def get_active_client(self) -> tuple[Any, str]:
        """Return the next available (client, api_key) using round-robin.

        If all keys are currently cooling down, returns the key that will become
        available earliest.
        """
        if not self._keys:
            raise RuntimeError("GeminiKeyPool: Không có API key nào được cấu hình.")

        async with self._lock:
            now = datetime.now(UTC)
            available = [k for k in self._keys if k not in self._exhausted_until or self._exhausted_until[k] <= now]

            if available:
                self._index = (self._index + 1) % len(available)
                chosen = available[self._index]
            else:
                # All keys in cooldown: find the one that recovers earliest
                soonest_key = min(self._exhausted_until, key=lambda k: self._exhausted_until[k])
                soonest_time = self._exhausted_until[soonest_key]
                log.warning(
                    "Tất cả %d Gemini API keys đều đang trong thời gian chờ (hết quota). Key sớm nhất (%s) sẽ phục hồi lúc %s",
                    len(self._keys),
                    mask_key(soonest_key),
                    soonest_time.strftime("%H:%M:%S UTC"),
                )
                chosen = soonest_key

            return self.get_client(chosen), chosen

    async def get_all_available_keys(self) -> list[str]:
        """Return all keys not currently marked as exhausted."""
        async with self._lock:
            now = datetime.now(UTC)
            return [k for k in self._keys if k not in self._exhausted_until or self._exhausted_until[k] <= now]

    async def get_rotated_keys(self) -> list[str]:
        """Return available keys starting from the next round-robin position."""
        async with self._lock:
            now = datetime.now(UTC)
            available = [k for k in self._keys if k not in self._exhausted_until or self._exhausted_until[k] <= now]
            if not available:
                available = list(self._keys)
            if not available:
                return []
            self._index = (self._index + 1) % len(available)
            return available[self._index :] + available[: self._index]

    async def mark_exhausted(self, api_key: str, cooldown_minutes: int = 60, reason: str = "quota exceeded"):
        """Mark an API key as exhausted and put it on cooldown."""
        async with self._lock:
            until = datetime.now(UTC) + timedelta(minutes=cooldown_minutes)
            self._exhausted_until[api_key] = until
            log.warning(
                "Gemini key [%s] chạm giới hạn (%s) -> Tạm dừng key này %d phút (đến %s). Tự động chuyển key tiếp theo!",
                mask_key(api_key),
                reason,
                cooldown_minutes,
                until.strftime("%H:%M:%S UTC"),
            )

    async def mark_recovered(self, api_key: str):
        """Remove a key from cooldown early if it responded successfully."""
        async with self._lock:
            if api_key in self._exhausted_until:
                del self._exhausted_until[api_key]
                log.info("Gemini key [%s] đã hoạt động lại bình thường.", mask_key(api_key))

    def status_summary(self) -> dict[str, Any]:
        """Summary of active vs cooling-down keys."""
        now = datetime.now(UTC)
        available = [mask_key(k) for k in self._keys if k not in self._exhausted_until or self._exhausted_until[k] <= now]
        cooldown = {
            mask_key(k): self._exhausted_until[k].strftime("%H:%M:%S UTC")
            for k in self._keys
            if k in self._exhausted_until and self._exhausted_until[k] > now
        }
        return {
            "total": len(self._keys),
            "available_count": len(available),
            "available": available,
            "cooldown_count": len(cooldown),
            "cooldown": cooldown,
        }

