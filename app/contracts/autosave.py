"""Canonical autosave contract (guardrail H-04, spec section 5.2).

Only stdlib and pydantic may be imported here (app.contracts is a leaf package).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AutosaveRequestContract(BaseModel):
    """Autosave request with optimistic concurrency and payload hash.

    ``expected_response_version`` is required: no default, never Optional.
    A first save on a question (no response row yet) sends 0.
    """

    model_config = ConfigDict(extra="forbid")

    client_sequence: int = Field(ge=1)
    expected_response_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    payload_hash: str = Field(min_length=64, max_length=64)
    response_payload: dict[str, Any]
    is_flagged: bool = False


class AutosaveResponseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["COMMITTED", "IDEMPOTENT_REPLAY"]
    attempt_id: UUID
    question_version_id: UUID
    committed_sequence: int
    saved_response_version: int
    server_timestamp: datetime


def canonical_json(payload: Any) -> str:
    """Canonical JSON shared with the browser (frontend ``canonicalJson``).

    Equivalent to ``json.dumps(payload, sort_keys=True, separators=(",", ":"),
    ensure_ascii=False)``. ``ensure_ascii=False`` is chosen because the spec
    (section 6.4 ``compute_payload_sha256`` and the 6.9 TypeScript docstring)
    mandates it and JS ``JSON.stringify`` also emits non-ASCII characters
    verbatim.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_hash(payload: Any) -> str:
    """SHA-256 hex digest of the UTF-8 canonical JSON of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
