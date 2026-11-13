"""Per-tenant daily AI budget ledger in Decimal with Asia/Ho_Chi_Minh day boundary and two-phase reserve/settle."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    VN_TIMEZONE: tzinfo = ZoneInfo("Asia/Ho_Chi_Minh")
except ZoneInfoNotFoundError:
    # Asia/Ho_Chi_Minh is permanently UTC+7 without DST; fallback when tzdata is absent.
    VN_TIMEZONE = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")


class BudgetGatewayError(Exception):
    """Base error with machine-readable code for E01 budget checks."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DailyBudgetExceededError(BudgetGatewayError):
    """Raised when reserving would exceed `daily_ai_budget_usd` for a tenant's VN day."""

    def __init__(self, message: str) -> None:
        super().__init__("DAILY_AI_BUDGET_EXCEEDED", message)


class ReservationStateError(BudgetGatewayError):
    """Raised when settling or releasing a missing or already finalized reservation."""


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: str
    tenant_id: str
    vn_day: date
    reserved_usd: Decimal


@dataclass(frozen=True, slots=True)
class TenantDailyBudgetSnapshot:
    tenant_id: str
    vn_day: date
    daily_ai_budget_usd: Decimal
    spent_usd: Decimal
    reserved_usd: Decimal
    remaining_usd: Decimal


@dataclass(slots=True)
class _DayBucket:
    spent_usd: Decimal = Decimal("0")
    reserved_usd: Decimal = Decimal("0")


class DailyBudgetLedger:
    """
    Two-phase (`reserve` -> `settle` / `release`) daily AI budget guard in `Decimal`.

    Day boundaries strictly follow `Asia/Ho_Chi_Minh` (UTC+7).
    `daily_ai_budget_usd` is a mandatory parameter on every check/reserve call with NO default.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, date], _DayBucket] = {}
        self._open_reservations: dict[str, BudgetReservation] = {}
        self._closed_reservations: set[str] = set()

    def current_vn_day(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(VN_TIMEZONE).date()

    def reserve(
        self,
        tenant_id: str,
        estimated_cost_usd: Decimal,
        *,
        daily_ai_budget_usd: Decimal,
    ) -> BudgetReservation:
        """
        Atomically check ceiling and reserve `estimated_cost_usd` before invoking an AI provider.

        Raises `DailyBudgetExceededError` if `spent + reserved + estimated > daily_ai_budget_usd`.
        """
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        if not isinstance(daily_ai_budget_usd, Decimal):
            raise TypeError("daily_ai_budget_usd must be a Decimal")
        if not isinstance(estimated_cost_usd, Decimal):
            raise TypeError("estimated_cost_usd must be a Decimal")
        if daily_ai_budget_usd < Decimal("0"):
            raise ValueError("daily_ai_budget_usd must be >= 0")
        if estimated_cost_usd < Decimal("0"):
            raise ValueError("estimated_cost_usd must be >= 0")

        with self._lock:
            day = self.current_vn_day()
            bucket = self._buckets.setdefault((tenant_id, day), _DayBucket())
            projected = bucket.spent_usd + bucket.reserved_usd + estimated_cost_usd
            if projected > daily_ai_budget_usd:
                raise DailyBudgetExceededError(
                    f"Tenant {tenant_id} exceeded daily_ai_budget_usd={daily_ai_budget_usd} on {day.isoformat()} "
                    f"(spent={bucket.spent_usd}, reserved={bucket.reserved_usd}, requested={estimated_cost_usd})"
                )
            bucket.reserved_usd += estimated_cost_usd
            reservation = BudgetReservation(
                reservation_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                vn_day=day,
                reserved_usd=estimated_cost_usd,
            )
            self._open_reservations[reservation.reservation_id] = reservation
            return reservation

    def settle(self, reservation_id: str, actual_cost_usd: Decimal) -> None:
        """Convert an open reservation into finalized spent USD."""
        if not isinstance(actual_cost_usd, Decimal):
            raise TypeError("actual_cost_usd must be a Decimal")
        if actual_cost_usd < Decimal("0"):
            raise ValueError("actual_cost_usd must be >= 0")

        with self._lock:
            if reservation_id in self._closed_reservations:
                raise ReservationStateError(
                    "RESERVATION_ALREADY_CLOSED",
                    f"Reservation {reservation_id} has already been finalized",
                )
            reservation = self._open_reservations.pop(reservation_id, None)
            if reservation is None:
                raise ReservationStateError(
                    "RESERVATION_NOT_FOUND",
                    f"Unknown reservation {reservation_id}",
                )
            bucket = self._buckets[(reservation.tenant_id, reservation.vn_day)]
            bucket.reserved_usd = max(Decimal("0"), bucket.reserved_usd - reservation.reserved_usd)
            bucket.spent_usd += actual_cost_usd
            self._closed_reservations.add(reservation_id)

    def release(self, reservation_id: str) -> None:
        """Release an unused reservation back to the tenant's available daily budget."""
        with self._lock:
            if reservation_id in self._closed_reservations:
                raise ReservationStateError(
                    "RESERVATION_ALREADY_CLOSED",
                    f"Reservation {reservation_id} has already been finalized",
                )
            reservation = self._open_reservations.pop(reservation_id, None)
            if reservation is None:
                raise ReservationStateError(
                    "RESERVATION_NOT_FOUND",
                    f"Unknown reservation {reservation_id}",
                )
            bucket = self._buckets[(reservation.tenant_id, reservation.vn_day)]
            bucket.reserved_usd = max(Decimal("0"), bucket.reserved_usd - reservation.reserved_usd)
            self._closed_reservations.add(reservation_id)

    def snapshot(
        self,
        tenant_id: str,
        *,
        daily_ai_budget_usd: Decimal,
    ) -> TenantDailyBudgetSnapshot:
        """Read current VN-day budget state for `tenant_id`."""
        with self._lock:
            day = self.current_vn_day()
            bucket = self._buckets.get((tenant_id, day), _DayBucket())
            remaining = max(
                Decimal("0"),
                daily_ai_budget_usd - bucket.spent_usd - bucket.reserved_usd,
            )
            return TenantDailyBudgetSnapshot(
                tenant_id=tenant_id,
                vn_day=day,
                daily_ai_budget_usd=daily_ai_budget_usd,
                spent_usd=bucket.spent_usd,
                reserved_usd=bucket.reserved_usd,
                remaining_usd=remaining,
            )
