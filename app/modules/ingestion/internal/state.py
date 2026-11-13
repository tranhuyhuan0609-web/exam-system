"""A01 ingestion state machines mapped to DDL CHECK constraints on acquisition_jobs & pipeline_stage_runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class AcquisitionJobStatus(StrEnum):
    """Exact status values permitted by DDL CHECK constraint on `acquisition_jobs.job_status`."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class PipelineStageStatus(StrEnum):
    """Exact status values permitted by DDL CHECK constraint on `pipeline_stage_runs.status`."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


TERMINAL_JOB_STATUSES: frozenset[AcquisitionJobStatus] = frozenset(
    {
        AcquisitionJobStatus.COMPLETED,
        AcquisitionJobStatus.DEAD_LETTER,
        AcquisitionJobStatus.CANCELLED,
    }
)

ALLOWED_JOB_TRANSITIONS: Mapping[AcquisitionJobStatus, frozenset[AcquisitionJobStatus]] = (
    MappingProxyType(
        {
            AcquisitionJobStatus.QUEUED: frozenset(
                {AcquisitionJobStatus.RUNNING, AcquisitionJobStatus.CANCELLED}
            ),
            AcquisitionJobStatus.RUNNING: frozenset(
                {
                    AcquisitionJobStatus.COMPLETED,
                    AcquisitionJobStatus.FAILED,
                    AcquisitionJobStatus.DEAD_LETTER,
                    AcquisitionJobStatus.CANCELLED,
                }
            ),
            AcquisitionJobStatus.FAILED: frozenset(
                {
                    AcquisitionJobStatus.RUNNING,
                    AcquisitionJobStatus.DEAD_LETTER,
                    AcquisitionJobStatus.CANCELLED,
                }
            ),
            AcquisitionJobStatus.COMPLETED: frozenset(),
            AcquisitionJobStatus.DEAD_LETTER: frozenset(),
            AcquisitionJobStatus.CANCELLED: frozenset(),
        }
    )
)

ALLOWED_STAGE_TRANSITIONS: Mapping[PipelineStageStatus, frozenset[PipelineStageStatus]] = (
    MappingProxyType(
        {
            PipelineStageStatus.RUNNING: frozenset(
                {
                    PipelineStageStatus.COMPLETED,
                    PipelineStageStatus.FAILED,
                    PipelineStageStatus.SKIPPED,
                }
            ),
            PipelineStageStatus.FAILED: frozenset(
                {
                    PipelineStageStatus.RUNNING,
                    PipelineStageStatus.SKIPPED,
                }
            ),
            PipelineStageStatus.COMPLETED: frozenset(),
            PipelineStageStatus.SKIPPED: frozenset(),
        }
    )
)


class IngestionTransitionError(ValueError):
    """Raised on any illegal ingestion job or pipeline stage transition."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_job_transition(
    from_status: AcquisitionJobStatus,
    to_status: AcquisitionJobStatus,
) -> None:
    """Validate direct `acquisition_jobs.job_status` transition against explicit transition table."""
    allowed = ALLOWED_JOB_TRANSITIONS[from_status]
    if to_status in allowed:
        return
    if to_status == AcquisitionJobStatus.CANCELLED and from_status in TERMINAL_JOB_STATUSES:
        raise IngestionTransitionError(
            "CANCEL_FROM_TERMINAL_STATE",
            f"Cannot cancel job from terminal state {from_status.value}",
        )
    raise IngestionTransitionError(
        "INVALID_JOB_TRANSITION",
        f"Illegal job transition: {from_status.value} -> {to_status.value}",
    )


def validate_stage_transition(
    from_status: PipelineStageStatus,
    to_status: PipelineStageStatus,
) -> None:
    """Validate `pipeline_stage_runs.status` transition against explicit transition table."""
    if to_status in ALLOWED_STAGE_TRANSITIONS[from_status]:
        return
    raise IngestionTransitionError(
        "INVALID_STAGE_TRANSITION",
        f"Illegal stage transition: {from_status.value} -> {to_status.value}",
    )


