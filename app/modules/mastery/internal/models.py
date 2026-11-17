"""SQLAlchemy ORM models for D06 Mastery & Adaptive IRT (`question_irt_parameters`, `mastery_estimates`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class QuestionIrtParameter(Base):
    """Calibrated IRT item parameters gated by 7 multi-criteria diagnostics."""

    __tablename__ = "question_irt_parameters"
    __table_args__ = (
        CheckConstraint(
            "model_type IN ('RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')",
            name="ck_question_irt_parameters_model_type",
        ),
        CheckConstraint("sample_size >= 150", name="ck_question_irt_parameters_sample_size"),
        CheckConstraint("learner_count >= 100", name="ck_question_irt_parameters_learner_count"),
        CheckConstraint("ability_spread_sd >= 0.600", name="ck_question_irt_parameters_ability_sd"),
        CheckConstraint("converged IS TRUE", name="ck_question_irt_parameters_converged"),
        CheckConstraint("infit_msq BETWEEN 0.700 AND 1.300", name="ck_question_irt_parameters_infit"),
        CheckConstraint("max_abs_dif <= 0.500", name="ck_question_irt_parameters_max_dif"),
        CheckConstraint("se_difficulty < 0.3000", name="ck_question_irt_parameters_se_b"),
    )

    question_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_versions.id", ondelete="CASCADE"), primary_key=True
    )
    calibration_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, server_default=UUID_V7)
    model_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    learner_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ability_spread_sd: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    converged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    infit_msq: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    outfit_msq: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    max_abs_dif: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False, server_default=text("0.000"))
    discrimination_a: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, server_default=text("1.0000"))
    difficulty_b: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    guessing_c: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False, server_default=text("0.0000"))
    step_thresholds_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    se_difficulty: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    diagnostics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    calibrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class MasteryEstimate(Base):
    """Per-learner, per-taxonomy-node mastery estimate with uncertainty gating."""

    __tablename__ = "mastery_estimates"
    __table_args__ = (
        CheckConstraint(
            "model_code IN ('RULE_WEIGHTED_L1', 'RASCH_1PL_L2', 'IRT_2PL_GPCM_L3', 'IRT_3PL_GPCM_L4')",
            name="ck_mastery_estimates_model_code",
        ),
        CheckConstraint("mastery_probability BETWEEN 0 AND 1", name="ck_mastery_estimates_prob"),
        CheckConstraint("status IN ('INSUFFICIENT_DATA', 'CALIBRATED')", name="ck_mastery_estimates_status"),
        UniqueConstraint(
            "membership_id",
            "taxonomy_node_id",
            "model_code",
            name="uq_mastery_estimates_member_node_model",
        ),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_mastery_estimates_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxonomy_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxonomy_nodes.id", ondelete="CASCADE"), nullable=False
    )
    model_code: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'RULE_WEIGHTED_L1'"))
    theta_estimate: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, server_default=text("0.000"))
    mastery_probability: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    standard_error: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False, server_default=text("0.500"))
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'INSUFFICIENT_DATA'"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
