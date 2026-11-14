"""SQLAlchemy ORM model for C02 quality (spec chapter 4): verification runs.

`question_version_id` stays a plain UUID until C01 creates `question_versions` (TASK-17 adds the foreign key).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class VerificationRun(Base):
    """One independent check of a question (or candidate): what the verifier computed and whether it matched."""

    __tablename__ = "verification_runs"
    __table_args__ = (
        CheckConstraint(
            "verifier_type IN ('DETERMINISTIC_RULE', 'SYMPY_SOLVER', 'LLM_BLIND_SOLVER', 'HUMAN_EXPERT')",
            name="ck_verification_runs_verifier_type",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "tenant_id"],
            ["question_candidates.id", "question_candidates.tenant_id"],
            ondelete="CASCADE",
            name="fk_verification_runs_candidate_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    question_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verifier_type: Mapped[str] = mapped_column(String(40), nullable=False)
    model_or_tool_version: Mapped[str] = mapped_column(String(80), nullable=False)
    computed_answer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_match_with_source: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    findings_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
