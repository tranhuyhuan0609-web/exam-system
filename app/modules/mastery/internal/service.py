"""Database service layer for D06 Progress, Mastery & Phased Adaptive IRT."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mastery.internal.irt_tier import (
    IrtCalibrationMetrics,
    select_irt_model_tier,
)
from app.modules.mastery.internal.models import MasteryEstimate, QuestionIrtParameter
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID

MIN_CALIBRATED_EVIDENCE_COUNT = 20
MAX_CALIBRATED_SE_THETA = Decimal("0.350")
_QUANT_3 = Decimal("0.001")


class MasteryValidationError(ValueError):
    """Raised when mastery recomputation or IRT calibration inputs fail validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MasteryNotFoundError(LookupError):
    """Raised when a taxonomy node or question version cannot be found inside tenant RLS."""


class MasteryScopeError(PermissionError):
    """Raised when a caller attempts to calibrate/recompute outside permitted tenant/member scope."""


@dataclass(frozen=True, slots=True)
class MasteryEvidenceItem:
    """Single item response observation used to estimate learner theta & mastery probability."""

    is_correct: bool
    question_version_id: uuid.UUID | None = None
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class QuestionCalibrationView:
    question_version_id: uuid.UUID
    persisted: bool
    selected_tier: str
    next_tier: str | None
    failed_next_tier_criteria: tuple[str, ...]
    calibration_run_id: uuid.UUID | None
    difficulty_b: Decimal | None
    discrimination_a: Decimal | None
    guessing_c: Decimal | None
    se_difficulty: Decimal | None
    calibrated_at: datetime | None


@dataclass(frozen=True, slots=True)
class MasteryEstimateView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    model_code: str
    status: str
    evidence_count: int
    standard_error: Decimal
    # Hidden (None) whenever status == 'INSUFFICIENT_DATA' so UI/API never displays misleading estimates.
    theta_estimate: Decimal | None
    mastery_probability: Decimal | None
    updated_at: datetime


def _to_estimate_view(row: MasteryEstimate) -> MasteryEstimateView:
    insufficient = row.status == "INSUFFICIENT_DATA" or row.evidence_count < MIN_CALIBRATED_EVIDENCE_COUNT or (
        Decimal(str(row.standard_error)) >= MAX_CALIBRATED_SE_THETA
    )
    return MasteryEstimateView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        taxonomy_node_id=row.taxonomy_node_id,
        model_code=row.model_code,
        status="INSUFFICIENT_DATA" if insufficient else "CALIBRATED",
        evidence_count=row.evidence_count,
        standard_error=Decimal(str(row.standard_error)),
        theta_estimate=None if insufficient else Decimal(str(row.theta_estimate)),
        mastery_probability=None if insufficient else Decimal(str(row.mastery_probability)),
        updated_at=row.updated_at,
    )


async def calibrate_question_irt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    question_version_id: uuid.UUID,
    metrics: IrtCalibrationMetrics,
    difficulty_b: Decimal,
    discrimination_a: Decimal = Decimal("1.0000"),
    guessing_c: Decimal = Decimal("0.0000"),
    outfit_msq: Decimal = Decimal("1.000"),
    step_thresholds: Sequence[float] = (),
    diagnostics: dict[str, Any] | None = None,
    approved_by: uuid.UUID | None = None,
) -> QuestionCalibrationView:
    """Evaluate 7-criterion IRT tier gate; persist only if eligible for RASCH_1PL_L2 or above."""
    qv_row = (
        await session.execute(
            text("SELECT id, tenant_id FROM question_versions WHERE id = :qvid"),
            {"qvid": question_version_id},
        )
    ).mappings().one_or_none()
    if qv_row is None:
        raise MasteryNotFoundError(f"Question version {question_version_id} not found")
    if qv_row["tenant_id"] not in (tenant_id, SYSTEM_PUBLIC_TENANT_ID):
        raise MasteryScopeError("Cannot calibrate question version belonging to another tenant")

    try:
        decision = select_irt_model_tier(metrics)
    except ValueError as exc:
        raise MasteryValidationError("INVALID_IRT_METRICS", str(exc)) from exc

    if decision.selected_tier == "RULE_WEIGHTED_L1":
        return QuestionCalibrationView(
            question_version_id=question_version_id,
            persisted=False,
            selected_tier=decision.selected_tier,
            next_tier=decision.next_tier,
            failed_next_tier_criteria=decision.failed_next_tier_criteria,
            calibration_run_id=None,
            difficulty_b=None,
            discrimination_a=None,
            guessing_c=None,
            se_difficulty=None,
            calibrated_at=None,
        )

    eff_a = Decimal("1.0000") if decision.selected_tier == "RASCH_1PL_L2" else discrimination_a
    # Beta(5, 17) prior mode / mean (~0.2273) when L4 3PL enabled cleanly within [0, 0.35]
    eff_c = guessing_c if decision.selected_tier == "IRT_3PL_GPCM_L4" else Decimal("0.0000")
    run_id = uuid.uuid4()
    values = {
        "question_version_id": question_version_id,
        "calibration_run_id": run_id,
        "model_type": decision.selected_tier,
        "sample_size": metrics.sample_size,
        "learner_count": metrics.learner_count,
        "ability_spread_sd": Decimal(str(round(metrics.ability_sd, 3))),
        "converged": metrics.converged,
        "infit_msq": Decimal(str(round(metrics.infit_msq, 3))),
        "outfit_msq": outfit_msq,
        "max_abs_dif": Decimal(str(round(metrics.max_abs_dif, 3))),
        "discrimination_a": eff_a,
        "difficulty_b": difficulty_b,
        "guessing_c": eff_c,
        "step_thresholds_json": list(step_thresholds),
        "se_difficulty": Decimal(str(round(metrics.se_b, 4))),
        "diagnostics_json": dict(
            diagnostics or {"failed_next_tier_criteria": list(decision.failed_next_tier_criteria)}
        ),
        "approved_by": approved_by,
    }
    await session.execute(
        pg_insert(QuestionIrtParameter)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[QuestionIrtParameter.question_version_id],
            set_={
                **{k: v for k, v in values.items() if k != "question_version_id"},
                "calibrated_at": text("NOW()"),
            },
        )
    )
    saved = (
        await session.execute(
            select(QuestionIrtParameter).where(QuestionIrtParameter.question_version_id == question_version_id)
        )
    ).scalar_one()

    return QuestionCalibrationView(
        question_version_id=saved.question_version_id,
        persisted=True,
        selected_tier=saved.model_type,
        next_tier=decision.next_tier,
        failed_next_tier_criteria=decision.failed_next_tier_criteria,
        calibration_run_id=saved.calibration_run_id,
        difficulty_b=Decimal(str(saved.difficulty_b)),
        discrimination_a=Decimal(str(saved.discrimination_a)),
        guessing_c=Decimal(str(saved.guessing_c)),
        se_difficulty=Decimal(str(saved.se_difficulty)),
        calibrated_at=saved.calibrated_at,
    )


