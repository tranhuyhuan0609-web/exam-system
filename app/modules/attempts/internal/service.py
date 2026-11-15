"""C06 attempts service: Fast Autosave (H-04, spec section 6.4) and resync, on real rows, under the caller's RLS.

Runs as `ulp_exam_session_rw`. One save is one transaction, in this order:
1. the attempt row `FOR SHARE`: saves of one attempt run side by side, but a submit (`FOR UPDATE` in C05) waits for
   them and they wait for it, so no save lands after a submit has been decided (submit race protection);
2. a transaction advisory lock on (attempt, question), then the response row `FOR UPDATE`: two saves of one answer
   are serialized, also the very first two, when no response row exists yet;
3. the registry entry of the Idempotency-Key, then the pure decision `process_autosave_idempotent_first` (fixed
   check order: registry, payload hash, attempt status, deadline + grace, expected_response_version, client_sequence);
4. on COMMITTED: the registry row first (`ON CONFLICT DO NOTHING`, so a key raced onto another question of the
   attempt is caught), then the response row, then the append-only revision.
Nothing is written for any other outcome. A key is unique per attempt: reusing it for another question, or with
another payload, is `PAYLOAD_HASH_MISMATCH`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.autosave import AutosaveRequestContract
from app.modules.attempts.internal.autosave import (
    DEFAULT_NETWORK_GRACE_SECONDS,
    AttemptState,
    AutosaveResult,
    ResponseState,
    StoredAutosave,
    process_autosave_idempotent_first,
)
from app.modules.attempts.internal.models import AutosaveIdempotencyRegistry, Response, ResponseRevision


class AttemptNotFoundError(LookupError):
    """The attempt or question does not exist, is another tenant's, another student's, or not on the paper."""


@dataclass(frozen=True, slots=True)
class SavedResponse:
    question_version_id: uuid.UUID
    revision_no: int
    current_sequence: int
    last_idempotency_key: str | None
    response_payload: dict[str, Any]
    is_flagged: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SyncState:
    attempt_id: uuid.UUID
    status: str
    deadline_at: datetime
    server_time: datetime
    responses: tuple[SavedResponse, ...]


async def _own_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, membership_id: uuid.UUID, *, share_lock: bool
) -> Any:
    lock = " FOR SHARE" if share_lock else ""
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, membership_id, status, deadline_at, question_order_ids "
                f"FROM attempts WHERE id = :a{lock}"
            ),
            {"a": attempt_id},
        )
    ).one_or_none()
    if row is None or row.membership_id != membership_id:
        raise AttemptNotFoundError("attempt not found")
    return row


async def save_response(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    question_version_id: uuid.UUID,
    *,
    membership_id: uuid.UUID,
    request: AutosaveRequestContract,
    now: datetime | None = None,
    network_grace_seconds: int = DEFAULT_NETWORK_GRACE_SECONDS,
) -> AutosaveResult:
    """Apply one autosave of the caller's answer to one question (see the module docstring for the order)."""
    moment = now or datetime.now(UTC)
    attempt = await _own_attempt(session, attempt_id, membership_id, share_lock=True)
    if question_version_id not in set(attempt.question_order_ids):
        raise AttemptNotFoundError("the question is not on this attempt's paper")

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"autosave:{attempt_id}:{question_version_id}"},
    )
    response = await session.scalar(
        select(Response)
        .where(Response.attempt_id == attempt_id, Response.question_version_id == question_version_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    entry = await session.scalar(
        select(AutosaveIdempotencyRegistry).where(
            AutosaveIdempotencyRegistry.tenant_id == attempt.tenant_id,
            AutosaveIdempotencyRegistry.attempt_id == attempt_id,
            AutosaveIdempotencyRegistry.idempotency_key == request.idempotency_key,
        )
    )
    if entry is not None and (response is None or entry.response_id != response.id):
        return AutosaveResult(422, "PAYLOAD_HASH_MISMATCH")  # the key was spent on another question

    result = process_autosave_idempotent_first(
        idempotency_key=request.idempotency_key,
        client_sequence=request.client_sequence,
        expected_response_version=request.expected_response_version,
        payload_hash=request.payload_hash,
        response_payload=request.response_payload,
        attempt=AttemptState(status=attempt.status, deadline_at=attempt.deadline_at),
        response=ResponseState()
        if response is None
        else ResponseState(
            current_revision_no=response.current_revision_no, current_sequence=response.current_sequence
        ),
        registry_entry=None
        if entry is None
        else StoredAutosave(
            payload_hash=entry.payload_sha256,
            client_sequence=entry.client_sequence,
            revision_no=entry.revision_no,
            recorded_at=entry.recorded_at,
        ),
        now_utc=moment,
        network_grace_seconds=network_grace_seconds,
    )
    if result.code != "COMMITTED":
        return result

    assert result.saved_response_version is not None and result.committed_sequence is not None
    response_id = (
        response.id if response is not None else (await session.execute(text("SELECT uuid_generate_v7()"))).scalar_one()
    )
    claimed = (
        await session.execute(
            pg_insert(AutosaveIdempotencyRegistry)
            .values(
                tenant_id=attempt.tenant_id,
                attempt_id=attempt_id,
                idempotency_key=request.idempotency_key,
                response_id=response_id,
                revision_no=result.saved_response_version,
                client_sequence=result.committed_sequence,
                payload_sha256=request.payload_hash,
                recorded_at=moment,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "attempt_id", "idempotency_key"])
            .returning(AutosaveIdempotencyRegistry.response_id)
        )
    ).scalar_one_or_none()
    if claimed is None:  # the same key committed meanwhile on another question of the attempt
        return AutosaveResult(422, "PAYLOAD_HASH_MISMATCH")

    if response is None:
        await session.execute(
            insert(Response).values(
                id=response_id,
                tenant_id=attempt.tenant_id,
                attempt_id=attempt_id,
                question_version_id=question_version_id,
                current_sequence=result.committed_sequence,
                current_revision_no=result.saved_response_version,
                last_idempotency_key=request.idempotency_key,
                response_payload=request.response_payload,
                is_flagged=request.is_flagged,
                updated_at=moment,
            )
        )
    else:
        await session.execute(
            update(Response)
            .where(Response.id == response.id)
            .values(
                current_sequence=result.committed_sequence,
                current_revision_no=result.saved_response_version,
                last_idempotency_key=request.idempotency_key,
                response_payload=request.response_payload,
                is_flagged=request.is_flagged,
                updated_at=moment,
            )
        )
    await session.execute(
        insert(ResponseRevision).values(
            tenant_id=attempt.tenant_id,
            response_id=response_id,
            revision_no=result.saved_response_version,
            client_sequence=result.committed_sequence,
            idempotency_key=request.idempotency_key,
            payload_sha256=request.payload_hash,
            payload_json=request.response_payload,
            recorded_at=moment,
        )
    )
    return result


async def get_sync_state(
    session: AsyncSession, attempt_id: uuid.UUID, *, membership_id: uuid.UUID, now: datetime | None = None
) -> SyncState:
    """Everything the browser needs to resynchronise its offline queue: the server's version of every answer."""
    attempt = await _own_attempt(session, attempt_id, membership_id, share_lock=False)
    rows = (
        await session.scalars(
            select(Response)
            .where(Response.attempt_id == attempt_id)
            .order_by(Response.question_version_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    return SyncState(
        attempt_id=attempt.id,
        status=attempt.status,
        deadline_at=attempt.deadline_at,
        server_time=now or datetime.now(UTC),
        responses=tuple(
            SavedResponse(
                question_version_id=r.question_version_id,
                revision_no=r.current_revision_no,
                current_sequence=r.current_sequence,
                last_idempotency_key=r.last_idempotency_key,
                response_payload=dict(r.response_payload),
                is_flagged=r.is_flagged,
                updated_at=r.updated_at,
            )
            for r in rows
        ),
    )
