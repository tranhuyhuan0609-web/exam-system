"""G02 audit service: the append-only audit trail and legal takedown cases, under the caller's tenant RLS.

* `record_audit_event` builds the row with `internal.diff` (redacted before/after, a reason of at least
  15 characters for sensitive actions) and inserts it into `audit_events`; the database refuses any later
  UPDATE or DELETE and any raw PII key in the snapshots.
* `execute_takedown_case` runs the `internal.takedown` state machine and, in ONE transaction, forces the
  target to TAKEDOWN / TAKEN_DOWN, closes the case, writes the `EXECUTE_TAKEDOWN` audit event and the
  `rights.takedown.executed` outbox event (search, CDN and the exam pool drop the target from there).
  Nothing is visible to anyone until that transaction commits, and a failure in any step undoes all of them.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.audit.internal.diff import AuditDiffError, build_audit_event_record
from app.modules.audit.internal.models import AuditEvent, TakedownCase
from app.modules.audit.internal.takedown import (
    TakedownCaseState,
    TakedownEntityType,
    TakedownStatus,
    execute_takedown,
)
from app.modules.outbox.public import enqueue
from app.modules.review.public import GovernancePolicyError, PublicationStatus, RightsStatus
from app.platform.pagination import CursorPage, build_cursor_page, clamp_limit, decode_cursor

TAKEDOWN_EXECUTED_EVENT = "rights.takedown.executed"
EXECUTE_TAKEDOWN_ACTION = "EXECUTE_TAKEDOWN"
OPEN_TAKEDOWN_ACTION = "OPEN_TAKEDOWN_CASE"
_AUDIT_ROUTE = "/api/v1/audit-logs"
_AUDIT_SORT_FIELD = "occurred_at"

# Where each target type lives and which status columns a takedown forces. QUESTION waits for C01: the
# V11.3 `questions` table does not exist yet (the legacy one has integer ids).
_TARGETS: dict[str, tuple[str, bool]] = {
    TakedownEntityType.CONTENT_ITEM: ("content_items", True),
    TakedownEntityType.SOURCE_DOCUMENT: ("source_documents", False),
}


class AuditNotFoundError(LookupError):
    """The case or its target does not exist or belongs to another tenant (same answer)."""


class TakedownConflictError(ValueError):
    """The case is not OPEN, or its target type cannot be executed yet; `code` says which."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnknownActorError(PermissionError):
    """The executing user is not a registered user (takedown_cases.executed_by references users)."""


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID
    reason: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    correlation_id: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TakedownCaseRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    complainant_name: str
    target_entity_type: str
    target_entity_id: uuid.UUID
    legal_reason: str
    status: str
    executed_by: uuid.UUID | None
    executed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TakedownExecution:
    case: TakedownCaseRecord
    audit_event: AuditEventRecord
    event_id: uuid.UUID


def _event(row: AuditEvent) -> AuditEventRecord:
    return AuditEventRecord(**{f: getattr(row, f) for f in AuditEventRecord.__dataclass_fields__})


def _case(row: TakedownCase) -> TakedownCaseRecord:
    return TakedownCaseRecord(**{f: getattr(row, f) for f in TakedownCaseRecord.__dataclass_fields__})


# --- audit trail ----------------------------------------------------------------------------------


async def record_audit_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    correlation_id: str,
    occurred_at: datetime | None = None,
) -> AuditEventRecord:
    """Append one audit event in the caller's transaction. Raises AuditDiffError for a missing context."""
    spec = build_audit_event_record(
        tenant_id=str(tenant_id),
        actor_id=str(actor_id),
        correlation_id=correlation_id,
        action=action,
        target_table=target_type,
        target_id=str(target_id),
        reason=reason,
        before_state=None if before is None else dict(before),
        after_state=None if after is None else dict(after),
        occurred_at=occurred_at or datetime.now(UTC),
    )
    row = (
        await session.execute(
            insert(AuditEvent)
            .values(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=spec.action,
                target_type=spec.target_table,
                target_id=target_id,
                reason=spec.reason,
                before_json=spec.before_json or None,
                after_json=spec.after_json or None,
                correlation_id=spec.correlation_id,
                occurred_at=spec.occurred_at,
            )
            .returning(AuditEvent)
        )
    ).scalar_one()
    return _event(row)