async def _collect_db_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
) -> list[MasteryEvidenceItem]:
    items: list[MasteryEvidenceItem] = []

    has_gd = bool((await session.execute(text("SELECT to_regclass('public.grading_details')"))).scalar())
    if has_gd:
        rows = (
            await session.execute(
                text(
                    "SELECT gd.question_version_id, gd.is_correct "
                    "FROM grading_details gd "
                    "JOIN grades g ON g.id = gd.grade_id AND g.tenant_id = gd.tenant_id "
                    "JOIN attempts a ON a.id = g.attempt_id AND a.tenant_id = g.tenant_id "
                    "JOIN question_versions qv ON qv.id = gd.question_version_id "
                    "JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id "
                    "WHERE g.tenant_id = :tid AND g.is_current IS TRUE "
                    "AND a.membership_id = :mid AND q.primary_taxonomy_node_id = :nid"
                ),
                {"tid": tenant_id, "mid": membership_id, "nid": taxonomy_node_id},
            )
        ).mappings().all()
        items.extend(
            MasteryEvidenceItem(question_version_id=r["question_version_id"], is_correct=bool(r["is_correct"]))
            for r in rows
        )

    has_pr = bool((await session.execute(text("SELECT to_regclass('public.practice_responses')"))).scalar())
    if has_pr:
        rows = (
            await session.execute(
                text(
                    "SELECT pr.question_version_id, pr.is_correct "
                    "FROM practice_responses pr "
                    "JOIN practice_sessions ps ON ps.id = pr.practice_session_id AND ps.tenant_id = pr.tenant_id "
                    "JOIN question_versions qv ON qv.id = pr.question_version_id "
                    "JOIN questions q ON q.id = qv.question_id AND q.tenant_id = qv.tenant_id "
                    "WHERE ps.tenant_id = :tid AND ps.membership_id = :mid "
                    "AND q.primary_taxonomy_node_id = :nid"
                ),
                {"tid": tenant_id, "mid": membership_id, "nid": taxonomy_node_id},
            )
        ).mappings().all()
        items.extend(
            MasteryEvidenceItem(question_version_id=r["question_version_id"], is_correct=bool(r["is_correct"]))
            for r in rows
        )

    return items


