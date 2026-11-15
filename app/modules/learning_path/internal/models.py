"""SQLAlchemy ORM models for D02 Personalized Learning Path (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class LearningPlan(Base):
    """Per-learner personalized remediation & study plan."""

    __tablename__ = "learning_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')",
            name="ck_learning_plans_status",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_learning_plans_id_tenant"),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_learning_plans_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_framework_code: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        server_default=text("'THPTQG_2025'"),
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'ACTIVE'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class PlanTask(Base):
    """Ordered remediation or study step inside a learning plan with explicit rationale."""

    __tablename__ = "plan_tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('PREREQUISITE_REVIEW', 'READ_CONCEPT', 'PRACTICE_SET', 'ERROR_REMEDIATION')",
            name="ck_plan_tasks_task_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'SKIPPED')",
            name="ck_plan_tasks_status",
        ),
        ForeignKeyConstraint(
            ["learning_plan_id", "tenant_id"],
            ["learning_plans.id", "learning_plans.tenant_id"],
            ondelete="CASCADE",
            name="fk_plan_tasks_plan_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    learning_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxonomy_nodes.id"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale_text: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=text("'PENDING'"),
    )
