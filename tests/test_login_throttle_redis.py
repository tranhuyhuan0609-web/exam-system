"""RedisLoginThrottle: one count for every API worker, with a safe fallback when Redis is away.

A small in-memory stand-in for the five Redis commands used runs everywhere; the same scenarios also run against the
real Redis of docker-compose when it answers (skipped otherwise).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

from app.platform.login_throttle import (
    LoginThrottle,
    MemoryLoginThrottle,
    RedisLoginThrottle,
    normalise_email,
)

WINDOW = 300.0


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeRedis:
    """Sorted sets only: zadd, zremrangebyscore, zremrangebyrank, zrange(withscores), expire, delete."""

    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}
        self.ttl: dict[str, int] = {}
        self.calls = 0

    def pipeline(self, transaction: bool = True):
        return _Pipe(self)

    async def delete(self, *keys: str) -> int:
        self.calls += 1
        return sum(1 for k in keys if self.sets.pop(k, None) is not None)


class _Pipe:
    def __init__(self, redis: FakeRedis) -> None:
        self._r, self._ops = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def zadd(self, key, mapping):
        self._ops.append(lambda: self._r.sets.setdefault(key, {}).update(mapping))

    def zremrangebyscore(self, key, low, high):
        def run():
            bucket = self._r.sets.get(key, {})
            for member in [m for m, score in bucket.items() if score <= float(high)]:
                del bucket[member]

        self._ops.append(run)

    def zremrangebyrank(self, key, start, stop):
        def run():
            bucket = self._r.sets.get(key, {})
            ordered = sorted(bucket, key=bucket.__getitem__)
            drop = ordered[start : (len(ordered) + stop + 1) if stop < 0 else stop + 1]
            for member in drop:
                del bucket[member]

        self._ops.append(run)

    def zrange(self, key, start, stop, withscores=False):
        def run():
            bucket = self._r.sets.get(key, {})
            return sorted(bucket.items(), key=lambda kv: kv[1])

        self._ops.append(run)

    def expire(self, key, seconds):
        self._ops.append(lambda: self._r.ttl.__setitem__(key, seconds))

    async def execute(self):
        self._r.calls += 1
        return [op() for op in self._ops]


class BrokenRedis:
    def __init__(self) -> None:
        self.calls = 0

    def pipeline(self, transaction: bool = True):
        self.calls += 1
        raise ConnectionError("redis is down")

    async def delete(self, *keys):
        self.calls += 1
        raise ConnectionError("redis is down")


def _worker(redis, clock, prefix="t"):
    """One API worker: its own memory counters, the shared Redis."""
    memory = MemoryLoginThrottle(LoginThrottle(clock=clock))
    return RedisLoginThrottle(
        "redis://unused", fallback=memory, clock=clock, key_prefix=prefix, client=redis, retry_after_error_seconds=5
    )


def run(coro):
    return asyncio.run(coro)


def test_a_failure_seen_by_one_worker_blocks_the_others():
    redis, clock = FakeRedis(), Clock()
    a, b = _worker(redis, clock), _worker(redis, clock)

    async def scenario():
        for _ in range(5):
            assert (await a.check("1.1.1.1", "a@x.test")).allowed
            await a.record_failure("1.1.1.1", "a@x.test")
            clock.now += 10
        blocked = await b.check("9.9.9.9", " A@X.test ")  # another worker, another address, same e-mail normalised
        assert not blocked.allowed and 200 < blocked.retry_after_seconds <= 301
        assert (await b.check("9.9.9.9", "other@x.test")).allowed
        clock.now += WINDOW
        assert (await b.check("9.9.9.9", "a@x.test")).allowed, "the oldest failure left the window"

    run(scenario())


def test_ten_failures_from_one_address_block_it_on_every_worker():
    redis, clock = FakeRedis(), Clock()
    a, b = _worker(redis, clock), _worker(redis, clock)

    async def scenario():
        for i in range(10):
            await (a if i % 2 else b).record_failure("2.2.2.2", f"user{i}@x.test")
        assert not (await a.check("2.2.2.2", "fresh@x.test")).allowed
        assert not (await b.check("2.2.2.2", "fresh@x.test")).allowed
        assert (await a.check("3.3.3.3", "fresh@x.test")).allowed

    run(scenario())


def test_success_clears_only_the_shared_email_counter_and_the_email_is_stored_only_as_a_hash():
    redis, clock = FakeRedis(), Clock()
    a, b = _worker(redis, clock, prefix="p"), _worker(redis, clock, prefix="p")

    async def scenario():
        for _ in range(4):
            await a.record_failure("1.1.1.1", "Secret@x.test")
        digest = hashlib.sha256(normalise_email("secret@x.test").encode()).hexdigest()
        assert set(redis.sets) == {"p:ip:1.1.1.1", f"p:email:{digest}"}
        assert not any("secret" in key for key in redis.sets), "no e-mail address in a Redis key"
        assert all(ttl == int(WINDOW) + 1 for ttl in redis.ttl.values()), "keys expire with the window"
        await b.clear_email("secret@x.test")
        assert set(redis.sets) == {"p:ip:1.1.1.1"}, "a valid sign-in must not wipe the address"
        assert len(redis.sets["p:ip:1.1.1.1"]) == 4
        assert (await a.check("2.2.2.2", "secret@x.test")).allowed

    run(scenario())


def test_a_set_never_grows_past_its_cap():
    redis, clock = FakeRedis(), Clock()
    a = _worker(redis, clock)

    async def scenario():
        for _ in range(150):
            await a.record_failure("1.1.1.1", "a@x.test")
            clock.now += 0.001
        assert all(len(bucket) <= 100 for bucket in redis.sets.values())

    run(scenario())


def test_when_redis_is_down_the_worker_counts_in_its_own_memory_and_does_not_fail_open():
    broken, clock = BrokenRedis(), Clock()
    worker = _worker(broken, clock)

    async def scenario():
        for _ in range(5):
            assert (await worker.check("1.1.1.1", "a@x.test")).allowed
            await worker.record_failure("1.1.1.1", "a@x.test")
        blocked = await worker.check("1.1.1.1", "a@x.test")
        assert not blocked.allowed, "five failures still block, from this worker's memory"
        calls_after_first_error = broken.calls
        for _ in range(20):
            await worker.check("1.1.1.1", "a@x.test")
        assert broken.calls == calls_after_first_error, "Redis is left alone for a few seconds after an error"
        clock.now += 6
        await worker.check("1.1.1.1", "a@x.test")
        assert broken.calls > calls_after_first_error, "and tried again afterwards"

    run(scenario())


def test_an_unreachable_redis_url_degrades_to_memory_instead_of_raising():
    clock = Clock()
    memory = MemoryLoginThrottle(LoginThrottle(clock=clock))
    worker = RedisLoginThrottle("redis://127.0.0.1:1/0", fallback=memory, clock=clock)

    async def scenario():
        assert (await worker.check("1.1.1.1", "a@x.test")).allowed
        for _ in range(5):
            await worker.record_failure("1.1.1.1", "a@x.test")
        assert not (await worker.check("1.1.1.1", "a@x.test")).allowed

    run(scenario())


# --- the same against the real Redis of docker-compose ---------------------------------------------------------------


def _real_redis_url() -> str | None:
    import redis

    url = "redis://127.0.0.1:6379/0"
    try:
        redis.Redis.from_url(url, socket_connect_timeout=0.3).ping()
    except Exception:
        return None
    return url


REAL_URL = _real_redis_url()


@pytest.mark.skipif(REAL_URL is None, reason="no Redis on 127.0.0.1:6379")
def test_two_workers_share_the_counters_in_a_real_redis_and_keys_expire():
    prefix = f"ulp:test:{uuid.uuid4().hex}"

    async def scenario():
        from redis.asyncio import Redis

        a = RedisLoginThrottle(REAL_URL, fallback=MemoryLoginThrottle(LoginThrottle()), key_prefix=prefix)
        b = RedisLoginThrottle(REAL_URL, fallback=MemoryLoginThrottle(LoginThrottle()), key_prefix=prefix)
        probe = Redis.from_url(REAL_URL, decode_responses=True)
        try:
            for _ in range(5):
                assert (await a.check("8.8.8.8", "real@x.test")).allowed
                await a.record_failure("8.8.8.8", "real@x.test")
            blocked = await b.check("4.4.4.4", "REAL@x.test")
            assert not blocked.allowed and blocked.retry_after_seconds > 0
            keys = [k async for k in probe.scan_iter(f"{prefix}:*")]
            ttls = [await probe.ttl(k) for k in keys]
            assert len(keys) == 2 and all(0 < ttl <= int(WINDOW) + 1 for ttl in ttls)
            await b.clear_email("real@x.test")
            assert (await a.check("4.4.4.4", "real@x.test")).allowed, "the e-mail is clear"
            assert [k async for k in probe.scan_iter(f"{prefix}:*")] == [f"{prefix}:ip:8.8.8.8"]
        finally:
            for key in [k async for k in probe.scan_iter(f"{prefix}:*")]:
                await probe.delete(key)
            await probe.aclose()

    run(scenario())


@pytest.mark.skipif(REAL_URL is None, reason="no Redis on 127.0.0.1:6379")
def test_the_login_route_counts_in_redis_when_the_setting_says_so(monkeypatch):
    """LOGIN_THROTTLE_BACKEND=redis through the real route: each request runs on its own event loop."""
    import redis
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app
    from app.platform import login_throttle as module

    settings = get_settings()
    monkeypatch.setattr(settings, "login_throttle_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", REAL_URL)
    monkeypatch.setattr(module, "_backend_cache", {})
    monkeypatch.setattr(module, "_memory_backend", MemoryLoginThrottle(LoginThrottle()))
    peer = f"203.0.113.{uuid.uuid4().int % 200 + 20}"
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=(peer, 40000))
    probe = redis.Redis.from_url(REAL_URL, decode_responses=True)
    emails = [f"nobody-{uuid.uuid4().hex[:8]}@x.test" for _ in range(11)]
    try:
        statuses = [
            client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-wrong-1"}).status_code
            for email in emails
        ]
        assert statuses == [401] * 10 + [429]
        assert probe.zcard(f"ulp:login:ip:{peer}") == 10, "the failures are in Redis, not only in this process"
    finally:  # only the keys this test made: the Redis may be a developer's
        probe.delete(f"ulp:login:ip:{peer}")
        probe.delete(*(f"ulp:login:email:{hashlib.sha256(normalise_email(e).encode()).hexdigest()}" for e in emails))