@dataclass(frozen=True, slots=True)
class StartJobEvent:
    event_id: str
    stage_name: str


@dataclass(frozen=True, slots=True)
class StageCompletedEvent:
    event_id: str
    stage_name: str
    next_stage: str | None = None


@dataclass(frozen=True, slots=True)
class FailJobEvent:
    event_id: str
    stage_name: str
    error_code: str
    error_detail: str = ""


@dataclass(frozen=True, slots=True)
class RetryJobEvent:
    event_id: str


@dataclass(frozen=True, slots=True)
class CompleteJobEvent:
    event_id: str


@dataclass(frozen=True, slots=True)
class CancelJobEvent:
    event_id: str
    reason: str = ""


type IngestionEvent = (
    StartJobEvent
    | StageCompletedEvent
    | FailJobEvent
    | RetryJobEvent
    | CompleteJobEvent
    | CancelJobEvent
)


@dataclass(frozen=True, slots=True)
class DeadLetterPayload:
    acquisition_job_id: str
    failed_stage: str | None
    attempt_count: int
    error_code: str
    error_trace: str


@dataclass(frozen=True, slots=True)
class AcquisitionJobState:
    """Immutable snapshot of an ingestion job and its stage runs."""

    job_id: str
    status: AcquisitionJobStatus = AcquisitionJobStatus.QUEUED
    attempt_count: int = 0
    max_attempts: int = 3
    current_stage: str | None = None
    last_error_code: str | None = None
    last_error_detail: str | None = None
    stage_statuses: tuple[tuple[str, PipelineStageStatus], ...] = ()
    applied_event_ids: frozenset[str] = field(default_factory=frozenset)
    dead_letter: DeadLetterPayload | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be >= 0")

    def stage_map(self) -> dict[str, PipelineStageStatus]:
        return dict(self.stage_statuses)


def _with_stage(
    stage_statuses: tuple[tuple[str, PipelineStageStatus], ...],
    stage_name: str,
    new_status: PipelineStageStatus,
) -> tuple[tuple[str, PipelineStageStatus], ...]:
    stages = dict(stage_statuses)
    if stage_name in stages:
        validate_stage_transition(stages[stage_name], new_status)
    elif new_status != PipelineStageStatus.RUNNING:
        # New stages enter via RUNNING before reaching COMPLETED/FAILED/SKIPPED.
        validate_stage_transition(PipelineStageStatus.RUNNING, new_status)
    stages[stage_name] = new_status
    return tuple(sorted(stages.items()))


