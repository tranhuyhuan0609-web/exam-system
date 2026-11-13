"""E05 Explainable Next-Step Recommendation Engine pure ranking and evidence rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class RecommendationActionType(StrEnum):
    REVIEW_PREREQUISITE = "REVIEW_PREREQUISITE"
    REMEDIATE_ERROR_NOTE = "REMEDIATE_ERROR_NOTE"
    TARGETED_PRACTICE = "TARGETED_PRACTICE"


class RecommendationFeedbackKind(StrEnum):
    HELPFUL = "HELPFUL"
    NOT_RELEVANT = "NOT_RELEVANT"
    DISMISSED = "DISMISSED"


@dataclass(frozen=True)
class LearnerSignalNode:
    taxonomy_node_id: str
    title: str
    mastery_estimate: float
    unresolved_question_ids: tuple[str, ...] = ()
    prerequisite_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecommendationEvidence:
    failed_question_ids: tuple[str, ...]
    weak_prerequisite_ids: tuple[str, ...]
    mastery_estimate: float
    rationale_text: str


@dataclass(frozen=True)
class RankedRecommendation:
    recommendation_id: str
    taxonomy_node_id: str
    title: str
    action_type: RecommendationActionType
    expected_gain_score: float
    evidence: RecommendationEvidence
    feedback: RecommendationFeedbackKind | None = None
    hidden: bool = False


def rank_next_step_recommendations(
    *,
    learner_id: str,
    nodes: tuple[LearnerSignalNode, ...],
    dismissed_node_ids: frozenset[str] = frozenset(),
    top_k: int = 3,
) -> tuple[RankedRecommendation, ...]:
    """Rank top-K high-impact study steps combining error notes, mastery deficit, and prerequisite gaps."""
    if not learner_id.strip():
        raise ValueError("learner_id is required")
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    mastery_by_id = {n.taxonomy_node_id: n.mastery_estimate for n in nodes}
    scored: list[RankedRecommendation] = []

    for node in nodes:
        if node.taxonomy_node_id in dismissed_node_ids:
            continue

        weak_prereqs = tuple(
            pid for pid in node.prerequisite_ids if mastery_by_id.get(pid, 1.0) < 0.60
        )
        mastery_deficit = max(0.0, 1.0 - max(0.0, min(1.0, node.mastery_estimate)))
        error_count = len(node.unresolved_question_ids)

        if mastery_deficit < 0.20 and error_count == 0 and not weak_prereqs:
            continue

        score = round(
            (mastery_deficit * 50.0) + (error_count * 18.0) + (len(weak_prereqs) * 20.0),
            4,
        )

        if weak_prereqs:
            action = RecommendationActionType.REVIEW_PREREQUISITE
        elif error_count > 0:
            action = RecommendationActionType.REMEDIATE_ERROR_NOTE
        else:
            action = RecommendationActionType.TARGETED_PRACTICE

        reasons: list[str] = []
        if error_count > 0:
            reasons.append(f"còn {error_count} câu sai chưa khắc phục ({', '.join(node.unresolved_question_ids[:3])})")
        if weak_prereqs:
            reasons.append(f"kiến thức tiên quyết yếu ở {', '.join(weak_prereqs)}")
        if mastery_deficit >= 0.20:
            reasons.append(f"mức làm chủ hiện đạt {round(node.mastery_estimate * 100)}%")

        rationale = f"Ưu tiên ôn '{node.title}' vì " + "; ".join(reasons) + "."
        evidence = RecommendationEvidence(
            failed_question_ids=node.unresolved_question_ids,
            weak_prerequisite_ids=weak_prereqs,
            mastery_estimate=node.mastery_estimate,
            rationale_text=rationale,
        )
        scored.append(
            RankedRecommendation(
                recommendation_id=f"rec-{learner_id}-{node.taxonomy_node_id}",
                taxonomy_node_id=node.taxonomy_node_id,
                title=node.title,
                action_type=action,
                expected_gain_score=score,
                evidence=evidence,
            )
        )

    scored.sort(key=lambda r: (-r.expected_gain_score, r.taxonomy_node_id))
    return tuple(scored[:top_k])


def apply_recommendation_feedback(
    recommendation: RankedRecommendation,
    *,
    feedback: RecommendationFeedbackKind,
) -> RankedRecommendation:
    """Record student relevance/dismiss feedback on an explainable recommendation."""
    hide = feedback in (RecommendationFeedbackKind.DISMISSED, RecommendationFeedbackKind.NOT_RELEVANT)
    return replace(recommendation, feedback=feedback, hidden=hide)
