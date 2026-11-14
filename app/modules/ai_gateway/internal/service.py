"""E01 AI gateway service: jobs, per-tenant daily budget, model choice with circuit breakers, traces and keys.

* **Budget.** A tenant's day is the Asia/Ho_Chi_Minh day (`budget.VN_TIMEZONE`). Spent money is the sum of the
  day's `ai_traces`; money promised to jobs that are still QUEUED or RUNNING (their estimate, kept in the job's
  `input_refs_json["_gateway"]`) is *reserved*. Submitting a job locks the tenant's row, so two concurrent
  submissions cannot both squeeze under the ceiling. A tenant at or over its ceiling is refused (429).
* **Models.** `select_models(task_role)` lists the active models of the role whose provider is not in a
  cooling-down OPEN circuit, cheapest first: the first is the primary, the rest are the fallbacks the executor
  tries on 429/5xx or a call slower than 15 s.
* **Circuit breaker.** `report_provider_outcome` feeds `platform.circuit_breaker` and stores the state in
  `ai_providers.circuit_state`, which every node reads; `allow_provider` moves OPEN to HALF_OPEN after the cooldown.
  The failure counter is per process (the table keeps the state only): a node opens the breaker for all.
* **Keys.** `GatewayKeyRings` holds one `ProviderKeyRing` per provider for the executor (rotation on 429).
* **Traces.** `record_trace` stores one call with its cost and announces `ai.budget.threshold_exceeded` when the
  day's spend crosses 80 % or 100 % of the ceiling (outbox, same transaction).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.ai_gateway.internal.budget import VN_TIMEZONE, DailyBudgetExceededError, TenantDailyBudgetSnapshot
from app.modules.ai_gateway.internal.key_ring import ApiKeyInput, KeyAcquisitionResult, ProviderKeyRing
from app.modules.ai_gateway.internal.models import AiJob, AiModel, AiProvider, AiTrace, PromptVersion
from app.modules.outbox.public import enqueue
from app.platform.circuit_breaker import (
    CircuitBreakerPolicy,
    CircuitSnapshot,
    CircuitState,
    evaluate_circuit_entry,
    initial_circuit,
    record_circuit_outcome,
)

GATEWAY_KEY = "_gateway"
BUDGET_THRESHOLD_EVENT = "ai.budget.threshold_exceeded"
BUDGET_THRESHOLDS_PCT = (80, 100)
CIRCUIT_POLICY = CircuitBreakerPolicy()
_COST_QUANTUM = Decimal("0.000001")
_TRANSITIONS = {"QUEUED": {"RUNNING", "FAILED"}, "RUNNING": {"COMPLETED", "FAILED"}}


class AiGatewayError(Exception):
    """Base of the gateway's refusals; `code` is machine readable."""

    code = "AI_GATEWAY_ERROR"


class JobNotFoundError(AiGatewayError, LookupError):
    code = "JOB_NOT_FOUND"


class PromptNotFoundError(AiGatewayError, LookupError):
    code = "PROMPT_NOT_FOUND"


class ModelNotFoundError(AiGatewayError, LookupError):
    code = "MODEL_NOT_FOUND"


class ProviderNotFoundError(AiGatewayError, LookupError):
    code = "PROVIDER_NOT_FOUND"


class NoModelAvailableError(AiGatewayError):
    """No active model of the role is reachable (none exists, or every provider is cooling down)."""

    code = "NO_MODEL_AVAILABLE"


class JobStateError(AiGatewayError):
    code = "JOB_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class ModelChoice:
    model_id: uuid.UUID
    provider_code: str
    model_name: str
    task_role: str
    input_cost_per_1m: Decimal
    output_cost_per_1m: Decimal


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    task_type: str
    status: str
    prompt_version_id: uuid.UUID | None
    input_refs: dict[str, Any]
    output: dict[str, Any] | None
    created_at: datetime
    requested_by: uuid.UUID | None
    task_role: str | None
    estimated_cost_usd: Decimal
    primary_model_id: uuid.UUID | None
    fallback_model_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class TraceRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    ai_job_id: uuid.UUID | None
    model_id: uuid.UUID
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ModelUsage:
    model_id: uuid.UUID
    provider_code: str
    model_name: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class UsageReport:
    budget: TenantDailyBudgetSnapshot
    calls: int
    by_model: tuple[ModelUsage, ...]