async def recompute_learner_mastery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    observations: Sequence[MasteryEvidenceItem] | None = None,
    override_se_theta: Decimal | None = None,
) -> MasteryEstimateView:
    """Recompute learner theta/mastery; enforces `< 20` items or `SE(theta) >= 0.35` -> INSUFFICIENT_DATA."""
    tax_row = (
        await session.execute(
            text("SELECT id FROM taxonomy_nodes WHERE id = :nid"),
            {"nid": taxonomy_node_id},
        )
    ).one_or_none()
    if tax_row is None:
        raise MasteryNotFoundError(f"Taxonomy node {taxonomy_node_id} not found")

    if override_se_theta is not None and (override_se_theta < Decimal("0") or override_se_theta > Decimal("9.999")):
        raise MasteryValidationError("INVALID_SE_THETA", "override_se_theta must be between 0 and 9.999")

    evidence = list(observations) if observations is not None else await _collect_db_evidence(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        taxonomy_node_id=taxonomy_node_id,
    )

    qv_ids = [item.question_version_id for item in evidence if item.question_version_id is not None]
    irt_by_qv: dict[uuid.UUID, QuestionIrtParameter] = {}
    if qv_ids:
        irt_rows = (
            await session.execute(
                select(QuestionIrtParameter).where(QuestionIrtParameter.question_version_id.in_(qv_ids))
            )
        ).scalars().all()
        irt_by_qv = {row.question_version_id: row for row in irt_rows}

    tier_order = ("RULE_WEIGHTED_L1", "RASCH_1PL_L2", "IRT_2PL_GPCM_L3", "IRT_3PL_GPCM_L4")
    highest_idx = 0
    weighted_correct = 0.0
    weighted_total = 0.0
    info_sum = 0.0

    for item in evidence:
        if item.weight <= 0 or not math.isfinite(item.weight):
            raise MasteryValidationError("INVALID_ITEM_WEIGHT", "Observation weight must be finite and positive")
        irt = irt_by_qv.get(item.question_version_id) if item.question_version_id else None
        if irt is not None:
            tier_idx = tier_order.index(irt.model_type)
            highest_idx = max(highest_idx, tier_idx)
            a_val = float(irt.discrimination_a)
            weighted_total += item.weight * a_val
            if item.is_correct:
                weighted_correct += item.weight * a_val
            # Conservative Fisher information proxy bounded away from degenerate inflation
            info_sum += max(0.25, min(1.5, a_val * a_val * 0.45))
        else:
            weighted_total += item.weight
            if item.is_correct:
                weighted_correct += item.weight
            info_sum += 0.45

    model_code = tier_order[highest_idx]
    evidence_count = len(evidence)

    if override_se_theta is not None:
        se_theta = override_se_theta.quantize(_QUANT_3)
    elif evidence_count == 0 or info_sum <= 0:
        se_theta = Decimal("0.999")
    else:
        raw_se = min(0.999, 1.0 / math.sqrt(info_sum))
        se_theta = Decimal(f"{raw_se:.3f}")

    if evidence_count == 0:
        prob = Decimal("0.500")
        theta = Decimal("0.000")
    else:
        # Shrink slightly away from 0/1 to avoid infinite logit while reflecting empirical performance
        smoothed = (weighted_correct + 0.5) / (weighted_total + 1.0)
        smoothed = max(0.01, min(0.99, smoothed))
        prob = Decimal(f"{smoothed:.3f}")
        logit = max(-4.0, min(4.0, math.log(smoothed / (1.0 - smoothed))))
        theta = Decimal(f"{logit:.3f}")

    status_val = (
        "INSUFFICIENT_DATA"
        if evidence_count < MIN_CALIBRATED_EVIDENCE_COUNT or se_theta >= MAX_CALIBRATED_SE_THETA
        else "CALIBRATED"
    )
    persisted_prob = Decimal("0.500") if status_val == "INSUFFICIENT_DATA" else prob
    persisted_theta = Decimal("0.000") if status_val == "INSUFFICIENT_DATA" else theta

    stmt = (
        pg_insert(MasteryEstimate)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            membership_id=membership_id,
            taxonomy_node_id=taxonomy_node_id,
            model_code=model_code,
            theta_estimate=persisted_theta,
            mastery_probability=persisted_prob,
            standard_error=se_theta,
            evidence_count=evidence_count,
            status=status_val,
        )
        .on_conflict_do_update(
            index_elements=[
                MasteryEstimate.membership_id,
                MasteryEstimate.taxonomy_node_id,
                MasteryEstimate.model_code,
            ],
            set_={
                "theta_estimate": persisted_theta,
                "mastery_probability": persisted_prob,
                "standard_error": se_theta,
                "evidence_count": evidence_count,
                "status": status_val,
                "updated_at": text("NOW()"),
            },
        )
        .returning(MasteryEstimate)
    )
    row = (await session.execute(stmt)).scalar_one()
    return _to_estimate_view(row)


async def list_learner_mastery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID | None = None,
) -> tuple[MasteryEstimateView, ...]:
    """List mastery estimates for a learner within tenant scope, hiding INSUFFICIENT_DATA scores."""
    stmt = (
        select(MasteryEstimate)
        .where(
            MasteryEstimate.tenant_id == tenant_id,
            MasteryEstimate.membership_id == membership_id,
        )
        .order_by(MasteryEstimate.updated_at.desc(), MasteryEstimate.taxonomy_node_id)
    )
    if taxonomy_node_id is not None:
        stmt = stmt.where(MasteryEstimate.taxonomy_node_id == taxonomy_node_id)
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(_to_estimate_view(row) for row in rows)
