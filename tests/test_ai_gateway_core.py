"""Pure unit tests for E01 AI Orchestration & Provider Gateway core (P24)."""

from __future__ import annotations

import inspect
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.ai_gateway.public import (
    ApiKeyInput,
    DailyBudgetExceededError,
    DailyBudgetLedger,
    KeyStatus,
    ProviderKeyRing,
    ReservationStateError,
)


class _MutableUtcClock:
    def __init__(self, initial: datetime) -> None:
        self.now = initial

    def __call__(self) -> datetime:
        return self.now


class _MutableMonoClock:
    def __init__(self, initial: float = 100.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now


def test_daily_ai_budget_usd_is_required_without_default() -> None:
    sig = inspect.signature(DailyBudgetLedger.reserve)
    param = sig.parameters["daily_ai_budget_usd"]
    assert param.default is inspect.Parameter.empty

    ledger = DailyBudgetLedger()
    with pytest.raises(TypeError):
        ledger.reserve("tenant-1", Decimal("0.10"))  # type: ignore[call-arg]


def test_asia_ho_chi_minh_day_boundary_resets_daily_budget() -> None:
    # 2026-11-13 16:59:59Z == 2026-11-13 23:59:59+07:00
    clock = _MutableUtcClock(datetime(2026, 11, 13, 16, 59, 59, tzinfo=UTC))
    ledger = DailyBudgetLedger(clock=clock)
    budget = Decimal("0.50")

    r1 = ledger.reserve("tenant-a", Decimal("0.40"), daily_ai_budget_usd=budget)
    ledger.settle(r1.reservation_id, Decimal("0.45"))

    # Still same VN day -> requesting 0.10 exceeds 0.50
    with pytest.raises(DailyBudgetExceededError) as exc:
        ledger.reserve("tenant-a", Decimal("0.10"), daily_ai_budget_usd=budget)
    assert exc.value.code == "DAILY_AI_BUDGET_EXCEEDED"

    # Cross midnight Asia/Ho_Chi_Minh (17:00:00Z == 00:00:00+07:00 next day)
    clock.now = datetime(2026, 11, 13, 17, 0, 0, tzinfo=UTC)
    snap = ledger.snapshot("tenant-a", daily_ai_budget_usd=budget)
    assert snap.vn_day.isoformat() == "2026-11-14"
    assert snap.spent_usd == Decimal("0")
    assert snap.remaining_usd == budget

    r2 = ledger.reserve("tenant-a", Decimal("0.40"), daily_ai_budget_usd=budget)
    assert r2.vn_day.isoformat() == "2026-11-14"


def test_two_phase_concurrent_reserve_blocks_combined_overrun() -> None:
    ledger = DailyBudgetLedger(clock=_MutableUtcClock(datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)))
    daily_limit = Decimal("1.00")
    barrier = threading.Barrier(2)

    def _try_reserve(_: int) -> str:
        barrier.wait(timeout=5)
        try:
            res = ledger.reserve("tenant-race", Decimal("0.60"), daily_ai_budget_usd=daily_limit)
            return f"RESERVED:{res.reservation_id}"
        except DailyBudgetExceededError as exc:
            return f"REJECTED:{exc.code}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(_try_reserve, [0, 1]))

    reserved = [o for o in outcomes if o.startswith("RESERVED:")]
    rejected = [o for o in outcomes if o.startswith("REJECTED:")]
    assert len(reserved) == 1
    assert rejected == ["REJECTED:DAILY_AI_BUDGET_EXCEEDED"]

    # Releasing the held reservation frees budget cleanly
    res_id = reserved[0].split(":", 1)[1]
    ledger.release(res_id)
    with pytest.raises(ReservationStateError) as dbl:
        ledger.settle(res_id, Decimal("0.10"))
    assert dbl.value.code == "RESERVATION_ALREADY_CLOSED"

    snap = ledger.snapshot("tenant-race", daily_ai_budget_usd=daily_limit)
    assert snap.remaining_usd == Decimal("1.00")


def test_exact_decimal_precision_without_float_drift() -> None:
    ledger = DailyBudgetLedger(clock=_MutableUtcClock(datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)))
    cap = Decimal("0.0003")
    r1 = ledger.reserve("t-dec", Decimal("0.0001"), daily_ai_budget_usd=cap)
    ledger.settle(r1.reservation_id, Decimal("0.0001"))
    r2 = ledger.reserve("t-dec", Decimal("0.0002"), daily_ai_budget_usd=cap)
    ledger.settle(r2.reservation_id, Decimal("0.0002"))

    snap = ledger.snapshot("t-dec", daily_ai_budget_usd=cap)
    assert snap.spent_usd == Decimal("0.0003")
    assert snap.remaining_usd == Decimal("0.0000")

    with pytest.raises(DailyBudgetExceededError):
        ledger.reserve("t-dec", Decimal("0.00001"), daily_ai_budget_usd=cap)