async def list_audit_events(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cursor_secret: str,
    limit: int | None = None,
    cursor: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    action: str | None = None,
) -> CursorPage[AuditEventRecord]:
    """The tenant's audit events, newest first, with a signed keyset cursor."""
    resolved = clamp_limit(limit)
    stmt = select(AuditEvent)
    if target_type:
        stmt = stmt.where(AuditEvent.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action.strip().upper())
    if cursor is not None:
        decoded = decode_cursor(
            cursor,
            secret=cursor_secret,
            allowed_sort_fields=(_AUDIT_SORT_FIELD,),
            expected_sort_field=_AUDIT_SORT_FIELD,
            expected_direction="desc",
            tenant_id=str(tenant_id),
            route=_AUDIT_ROUTE,
        )
        stmt = stmt.where(
            tuple_(AuditEvent.occurred_at, AuditEvent.id)
            < (datetime.fromisoformat(str(decoded.sort_value)), uuid.UUID(decoded.tie_id))
        )
    rows = (
        await session.scalars(stmt.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(resolved + 1))
    ).all()
    return build_cursor_page(
        [_event(r) for r in rows],
        limit=resolved,
        sort_field=_AUDIT_SORT_FIELD,
        direction="desc",
        key_extractor=lambda rec: (rec.occurred_at.isoformat(), str(rec.id)),
        secret=cursor_secret,
        tenant_id=str(tenant_id),
        route=_AUDIT_ROUTE,
    )


# --- takedown -------------------------------------------------------------------------------------


async def _target_statuses(session: AsyncSession, entity_type: str, entity_id: uuid.UUID) -> tuple[str, str | None]:
    """(rights_status, publication_status or None) of a target the tenant can see; AuditNotFoundError otherwise."""
    if entity_type not in _TARGETS:
        raise TakedownConflictError(
            "TARGET_TYPE_NOT_SUPPORTED_YET", f"{entity_type} takedowns need the C01 questions module (V11.3 table)"
        )
    table, has_publication = _TARGETS[entity_type]
    columns = "rights_status, publication_status" if has_publication else "rights_status, NULL"
    # table and columns come from the fixed map above, never from input
    row = (
        await session.execute(text(f"SELECT {columns} FROM {table} WHERE id = :id"), {"id": entity_id})
    ).one_or_none()
    if row is None:
        raise AuditNotFoundError(f"{entity_type.lower()} not found")
    return row[0], row[1]


async def create_takedown_case(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    complainant_name: str,
    target_entity_type: str,
    target_entity_id: uuid.UUID,
    legal_reason: str,
    correlation_id: str,
) -> TakedownCaseRecord:
    """Open a case against a target of the tenant (another tenant's target is not found), audited."""
    entity_type = TakedownEntityType(target_entity_type)
    if entity_type in _TARGETS:
        await _target_statuses(session, entity_type, target_entity_id)
    row = (
        await session.execute(
            insert(TakedownCase)
            .values(
                tenant_id=tenant_id,
                complainant_name=complainant_name.strip(),
                target_entity_type=entity_type.value,
                target_entity_id=target_entity_id,
                legal_reason=legal_reason.strip(),
            )
            .returning(TakedownCase)
        )
    ).scalar_one()
    await record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=OPEN_TAKEDOWN_ACTION,
        target_type="takedown_cases",
        target_id=row.id,
        reason=row.legal_reason,
        before=None,
        after={"status": row.status, "target_entity_type": row.target_entity_type,
               "target_entity_id": str(row.target_entity_id)},
        correlation_id=correlation_id,
    )  # fmt: skip
    return _case(row)


async def get_takedown_case(session: AsyncSession, case_id: uuid.UUID) -> TakedownCaseRecord | None:
    row = await session.scalar(
        select(TakedownCase).where(TakedownCase.id == case_id).execution_options(populate_existing=True)
    )
    return None if row is None else _case(row)


