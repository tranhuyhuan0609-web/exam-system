"""SQLAlchemy ORM model for C05 delivery (spec chapter 4): one attempt of a student at a paper.

The order of the questions and the permutation of the options are stored with the attempt, so a reload, another
device, grading and an appeal all see exactly what the student saw. `assignment_id` is a plain UUID until F02.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

ATTEMPT_STATUSES = ("IN_PROGRESS", "SUBMITTED", "AUTO_SUBMITTED", "GRADED", "VOIDED")


class Attempt(Base):
    """A student's sitting: seed, frozen question order and option permutations, deadline and submission."""

    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'VOIDED')",
            name="ck_attempts_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_attempts_id_tenant_id"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_attempts_membership_tenant",
        ),
        Index("ix_attempts_membership_status", "tenant_id", "membership_id", "status"),
        Index(
            "ix_attempts_auto_submit_scan",
            "status",
            "deadline_at",
            postgresql_where=text("status = 'IN_PROGRESS'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_versions.id", ondelete="RESTRICT"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="IN_PROGRESS")
    attempt_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_order_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    option_permutations_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
