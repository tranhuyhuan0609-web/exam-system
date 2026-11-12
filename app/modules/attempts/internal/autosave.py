"""Pure decision function for exam-room autosave (H-04, spec section 6.4).

No I/O. The caller (module C06, later) reads the attempt, response row and
idempotency registry entry under ``SELECT ... FOR UPDATE`` and persists the
outcome itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.contracts.autosave import compute_payload_hash

DEFAULT_NETWORK_GRACE_SECONDS = 5


@dataclass(frozen=True)
class StoredAutosave:
    """Row of ``autosave_idempotency_registry`` for the incoming key."""

    payload_hash: str
    client_sequence: int
    revision_no: int
    recorded_at: datetime


@dataclass(frozen=True)
class AttemptState:
    status: str
    deadline_at: datetime


@dataclass(frozen=True)
class ResponseState:
    """Current response row; revision 0 / sequence 0 when none exists yet."""

    current_revision_no: int = 0
    current_sequence: int = 0


@dataclass(frozen=True)
class AutosaveResult:
    http_status: int
    code: str  # COMMITTED / IDEMPOTENT_REPLAY or an error code
    committed_sequence: int | None = None
    saved_response_version: int | None = None
    server_timestamp: datetime | None = None
    current_response_version: int | None = None
    current_sequence: int | None = None
    attempt_status: str | None = None
    deadline_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.http_status == 200


def process_autosave_idempotent_first(
    *,
    idempotency_key: str,
    client_sequence: int,
    expected_response_version: int,
    payload_hash: str,
    response_payload: dict[str, Any],
    attempt: AttemptState,
    response: ResponseState,
    registry_entry: StoredAutosave | None,
    now_utc: datetime | None = None,
    network_grace_seconds: int = DEFAULT_NETWORK_GRACE_SECONDS,
) -> AutosaveResult:
    """Decide the outcome of one autosave request. Check order is fixed.

    ``registry_entry`` is the registry row for ``idempotency_key`` (or None).
    """
    now_utc = now_utc or datetime.now(UTC)

    # 1. Idempotency registry first.
    if registry_entry is not None:
        if registry_entry.payload_hash != payload_hash:
            return AutosaveResult(422, "PAYLOAD_HASH_MISMATCH")
        return AutosaveResult(
            200,
            "IDEMPOTENT_REPLAY",
            committed_sequence=registry_entry.client_sequence,
            saved_response_version=registry_entry.revision_no,
            server_timestamp=registry_entry.recorded_at,
        )

    # 2. The client hash must match the payload actually received.
    if payload_hash != compute_payload_hash(response_payload):
        return AutosaveResult(422, "PAYLOAD_HASH_MISMATCH")

    # 3. Submit race protection.
    if attempt.status != "IN_PROGRESS":
        return AutosaveResult(409, "ATTEMPT_ALREADY_SUBMITTED_OR_CLOSED", attempt_status=attempt.status)

    # 4. Server-side deadline.
    if now_utc > attempt.deadline_at + timedelta(seconds=network_grace_seconds):
        return AutosaveResult(403, "ATTEMPT_DEADLINE_EXPIRED", deadline_at=attempt.deadline_at)

    # 5. Optimistic concurrency.
    if expected_response_version != response.current_revision_no:
        return AutosaveResult(
            409,
            "OPTIMISTIC_VERSION_CONFLICT",
            current_response_version=response.current_revision_no,
            current_sequence=response.current_sequence,
        )

    # 6. Monotonic client sequence.
    if client_sequence <= response.current_sequence:
        return AutosaveResult(409, "STALE_CLIENT_SEQUENCE", current_sequence=response.current_sequence)

    # 7. Commit.
    return AutosaveResult(
        200,
        "COMMITTED",
        committed_sequence=client_sequence,
        saved_response_version=response.current_revision_no + 1,
        server_timestamp=now_utc,
    )