async def execute_takedown_case(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    executor_id: uuid.UUID,
    correlation_id: str,
    now: datetime | None = None,
) -> TakedownExecution:
    """Execute an OPEN case; every effect lands in the caller's single transaction (see module docstring)."""
    moment = now or datetime.now(UTC)
    case = await session.scalar(
        select(TakedownCase)
        .where(TakedownCase.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if case is None:
        raise AuditNotFoundError("takedown case not found")
    if not await session.scalar(text("SELECT EXISTS (SELECT 1 FROM users WHERE id = :u)"), {"u": executor_id}):
        raise UnknownActorError("the executor must be a registered user")
    rights, publication = await _target_statuses(session, case.target_entity_type, case.target_entity_id)
    state = TakedownCaseState(
        case_id=str(case.id),
        tenant_id=str(case.tenant_id),
        complainant_name=case.complainant_name,
        target_entity_type=TakedownEntityType(case.target_entity_type),
        target_entity_id=str(case.target_entity_id),
        legal_reason=case.legal_reason,
        status=TakedownStatus(case.status),
        executed_by=None if case.executed_by is None else str(case.executed_by),
    )
    try:
        result = execute_takedown(
            state,
            executor_id=str(executor_id),
            previous_rights_status=RightsStatus(rights),
            previous_publication_status=PublicationStatus(publication or PublicationStatus.DRAFT),
        )
    except GovernancePolicyError as exc:
        raise TakedownConflictError(exc.code, str(exc)) from exc

    table, has_publication = _TARGETS[case.target_entity_type]
    if has_publication:
        await session.execute(
            text(f"UPDATE {table} SET rights_status = 'TAKEDOWN', publication_status = 'TAKEN_DOWN' WHERE id = :id"),
            {"id": case.target_entity_id},
        )
        # Every published version too: the immutability trigger allows exactly this one change.
        await session.execute(
            text(
                "UPDATE content_versions SET status = 'TAKEN_DOWN' WHERE content_item_id = :id AND status = 'PUBLISHED'"
            ),
            {"id": case.target_entity_id},
        )
    else:
        await session.execute(
            text(f"UPDATE {table} SET rights_status = 'TAKEDOWN' WHERE id = :id"), {"id": case.target_entity_id}
        )
    updated = (
        await session.execute(
            update(TakedownCase)
            .where(TakedownCase.id == case.id)
            .values(status=result.case_state.status.value, executed_by=executor_id, executed_at=moment)
            .returning(TakedownCase)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    before, after = dict(result.audit_before_json), dict(result.audit_after_json)
    if not has_publication:  # a source document has no publication axis
        before.pop("publication_status", None)
        after.pop("publication_status", None)
    try:
        audit = await record_audit_event(
            session,
            tenant_id=case.tenant_id,
            actor_id=executor_id,
            action=EXECUTE_TAKEDOWN_ACTION,
            target_type=case.target_entity_type.lower(),
            target_id=case.target_entity_id,
            reason=case.legal_reason,
            before=before,
            after={**after, "takedown_case_id": str(case.id)},
            correlation_id=correlation_id,
            occurred_at=moment,
        )
    except AuditDiffError as exc:
        raise TakedownConflictError("AUDIT_REASON_REQUIRED", str(exc)) from exc

    envelope = EventEnvelopeContract(
        event_type=TAKEDOWN_EXECUTED_EVENT,
        tenant_id=case.tenant_id,
        aggregate_type="takedown_case",
        aggregate_id=case.id,
        correlation_id=correlation_id,
        payload={
            # chapter 5 catalog fields
            "tenant_id": str(case.tenant_id),
            "takedown_case_id": str(case.id),
            "target_entity_type": case.target_entity_type,
            "target_entity_id": str(case.target_entity_id),
            "executed_at": moment.isoformat(),
            # forced statuses, for the consumers
            "rights_status": result.forced_rights_status.value,
            "publication_status": result.forced_publication_status.value if has_publication else None,
        },
    )
    await enqueue(session, envelope)
    return TakedownExecution(_case(updated), audit, envelope.event_id)
