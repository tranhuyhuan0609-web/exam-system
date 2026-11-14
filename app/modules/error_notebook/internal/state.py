"""Pure state machine for `error_notes` remediation lifecycle (module D05).

Statuses and fields match `error_notes` in `chapters/04_postgresql_ddl_rls_grants.sql`:
- status: OPEN | IN_REMEDIATION | RESOLVED
- wrong_count, consecutive_correct_count, last_wrong_at, resolved_at
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ErrorNoteStatus = Literal["OPEN", "IN_REMEDIATION", "RESOLVED"]
RemediationTaskStatus = Literal["ASSIGNED", "COMPLETED"]

REQUIRED_CONSECUTIVE_CORRECT = 2


@dataclass(frozen=True, slots=True)
class ErrorNoteState:
    """Immutable state snapshot mirroring an `error_notes` database row."""

    last_wrong_at: datetime
    status: ErrorNoteStatus = "OPEN"
    wrong_count: int = 1
    consecutive_correct_count: int = 0
    resolved_at: datetime | None = None


def _is_tz_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.utcoffset() is not None


def apply_attempt(
    state: ErrorNoteState,
    is_correct: bool,
    at: datetime,
) -> ErrorNoteState:
    """Transition an error note state after one remediation or practice attempt.

    - Two consecutive correct answers (`consecutive_correct_count >= 2`) resolve the note (`RESOLVED`).
    - A single wrong answer resets `consecutive_correct_count` to 0, increments `wrong_count`,
      clears `resolved_at`, and reopens a `RESOLVED` note back to `OPEN`.
    """
    if state.wrong_count < 0 or state.consecutive_correct_count < 0:
        raise ValueError("counts must be non-negative")
    if not _is_tz_aware(at):
        raise ValueError("at must be timezone-aware")
    if not _is_tz_aware(state.last_wrong_at):
        raise ValueError("state.last_wrong_at must be timezone-aware")
    if at < state.last_wrong_at:
        raise ValueError("at cannot be earlier than state.last_wrong_at")

    if is_correct:
        next_consecutive = state.consecutive_correct_count + 1
        if next_consecutive >= REQUIRED_CONSECUTIVE_CORRECT:
            resolved_timestamp = (
                state.resolved_at if state.status == "RESOLVED" and state.resolved_at is not None else at
            )
            return ErrorNoteState(
                status="RESOLVED",
                wrong_count=state.wrong_count,
                consecutive_correct_count=next_consecutive,
                last_wrong_at=state.last_wrong_at,
                resolved_at=resolved_timestamp,
            )

        return ErrorNoteState(
            status="IN_REMEDIATION",
            wrong_count=state.wrong_count,
            consecutive_correct_count=next_consecutive,
            last_wrong_at=state.last_wrong_at,
            resolved_at=None,
        )

    next_status: ErrorNoteStatus = "OPEN" if state.status in ("OPEN", "RESOLVED") else "IN_REMEDIATION"
    return ErrorNoteState(
        status=next_status,
        wrong_count=state.wrong_count + 1,
        consecutive_correct_count=0,
        last_wrong_at=at,
        resolved_at=None,
    )
