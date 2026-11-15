"""Database service layer for E05 Explainable Next-Step Recommendations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.recommendations.internal.models import RecommendationRecord
from app.modules.recommendations.internal.recommendations import (
    LearnerSignalNode,
    RankedRecommendation,
    RecommendationActionType,
    RecommendationEvidence,
    RecommendationFeedbackKind,
    apply_recommendation_feedback,
    rank_next_step_recommendations,
)
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID

_DEFAULT_ERROR_MASTERY = 0.50
_DEFAULT_NEUTRAL_MASTERY = 1.00


class RecommendationValidationError(ValueError):
    """Raised when recommendation query parameters or feedback payload are invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RecommendationNotFoundError(LookupError):
    """Raised when the learner membership or recommendation row is not found inside tenant RLS."""


@dataclass(frozen=True, slots=True)
class RecommendationView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    recommended_action_type: str
    target_entity_id: uuid.UUID
    title: str
    expected_gain_score: float
    evidence_json: dict[str, Any]
    status: str
    feedback: str | None
    hidden: bool
    created_at: datetime


async def _table_exists(session: AsyncSession, qualified_name: str) -> bool:
    result = await session.execute(
        text("SELECT to_regclass(:name) IS NOT NULL"),
        {"name": qualified_name},
    )
    return bool(result.scalar())


async def _assert_membership_in_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> None:
    row = (
        await session.execute(
            text("SELECT 1 FROM memberships WHERE id = :membership_id AND tenant_id = :tenant_id"),
            {"membership_id": membership_id, "tenant_id": tenant_id},
        )
    ).first()
    if row is None:
        raise RecommendationNotFoundError(
            f"membership {membership_id} was not found in tenant {tenant_id}"
        )


def _to_view(row: RecommendationRecord) -> RecommendationView:
    ev = dict(row.evidence_json or {})
    feedback_raw = ev.get("feedback")
    feedback = str(feedback_raw) if feedback_raw is not None else None
    hidden = bool(ev.get("hidden", False)) or row.status == "DISMISSED"
    title = str(ev.get("title") or row.target_entity_id)
    score = float(ev.get("expected_gain_score", 0.0))
    return RecommendationView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        recommended_action_type=row.recommended_action_type,
        target_entity_id=row.target_entity_id,
        title=title,
        expected_gain_score=score,
        evidence_json=ev,
        status=row.status,
        feedback=feedback,
        hidden=hidden,
        created_at=row.created_at,
    )