# --- the tenant's day ---------------------------------------------------------------------------


def vn_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC instants where the Vietnamese calendar `day` starts and ends."""
    start = datetime.combine(day, time.min, tzinfo=VN_TIMEZONE)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def vn_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(VN_TIMEZONE).date()


def estimate_cost_usd(model: ModelChoice, prompt_tokens: int, completion_tokens: int) -> Decimal:
    cost = (model.input_cost_per_1m * prompt_tokens + model.output_cost_per_1m * completion_tokens) / Decimal(1_000_000)
    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


async def _lock_tenant_budget(session: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    """Serialise budget decisions of one tenant; returns its daily ceiling."""
    budget = await session.scalar(
        text("SELECT daily_ai_budget_usd FROM tenants WHERE id = :t FOR UPDATE"), {"t": tenant_id}
    )
    if budget is None:
        raise AiGatewayError(f"tenant {tenant_id} not found")
    return Decimal(budget)


async def _spent(session: AsyncSession, tenant_id: uuid.UUID, day: date) -> Decimal:
    start, end = vn_day_bounds(day)
    total = await session.scalar(
        select(func.coalesce(func.sum(AiTrace.cost_usd), 0)).where(
            AiTrace.tenant_id == tenant_id, AiTrace.occurred_at >= start, AiTrace.occurred_at < end
        )
    )
    return Decimal(total)


async def _reserved(session: AsyncSession) -> Decimal:
    """Estimates promised to jobs that have not finished (the session's tenant, by RLS)."""
    total = await session.scalar(
        text(
            "SELECT COALESCE(SUM((input_refs_json -> :k ->> 'estimated_cost_usd')::numeric), 0) "
            "FROM ai_jobs WHERE status IN ('QUEUED', 'RUNNING')"
        ),
        {"k": GATEWAY_KEY},
    )
    return Decimal(total)


async def budget_snapshot(
    session: AsyncSession, tenant_id: uuid.UUID, *, day: date | None = None, lock: bool = False
) -> TenantDailyBudgetSnapshot:
    ceiling = await (_lock_tenant_budget(session, tenant_id) if lock else _ceiling(session, tenant_id))
    when = day or vn_today()
    spent, reserved = await _spent(session, tenant_id, when), await _reserved(session)
    return TenantDailyBudgetSnapshot(
        tenant_id=str(tenant_id),
        vn_day=when,
        daily_ai_budget_usd=ceiling,
        spent_usd=spent,
        reserved_usd=reserved,
        remaining_usd=max(Decimal(0), ceiling - spent - reserved),
    )


async def _ceiling(session: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
    budget = await session.scalar(text("SELECT daily_ai_budget_usd FROM tenants WHERE id = :t"), {"t": tenant_id})
    if budget is None:
        raise AiGatewayError(f"tenant {tenant_id} not found")
    return Decimal(budget)


# --- providers and circuit breakers ---------------------------------------------------------------

_local_circuits: dict[str, CircuitSnapshot] = {}


def _snapshot(row: AiProvider) -> CircuitSnapshot:
    """The breaker as the database knows it, with this process's failure count when it agrees."""
    opened_at = row.updated_at.timestamp()
    state: CircuitState = row.circuit_state  # type: ignore[assignment]
    local = _local_circuits.get(row.code)
    if local is not None and local.state == state:
        return local
    if state == "OPEN":
        return CircuitSnapshot("OPEN", CIRCUIT_POLICY.failure_threshold, opened_at, 0, 0)
    if state == "HALF_OPEN":
        return CircuitSnapshot("HALF_OPEN", 0, opened_at, 0, 0)
    return initial_circuit()


async def _provider(session: AsyncSession, code: str, *, lock: bool = False) -> AiProvider:
    query = select(AiProvider).where(AiProvider.code == code).execution_options(populate_existing=True)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ProviderNotFoundError(f"provider {code} not found")
    return row


async def _persist_state(session: AsyncSession, row: AiProvider, new: CircuitSnapshot, now: datetime) -> None:
    _local_circuits[row.code] = new
    if new.state != row.circuit_state:
        await session.execute(
            update(AiProvider).where(AiProvider.code == row.code).values(circuit_state=new.state, updated_at=now)
        )


async def allow_provider(session: AsyncSession, provider_code: str, *, now: datetime | None = None) -> bool:
    """May a call go to the provider now? An OPEN breaker whose cooldown is over lets one probe through."""
    moment = now or datetime.now(UTC)
    row = await _provider(session, provider_code, lock=True)
    decision = evaluate_circuit_entry(_snapshot(row), now_monotonic=moment.timestamp(), policy=CIRCUIT_POLICY)
    await _persist_state(session, row, decision.next_snapshot, moment)
    return decision.allowed


async def report_provider_outcome(
    session: AsyncSession,
    provider_code: str,
    *,
    succeeded: bool,
    latency_seconds: float,
    status_code: int | None = None,
    now: datetime | None = None,
) -> str:
    """Record a call result; 429, 5xx and calls slower than 15 s count as failures. Returns the new state."""
    moment = now or datetime.now(UTC)
    row = await _provider(session, provider_code, lock=True)
    new = record_circuit_outcome(
        _snapshot(row),
        succeeded=succeeded,
        latency_seconds=latency_seconds,
        status_code=status_code,
        now_monotonic=moment.timestamp(),
        policy=CIRCUIT_POLICY,
    )
    await _persist_state(session, row, new, moment)
    return new.state


async def select_models(session: AsyncSession, task_role: str, *, now: datetime | None = None) -> list[ModelChoice]:
    """Active models of `task_role`, cheapest first, skipping providers in a cooling-down OPEN circuit."""
    moment = (now or datetime.now(UTC)).timestamp()
    rows = (
        await session.execute(
            select(AiModel, AiProvider)
            .join(AiProvider, AiProvider.code == AiModel.provider_code)
            .where(AiModel.task_role == task_role, AiModel.is_active.is_(True))
            .order_by((AiModel.input_cost_per_1m + AiModel.output_cost_per_1m), AiModel.model_name, AiModel.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    choices = []
    for model, provider in rows:
        if provider.circuit_state == "OPEN":
            elapsed = moment - provider.updated_at.timestamp()
            if elapsed < CIRCUIT_POLICY.cooldown_seconds:
                continue
        choices.append(
            ModelChoice(
                model.id,
                model.provider_code,
                model.model_name,
                model.task_role,
                Decimal(model.input_cost_per_1m),
                Decimal(model.output_cost_per_1m),
            )  # fmt: skip
        )
    return choices


# --- jobs -----------------------------------------------------------------------------------------


def _job(row: AiJob) -> JobRecord:
    gateway = (row.input_refs_json or {}).get(GATEWAY_KEY, {})
    requested_by = gateway.get("requested_by")
    fallbacks = tuple(uuid.UUID(m) for m in gateway.get("fallback_model_ids", ()))
    primary = gateway.get("primary_model_id")
    return JobRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        task_type=row.task_type,
        status=row.status,
        prompt_version_id=row.prompt_version_id,
        input_refs={k: v for k, v in (row.input_refs_json or {}).items() if k != GATEWAY_KEY},
        output=row.output_json,
        created_at=row.created_at,
        requested_by=None if requested_by is None else uuid.UUID(requested_by),
        task_role=gateway.get("task_role"),
        estimated_cost_usd=Decimal(gateway.get("estimated_cost_usd", "0")),
        primary_model_id=None if primary is None else uuid.UUID(primary),
        fallback_model_ids=fallbacks,
    )


async def submit_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_by: uuid.UUID,
    task_type: str,
    input_refs: Mapping[str, Any] | None = None,
    prompt_code: str | None = None,
    task_role: str | None = None,
    estimated_prompt_tokens: int = 0,
    estimated_completion_tokens: int = 0,
    now: datetime | None = None,
) -> JobRecord:
    """Queue an AI job after the budget check; raises DailyBudgetExceededError at or over the ceiling.

    With a `task_role` the cheapest reachable model is the primary and the rest are recorded as fallbacks; the
    estimate (tokens times that model's prices) is reserved against the day's ceiling until the job finishes.
    """
    refs = dict(input_refs or {})
    if GATEWAY_KEY in refs:
        raise ValueError(f"input_refs must not contain {GATEWAY_KEY!r}")
    moment = now or datetime.now(UTC)
    ceiling = await _lock_tenant_budget(session, tenant_id)

    prompt_id: uuid.UUID | None = None
    if prompt_code is not None:
        prompt_id = await session.scalar(
            select(PromptVersion.id)
            .where(PromptVersion.code == prompt_code)
            .order_by(PromptVersion.version_no.desc())
            .limit(1)
        )
        if prompt_id is None:
            raise PromptNotFoundError(f"prompt {prompt_code!r} not found")

    choices: list[ModelChoice] = []
    estimate = Decimal(0)
    if task_role is not None:
        choices = await select_models(session, task_role, now=moment)
        if not choices:
            raise NoModelAvailableError(f"no reachable active model for task role {task_role}")
        estimate = estimate_cost_usd(choices[0], estimated_prompt_tokens, estimated_completion_tokens)

    day = vn_today(moment)
    spent, reserved = await _spent(session, tenant_id, day), await _reserved(session)
    if spent + reserved >= ceiling or spent + reserved + estimate > ceiling:
        raise DailyBudgetExceededError(
            f"tenant {tenant_id} reached daily_ai_budget_usd={ceiling} on {day.isoformat()} "
            f"(spent={spent}, reserved={reserved}, requested={estimate})"
        )
    refs[GATEWAY_KEY] = {
        "requested_by": str(requested_by),
        "task_role": task_role,
        "estimated_cost_usd": str(estimate),
        "primary_model_id": None if not choices else str(choices[0].model_id),
        "fallback_model_ids": [str(c.model_id) for c in choices[1:]],
    }
    row = (
        await session.execute(
            insert(AiJob)
            .values(tenant_id=tenant_id, task_type=task_type, prompt_version_id=prompt_id, input_refs_json=refs)
            .returning(AiJob)
        )
    ).scalar_one()
    return _job(row)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> JobRecord | None:
    row = await session.scalar(select(AiJob).where(AiJob.id == job_id).execution_options(populate_existing=True))
    return None if row is None else _job(row)


async def _transition(
    session: AsyncSession, job_id: uuid.UUID, new_status: str, output: Mapping[str, Any] | None = None
) -> JobRecord:
    row = await session.scalar(
        select(AiJob).where(AiJob.id == job_id).with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise JobNotFoundError(f"job {job_id} not found")
    if new_status not in _TRANSITIONS.get(row.status, set()):
        raise JobStateError(f"job is {row.status}, it cannot become {new_status}")
    values: dict[str, Any] = {"status": new_status}
    if output is not None:
        values["output_json"] = dict(output)
    updated = (
        await session.execute(
            update(AiJob)
            .where(AiJob.id == job_id)
            .values(**values)
            .returning(AiJob)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return _job(updated)


async def start_job(session: AsyncSession, job_id: uuid.UUID) -> JobRecord:
    return await _transition(session, job_id, "RUNNING")


async def complete_job(session: AsyncSession, job_id: uuid.UUID, output: Mapping[str, Any]) -> JobRecord:
    """Finish a RUNNING job; its reservation ends (the traces carry what was really spent)."""
    return await _transition(session, job_id, "COMPLETED", output)


async def fail_job(session: AsyncSession, job_id: uuid.UUID, output: Mapping[str, Any] | None = None) -> JobRecord:
    return await _transition(session, job_id, "FAILED", output)


# --- traces and usage -------------------------------------------------------------------------------


async def record_trace(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    model_id: uuid.UUID,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    ai_job_id: uuid.UUID | None = None,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> TraceRecord:
    """Store one provider call with its cost; announce the 80 % and 100 % budget thresholds when crossed."""
    if min(prompt_tokens, completion_tokens, latency_ms) < 0:
        raise ValueError("tokens and latency must be >= 0")
    moment = now or datetime.now(UTC)
    ceiling = await _lock_tenant_budget(session, tenant_id)
    model = await session.scalar(select(AiModel).where(AiModel.id == model_id))
    if model is None:
        raise ModelNotFoundError(f"model {model_id} not found")
    if ai_job_id is not None and await session.scalar(select(AiJob.id).where(AiJob.id == ai_job_id)) is None:
        raise JobNotFoundError(f"job {ai_job_id} not found")
    choice = ModelChoice(
        model.id, model.provider_code, model.model_name, model.task_role,
        Decimal(model.input_cost_per_1m), Decimal(model.output_cost_per_1m),
    )  # fmt: skip
    cost = estimate_cost_usd(choice, prompt_tokens, completion_tokens)
    day = vn_today(moment)
    before = await _spent(session, tenant_id, day)
    row = (
        await session.execute(
            insert(AiTrace)
            .values(
                tenant_id=tenant_id, ai_job_id=ai_job_id, model_id=model_id, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, cost_usd=cost, latency_ms=latency_ms, occurred_at=moment,
            )
            .returning(AiTrace)
        )
    ).scalar_one()  # fmt: skip
    after = before + cost
    for pct in BUDGET_THRESHOLDS_PCT:
        limit = ceiling * pct / 100
        if before < limit <= after:
            await enqueue(
                session,
                EventEnvelopeContract(
                    event_type=BUDGET_THRESHOLD_EVENT,
                    tenant_id=tenant_id,
                    aggregate_type="tenant",
                    aggregate_id=tenant_id,
                    correlation_id=correlation_id,
                    payload={
                        "tenant_id": str(tenant_id),
                        "current_spend_usd": str(after),
                        "daily_budget_usd": str(ceiling),
                        "threshold_pct": pct,
                    },
                ),
            )
    return TraceRecord(
        row.id, row.tenant_id, row.ai_job_id, row.model_id, row.prompt_tokens, row.completion_tokens,
        Decimal(row.cost_usd), row.latency_ms, row.occurred_at,
    )  # fmt: skip


async def usage_report(session: AsyncSession, tenant_id: uuid.UUID, *, day: date | None = None) -> UsageReport:
    """The tenant's budget position and the day's calls per model."""
    when = day or vn_today()
    snapshot = await budget_snapshot(session, tenant_id, day=when)
    start, end = vn_day_bounds(when)
    rows = (
        await session.execute(
            select(
                AiTrace.model_id,
                AiModel.provider_code,
                AiModel.model_name,
                func.count(),
                func.coalesce(func.sum(AiTrace.prompt_tokens), 0),
                func.coalesce(func.sum(AiTrace.completion_tokens), 0),
                func.coalesce(func.sum(AiTrace.cost_usd), 0),
            )
            .join(AiModel, AiModel.id == AiTrace.model_id)
            .where(AiTrace.tenant_id == tenant_id, AiTrace.occurred_at >= start, AiTrace.occurred_at < end)
            .group_by(AiTrace.model_id, AiModel.provider_code, AiModel.model_name)
            .order_by(func.sum(AiTrace.cost_usd).desc(), AiModel.model_name)
        )
    ).all()
    by_model = tuple(ModelUsage(r[0], r[1], r[2], r[3], int(r[4]), int(r[5]), Decimal(r[6])) for r in rows)
    return UsageReport(snapshot, sum(m.calls for m in by_model), by_model)


# --- provider keys -----------------------------------------------------------------------------------


class GatewayKeyRings:
    """One `ProviderKeyRing` per provider, for the executor: rotation on 429, eviction on 401."""

    def __init__(self) -> None:
        self._rings: dict[str, ProviderKeyRing] = {}

    def register(
        self, provider_code: str, keys: Sequence[ApiKeyInput], *, clock: Callable[[], float] | None = None
    ) -> ProviderKeyRing:
        ring = ProviderKeyRing(keys, clock=clock)
        self._rings[provider_code] = ring
        return ring

    def acquire(self, provider_code: str) -> KeyAcquisitionResult:
        ring = self._rings.get(provider_code)
        if ring is None:
            raise ProviderNotFoundError(f"no keys registered for provider {provider_code}")
        return ring.acquire()

    def on_rate_limited(
        self, provider_code: str, key_id: str, *, cooldown_seconds: float = 60.0
    ) -> KeyAcquisitionResult:
        """A 429 on `key_id`: put it to rest and hand out the next key at once."""
        ring = self._rings[provider_code]
        ring.mark_rate_limited(key_id, cooldown_seconds=cooldown_seconds)
        return ring.acquire()

    def on_unauthorized(self, provider_code: str, key_id: str) -> None:
        self._rings[provider_code].mark_permanently_invalid(key_id)


key_rings = GatewayKeyRings()
