"""Envelope of every internal event (ADR-012, spec section 5.1).

Only stdlib and pydantic may be imported here (app.contracts is a leaf package).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
"""Dotted lower-case names, as in the event catalog: `attempt.submitted`, `crawl.page.fetched`."""


def new_event_id() -> UUID:
    """A UUIDv7 (RFC 9562): ids created later sort later, like uuid_generate_v7() in SQL."""
    value = (time.time_ns() // 1_000_000) << 80 | int.from_bytes(os.urandom(10))
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return UUID(int=value)


class EventEnvelopeContract(BaseModel):
    """One event as producers write it to the outbox and consumers receive it from the relay.

    `event_id` is the idempotency key of every consumer (inbox_events). `correlation_id` ties all
    events of one user action together; `causation_id` is the `event_id` of the event whose
    handler produced this one (None for an event caused directly by a request).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=new_event_id)
    event_type: str = Field(max_length=100, pattern=EVENT_TYPE_PATTERN)
    schema_version: int = Field(default=1, ge=1)
    tenant_id: UUID
    aggregate_type: str = Field(min_length=1, max_length=60)
    aggregate_id: UUID
    correlation_id: str | None = Field(default=None, min_length=1, max_length=100)
    causation_id: UUID | None = None
    payload: dict[str, Any]
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