def apply_job_event(state: AcquisitionJobState, event: IngestionEvent) -> AcquisitionJobState:
    """
    Apply an ingestion event idempotently using explicit transition rules.

    Re-applying an already-seen `event.event_id` returns `state` unchanged.
    """
    if not event.event_id:
        raise IngestionTransitionError("MISSING_EVENT_ID", "event_id must be non-empty")

    if event.event_id in state.applied_event_ids:
        return state

    seen = state.applied_event_ids | {event.event_id}

    if isinstance(event, StartJobEvent):
        validate_job_transition(state.status, AcquisitionJobStatus.RUNNING)
        stages = _with_stage(state.stage_statuses, event.stage_name, PipelineStageStatus.RUNNING)
        return replace(
            state,
            status=AcquisitionJobStatus.RUNNING,
            attempt_count=state.attempt_count + 1,
            current_stage=event.stage_name,
            stage_statuses=stages,
            applied_event_ids=seen,
        )

    if isinstance(event, StageCompletedEvent):
        if state.status != AcquisitionJobStatus.RUNNING:
            raise IngestionTransitionError(
                "INVALID_JOB_TRANSITION",
                f"Cannot complete stage while job is in state {state.status.value}",
            )
        stages = _with_stage(state.stage_statuses, event.stage_name, PipelineStageStatus.COMPLETED)
        next_stage = event.next_stage
        if next_stage is not None:
            current_map = dict(stages)
            if current_map.get(next_stage) != PipelineStageStatus.COMPLETED:
                stages = _with_stage(stages, next_stage, PipelineStageStatus.RUNNING)
        return replace(
            state,
            current_stage=next_stage,
            stage_statuses=stages,
            applied_event_ids=seen,
        )

    if isinstance(event, FailJobEvent):
        validate_job_transition(state.status, AcquisitionJobStatus.FAILED)
        stages = _with_stage(state.stage_statuses, event.stage_name, PipelineStageStatus.FAILED)
        if state.attempt_count >= state.max_attempts:
            validate_job_transition(state.status, AcquisitionJobStatus.DEAD_LETTER)
            dlq = DeadLetterPayload(
                acquisition_job_id=state.job_id,
                failed_stage=event.stage_name,
                attempt_count=state.attempt_count,
                error_code=event.error_code,
                error_trace=event.error_detail or event.error_code,
            )
            return replace(
                state,
                status=AcquisitionJobStatus.DEAD_LETTER,
                current_stage=event.stage_name,
                last_error_code=event.error_code,
                last_error_detail=event.error_detail,
                stage_statuses=stages,
                applied_event_ids=seen,
                dead_letter=dlq,
            )
        return replace(
            state,
            status=AcquisitionJobStatus.FAILED,
            current_stage=event.stage_name,
            last_error_code=event.error_code,
            last_error_detail=event.error_detail,
            stage_statuses=stages,
            applied_event_ids=seen,
        )

    if isinstance(event, RetryJobEvent):
        if state.status != AcquisitionJobStatus.FAILED:
            raise IngestionTransitionError(
                "RETRY_NOT_ALLOWED",
                f"Retry is only allowed from FAILED state, got {state.status.value}",
            )
        if state.attempt_count >= state.max_attempts:
            validate_job_transition(state.status, AcquisitionJobStatus.DEAD_LETTER)
            dlq = DeadLetterPayload(
                acquisition_job_id=state.job_id,
                failed_stage=state.current_stage,
                attempt_count=state.attempt_count,
                error_code=state.last_error_code or "MAX_ATTEMPTS_EXCEEDED",
                error_trace=state.last_error_detail or "Exceeded max retry attempts",
            )
            return replace(
                state,
                status=AcquisitionJobStatus.DEAD_LETTER,
                applied_event_ids=seen,
                dead_letter=dlq,
            )
        validate_job_transition(state.status, AcquisitionJobStatus.RUNNING)
        # Keep already COMPLETED stages intact; reopen FAILED stages to RUNNING.
        reopened = dict(state.stage_statuses)
        for name, st in list(reopened.items()):
            if st == PipelineStageStatus.FAILED:
                validate_stage_transition(st, PipelineStageStatus.RUNNING)
                reopened[name] = PipelineStageStatus.RUNNING
        return replace(
            state,
            status=AcquisitionJobStatus.RUNNING,
            attempt_count=state.attempt_count + 1,
            stage_statuses=tuple(sorted(reopened.items())),
            applied_event_ids=seen,
        )

    if isinstance(event, CompleteJobEvent):
        validate_job_transition(state.status, AcquisitionJobStatus.COMPLETED)
        return replace(
            state,
            status=AcquisitionJobStatus.COMPLETED,
            current_stage=None,
            applied_event_ids=seen,
        )

    if isinstance(event, CancelJobEvent):
        validate_job_transition(state.status, AcquisitionJobStatus.CANCELLED)
        return replace(
            state,
            status=AcquisitionJobStatus.CANCELLED,
            last_error_detail=event.reason or state.last_error_detail,
            applied_event_ids=seen,
        )

    raise IngestionTransitionError("UNKNOWN_EVENT", f"Unsupported ingestion event: {type(event)!r}")
