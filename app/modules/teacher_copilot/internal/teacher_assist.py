"""E06 Teacher AI Copilot: class-scoped error clustering and confirmation-gated remedial draft assignments."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class DraftAssignmentStatus(StrEnum):
    AWAITING_TEACHER_CONFIRMATION = "AWAITING_TEACHER_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    DISCARDED = "DISCARDED"


class CrossClassScopeError(PermissionError):
    """Raised when Teacher Copilot receives data outside the target (tenant_id, class_id) scope."""


@dataclass(frozen=True)
class StudentMistakeObservation:
    tenant_id: str
    class_id: str
    student_id: str
    taxonomy_node_id: str
    question_id: str
    chosen_option: str
    correct_option: str


@dataclass(frozen=True)
class ClassMisconceptionCluster:
    taxonomy_node_id: str
    wrong_response_count: int
    affected_student_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    top_distractor: str | None
    summary_text: str


@dataclass(frozen=True)
class DraftRemedialAssignment:
    draft_id: str
    tenant_id: str
    class_id: str
    taxonomy_node_id: str
    target_student_ids: tuple[str, ...]
    suggested_question_count: int
    rationale: str
    status: DraftAssignmentStatus = DraftAssignmentStatus.AWAITING_TEACHER_CONFIRMATION
    confirmed_by_teacher_id: str | None = None


@dataclass(frozen=True)
class TeacherCopilotSummary:
    tenant_id: str
    class_id: str
    clusters: tuple[ClassMisconceptionCluster, ...]
    draft_assignments: tuple[DraftRemedialAssignment, ...]


def generate_class_copilot_summary(
    *,
    tenant_id: str,
    class_id: str,
    observations: tuple[StudentMistakeObservation, ...],
    max_question_target: int = 10,
) -> TeacherCopilotSummary:
    """Cluster class errors strictly inside (tenant_id, class_id) and propose unconfirmed drafts."""
    if not tenant_id.strip() or not class_id.strip():
        raise ValueError("tenant_id and class_id are required")

    for obs in observations:
        if obs.tenant_id != tenant_id or obs.class_id != class_id:
            raise CrossClassScopeError(
                f"observation for class {obs.class_id!r} (tenant {obs.tenant_id!r}) violates RLS scope {class_id!r}"
            )

    grouped: dict[str, list[StudentMistakeObservation]] = {}
    for obs in observations:
        if obs.chosen_option == obs.correct_option:
            continue
        grouped.setdefault(obs.taxonomy_node_id, []).append(obs)

    clusters: list[ClassMisconceptionCluster] = []
    drafts: list[DraftRemedialAssignment] = []

    for tax_id, items in grouped.items():
        students = tuple(sorted({item.student_id for item in items}))
        questions = tuple(sorted({item.question_id for item in items}))
        distractor_counts: dict[str, int] = {}
        for item in items:
            opt = item.chosen_option.strip()
            if opt:
                distractor_counts[opt] = distractor_counts.get(opt, 0) + 1
        top_distractor = (
            min(distractor_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if distractor_counts
            else None
        )
        summary = (
            f"Chủ đề {tax_id}: {len(students)} học sinh sai ({len(items)} lượt), "
            f"phương án nhiễu hay chọn nhất: {top_distractor or 'N/A'}."
        )
        clusters.append(
            ClassMisconceptionCluster(
                taxonomy_node_id=tax_id,
                wrong_response_count=len(items),
                affected_student_ids=students,
                question_ids=questions,
                top_distractor=top_distractor,
                summary_text=summary,
            )
        )

    clusters.sort(key=lambda c: (-c.wrong_response_count, c.taxonomy_node_id))

    for idx, cluster in enumerate(clusters, start=1):
        drafts.append(
            DraftRemedialAssignment(
                draft_id=f"remedial-{class_id}-{idx}",
                tenant_id=tenant_id,
                class_id=class_id,
                taxonomy_node_id=cluster.taxonomy_node_id,
                target_student_ids=cluster.affected_student_ids,
                suggested_question_count=max_question_target,
                rationale=cluster.summary_text,
                status=DraftAssignmentStatus.AWAITING_TEACHER_CONFIRMATION,
            )
        )

    return TeacherCopilotSummary(
        tenant_id=tenant_id,
        class_id=class_id,
        clusters=tuple(clusters),
        draft_assignments=tuple(drafts),
    )


def confirm_remedial_draft(
    draft: DraftRemedialAssignment,
    *,
    teacher_id: str,
    actor_tenant_id: str,
) -> DraftRemedialAssignment:
    """Require explicit teacher confirmation within the same tenant before assigning to students."""
    if draft.tenant_id != actor_tenant_id:
        raise CrossClassScopeError("cannot confirm remedial draft across tenant boundaries")
    if not teacher_id.strip():
        raise ValueError("teacher_id is required to confirm draft assignment")
    if draft.status != DraftAssignmentStatus.AWAITING_TEACHER_CONFIRMATION:
        raise ValueError(f"draft cannot be confirmed from status {draft.status}")

    return replace(
        draft,
        status=DraftAssignmentStatus.CONFIRMED,
        confirmed_by_teacher_id=teacher_id.strip(),
    )