def test_key_ring_429_cooldown_wait_without_sleep_and_401_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbid_sleep(_: float) -> None:
        raise AssertionError("KeyRing must never sleep inside core")

    monkeypatch.setattr(time, "sleep", _forbid_sleep)

    mono = _MutableMonoClock(1000.0)
    ring = ProviderKeyRing(
        [
            ApiKeyInput(key_id="k1", secret="AIzaSySecretOne_DO_NOT_LEAK"),
            ApiKeyInput(key_id="k2", secret="AIzaSySecretTwo_DO_NOT_LEAK"),
            ApiKeyInput(key_id="k3", secret="AIzaSySecretThree_DO_NOT_LEAK"),
        ],
        clock=mono,
    )

    first = ring.acquire()
    assert first.acquired and first.key_id == "k1"
    assert first.get_secret() == "AIzaSySecretOne_DO_NOT_LEAK"

    # 429 on k1 puts k1 into 30s cooldown and immediately serves k2
    ring.mark_rate_limited("k1", cooldown_seconds=30.0)
    assert ring.status_of("k1") == KeyStatus.COOLDOWN

    second = ring.acquire()
    assert second.acquired and second.key_id == "k2"

    # 401 permanently disables k2 (never returns even after long elapsed time)
    ring.mark_permanently_invalid("k2", status_code=401)
    assert ring.status_of("k2") == KeyStatus.DISABLED

    third = ring.acquire()
    assert third.acquired and third.key_id == "k3"

    # Put remaining healthy key k3 in 12s cooldown -> all remaining keys are in cooldown
    ring.mark_rate_limited("k3", cooldown_seconds=12.0)
    blocked = ring.acquire()
    assert not blocked.acquired
    assert not blocked.exhausted
    assert blocked.wait_seconds == pytest.approx(12.0)

    # Advance 12s -> k3 wakes up, k2 remains permanently disabled
    mono.now += 12.0
    woken = ring.acquire()
    assert woken.acquired and woken.key_id == "k3"
    assert ring.status_of("k2") == KeyStatus.DISABLED

    # Disable k3 and k1 permanently -> exhausted
    mono.now += 30.0  # k1 wakes up
    ring.mark_permanently_invalid("k1", status_code=401)
    ring.mark_permanently_invalid("k3", status_code=401)
    exhausted = ring.acquire()
    assert not exhausted.acquired
    assert exhausted.exhausted
    assert exhausted.wait_seconds is None


def test_key_secrets_never_leak_into_repr_str_exceptions_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    secret_a = "sk-live-SUPER-SECRET-ALPHA-999"
    secret_b = "sk-live-SUPER-SECRET-BETA-888"

    key_in = ApiKeyInput(key_id="primary", secret=secret_a)
    ring = ProviderKeyRing([key_in, ApiKeyInput(key_id="backup", secret=secret_b)], clock=_MutableMonoClock())

    lease = ring.acquire()
    ring.mark_rate_limited("primary", cooldown_seconds=10.0)
    ring.mark_rate_limited("backup", cooldown_seconds=20.0)
    cooldown_outcome = ring.acquire()

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("test_ai_gateway").info("state=%r result=%s cooldown=%r", ring, lease, cooldown_outcome)

    artifacts = [
        repr(key_in),
        str(key_in),
        repr(ring),
        str(ring),
        repr(lease),
        str(lease),
        repr(cooldown_outcome),
        str(cooldown_outcome),
        caplog.text,
    ]

    with pytest.raises(LookupError) as exc:
        cooldown_outcome.get_secret()
    artifacts.append(repr(exc.value))
    artifacts.append(str(exc.value))

    for secret in (secret_a, secret_b):
        for artifact in artifacts:
            assert secret not in artifact


def test_key_woken_from_cooldown_is_not_queued_twice() -> None:
    from app.modules.ai_gateway.internal.key_ring import ApiKeyInput, ProviderKeyRing

    now = [0.0]
    ring = ProviderKeyRing([ApiKeyInput("k1", "s1"), ApiKeyInput("k2", "s2")], clock=lambda: now[0])
    for _ in range(5):
        # k1 is rate limited while still sitting in the ready queue, then wakes up again.
        ring.mark_rate_limited("k1", cooldown_seconds=1.0)
        now[0] += 2.0
        assert ring.healthy_count() == 2
    served = [ring.acquire().key_id for _ in range(8)]
    assert served.count("k1") == 4
    assert served.count("k2") == 4