async def _build_learner_signal_nodes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> tuple[LearnerSignalNode, ...]:
    # 1. Load active taxonomy nodes visible to tenant (own + SYSTEM_PUBLIC).
    tax_rows = (
        await session.execute(
            text(
                """
                SELECT tn.id, COALESCE(tnv.name, tn.code) AS title
                FROM taxonomy_nodes tn
                LEFT JOIN taxonomy_node_versions tnv
                    ON tnv.taxonomy_node_id = tn.id
                    AND tnv.version_no = tn.current_version_no
                WHERE tn.is_active = TRUE
                  AND tn.tenant_id IN (:tenant_id, :system_public_tenant_id)
                ORDER BY tn.id
                """
            ),
            {
                "tenant_id": tenant_id,
                "system_public_tenant_id": SYSTEM_PUBLIC_TENANT_ID,
            },
        )
    ).all()
    titles_by_id: dict[str, str] = {str(r[0]): str(r[1]) for r in tax_rows}
    if not titles_by_id:
        return ()

    # 2. Load mastery_estimates if table exists.
    mastery_by_id: dict[str, float] = {}
    if await _table_exists(session, "public.mastery_estimates"):
        m_rows = (
            await session.execute(
                text(
                    """
                    SELECT taxonomy_node_id, mastery_probability
                    FROM mastery_estimates
                    WHERE tenant_id = :tenant_id
                      AND membership_id = :membership_id
                    ORDER BY updated_at DESC
                    """
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).all()
        for tax_id, prob in m_rows:
            key = str(tax_id)
            if key not in mastery_by_id:
                mastery_by_id[key] = float(prob)

    # 3. Load unresolved error_notes if table exists.
    errors_by_node: dict[str, list[str]] = {}
    if await _table_exists(session, "public.error_notes"):
        e_rows = (
            await session.execute(
                text(
                    """
                    SELECT primary_taxonomy_node_id, question_version_id
                    FROM error_notes
                    WHERE tenant_id = :tenant_id
                      AND membership_id = :membership_id
                      AND status IN ('OPEN', 'IN_REMEDIATION')
                      AND primary_taxonomy_node_id IS NOT NULL
                    ORDER BY last_wrong_at DESC, question_version_id ASC
                    """
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).all()
        for tax_id, qv_id in e_rows:
            key = str(tax_id)
            bucket = errors_by_node.setdefault(key, [])
            qv_str = str(qv_id)
            if qv_str not in bucket:
                bucket.append(qv_str)

    # 4. Load prerequisite edges if knowledge_edges exists (`source` is prereq of `target`).
    prereqs_by_target: dict[str, list[str]] = {}
    if await _table_exists(session, "public.knowledge_edges"):
        k_rows = (
            await session.execute(
                text(
                    """
                    SELECT source_node_id, target_node_id
                    FROM knowledge_edges
                    WHERE edge_type = 'PREREQUISITE'
                      AND tenant_id IN (:tenant_id, :system_public_tenant_id)
                    ORDER BY graph_version DESC, source_node_id ASC
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "system_public_tenant_id": SYSTEM_PUBLIC_TENANT_ID,
                },
            )
        ).all()
        for src_id, dst_id in k_rows:
            src_str, dst_str = str(src_id), str(dst_id)
            if src_str in titles_by_id and dst_str in titles_by_id and src_str != dst_str:
                prereqs = prereqs_by_target.setdefault(dst_str, [])
                if src_str not in prereqs:
                    prereqs.append(src_str)

    # Include any node with mastery signal, unresolved errors, or prerequisite participation.
    candidate_ids = set(mastery_by_id) | set(errors_by_node)
    for dst_id, prereqs in prereqs_by_target.items():
        if dst_id in candidate_ids:
            candidate_ids.update(prereqs)

    signal_nodes: list[LearnerSignalNode] = []
    for node_id in sorted(candidate_ids):
        if node_id not in titles_by_id:
            continue
        if node_id in mastery_by_id:
            est = mastery_by_id[node_id]
        elif node_id in errors_by_node:
            est = _DEFAULT_ERROR_MASTERY
        else:
            est = _DEFAULT_NEUTRAL_MASTERY
        signal_nodes.append(
            LearnerSignalNode(
                taxonomy_node_id=node_id,
                title=titles_by_id[node_id],
                mastery_estimate=est,
                unresolved_question_ids=tuple(errors_by_node.get(node_id, ())),
                prerequisite_ids=tuple(prereqs_by_target.get(node_id, ())),
            )
        )
    return tuple(signal_nodes)


async def list_learner_recommendations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    top_k: int = 3,
    refresh: bool = False,
) -> tuple[RecommendationView, ...]:
    """Return active explainable recommendations for learner, computing and persisting Top-K if needed."""
    if isinstance(top_k, bool) or top_k <= 0 or top_k > 10:
        raise RecommendationValidationError("INVALID_TOP_K", "top_k must be between 1 and 10")

    await _assert_membership_in_tenant(session, tenant_id=tenant_id, membership_id=membership_id)

    existing_rows = list(
        (
            await session.execute(
                select(RecommendationRecord)
                .where(
                    RecommendationRecord.tenant_id == tenant_id,
                    RecommendationRecord.membership_id == membership_id,
                )
                .order_by(RecommendationRecord.created_at.asc(), RecommendationRecord.id.asc())
            )
        ).scalars()
    )

    if existing_rows and not refresh:
        active_views = [
            _to_view(row)
            for row in existing_rows
            if row.status == "ACTIVE" and not bool((row.evidence_json or {}).get("hidden", False))
        ]
        active_views.sort(key=lambda v: (-v.expected_gain_score, str(v.target_entity_id)))
        return tuple(active_views[:top_k])

    dismissed_node_ids = frozenset(
        str(row.target_entity_id)
        for row in existing_rows
        if row.status == "DISMISSED" or bool((row.evidence_json or {}).get("hidden", False))
    )

    # Clear un-dismissed active recommendations on explicit refresh before persisting new Top-K.
    for row in existing_rows:
        if row.status == "ACTIVE" and not bool((row.evidence_json or {}).get("hidden", False)):
            await session.delete(row)
    if existing_rows and refresh:
        await session.flush()

    nodes = await _build_learner_signal_nodes(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
    )
    ranked = rank_next_step_recommendations(
        learner_id=str(membership_id),
        nodes=nodes,
        dismissed_node_ids=dismissed_node_ids,
        top_k=top_k,
    )

    persisted_views: list[RecommendationView] = []
    for item in ranked:
        evidence_payload: dict[str, Any] = {
            "title": item.title,
            "expected_gain_score": item.expected_gain_score,
            "failed_question_ids": list(item.evidence.failed_question_ids),
            "weak_prerequisite_ids": list(item.evidence.weak_prerequisite_ids),
            "mastery_estimate": item.evidence.mastery_estimate,
            "rationale_text": item.evidence.rationale_text,
            "feedback": None,
            "hidden": False,
        }
        record = RecommendationRecord(
            tenant_id=tenant_id,
            membership_id=membership_id,
            recommended_action_type=item.action_type.value,
            target_entity_id=uuid.UUID(item.taxonomy_node_id),
            evidence_json=evidence_payload,
            status="ACTIVE",
        )
        session.add(record)
        await session.flush()
        persisted_views.append(_to_view(record))

    return tuple(persisted_views)


async def submit_recommendation_feedback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    feedback: str,
    note: str | None = None,
) -> RecommendationView:
    """Apply HELPFUL / NOT_RELEVANT / DISMISSED feedback to a learner's own recommendation."""
    try:
        parsed_feedback = RecommendationFeedbackKind(feedback.strip().upper())
    except ValueError as exc:
        raise RecommendationValidationError(
            "INVALID_FEEDBACK",
            "feedback must be one of HELPFUL, NOT_RELEVANT, DISMISSED",
        ) from exc

    row = (
        await session.execute(
            select(RecommendationRecord).where(
                RecommendationRecord.id == recommendation_id,
                RecommendationRecord.tenant_id == tenant_id,
                RecommendationRecord.membership_id == membership_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise RecommendationNotFoundError(
            f"recommendation {recommendation_id} was not found for learner {membership_id}"
        )

    ev = dict(row.evidence_json or {})
    pure_rec = RankedRecommendation(
        recommendation_id=str(row.id),
        taxonomy_node_id=str(row.target_entity_id),
        title=str(ev.get("title") or row.target_entity_id),
        action_type=RecommendationActionType(row.recommended_action_type),
        expected_gain_score=float(ev.get("expected_gain_score", 0.0)),
        evidence=RecommendationEvidence(
            failed_question_ids=tuple(str(x) for x in ev.get("failed_question_ids", ())),
            weak_prerequisite_ids=tuple(str(x) for x in ev.get("weak_prerequisite_ids", ())),
            mastery_estimate=float(ev.get("mastery_estimate", 0.0)),
            rationale_text=str(ev.get("rationale_text") or ""),
        ),
    )
    updated_pure = apply_recommendation_feedback(pure_rec, feedback=parsed_feedback)

    ev["feedback"] = updated_pure.feedback.value if updated_pure.feedback is not None else None
    ev["hidden"] = updated_pure.hidden
    if note is not None and note.strip():
        ev["feedback_note"] = note.strip()

    row.evidence_json = ev
    if parsed_feedback == RecommendationFeedbackKind.DISMISSED:
        row.status = "DISMISSED"
    await session.flush()
    return _to_view(row)
