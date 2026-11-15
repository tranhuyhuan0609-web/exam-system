"""Database service layer for D02 Personalized Learning Path."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge_graph.public import list_knowledge_edges
from app.modules.learning_path.internal.models import LearningPlan, PlanTask
from app.modules.learning_path.internal.remediation import (
    DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
    PrerequisiteEdge,
    resolve_prerequisite_remediation_order,
)

PLAN_STATUSES = frozenset({"ACTIVE", "COMPLETED", "ARCHIVED"})
TASK_TYPES = frozenset({"PREREQUISITE_REVIEW", "READ_CONCEPT", "PRACTICE_SET", "ERROR_REMEDIATION"})
TASK_STATUSES = frozenset({"PENDING", "IN_PROGRESS", "COMPLETED", "SKIPPED"})


class LearningPathValidationError(ValueError):
    """Raised when learning plan or task parameters violate D02 invariants."""


class LearningPathNotFoundError(LookupError):
    """Raised when a learning plan or plan task does not exist in the caller's tenant."""


@dataclass(frozen=True, slots=True)
class PlanTaskView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    learning_plan_id: uuid.UUID
    taxonomy_node_id: uuid.UUID
    task_type: str
    rationale_text: str
    sequence_order: int
    status: str


@dataclass(frozen=True, slots=True)
class LearningPlanView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    target_framework_code: str
    status: str
    created_at: datetime
    tasks: tuple[PlanTaskView, ...]


def _task_view(row: PlanTask) -> PlanTaskView:
    return PlanTaskView(
        id=row.id,
        tenant_id=row.tenant_id,
        learning_plan_id=row.learning_plan_id,
        taxonomy_node_id=row.taxonomy_node_id,
        task_type=row.task_type,
        rationale_text=row.rationale_text,
        sequence_order=row.sequence_order,
        status=row.status,
    )


def _plan_view(row: LearningPlan, tasks: Sequence[PlanTask]) -> LearningPlanView:
    ordered = sorted(tasks, key=lambda t: (t.sequence_order, str(t.id)))
    return LearningPlanView(
        id=row.id,
        tenant_id=row.tenant_id,
        membership_id=row.membership_id,
        target_framework_code=row.target_framework_code,
        status=row.status,
        created_at=row.created_at,
        tasks=tuple(_task_view(t) for t in ordered),
    )


async def _selectable_tables(session: AsyncSession, names: Sequence[str]) -> set[str]:
    rows = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(:names) "
                "AND has_table_privilege(current_user, 'public.' || quote_ident(tablename), 'SELECT')"
            ),
            {"names": list(names)},
        )
    ).scalars().all()
    return set(rows)


async def _load_node_codes(
    session: AsyncSession,
    node_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, str]:
    if not node_ids:
        return {}
    rows = (
        await session.execute(
            text("SELECT id, code FROM taxonomy_nodes WHERE id = ANY(:ids)"),
            {"ids": list(dict.fromkeys(node_ids))},
        )
    ).all()
    return {uuid.UUID(str(r.id)): str(r.code) for r in rows}


async def _load_mastery_map(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    overrides: Mapping[uuid.UUID, float] | None = None,
) -> dict[uuid.UUID, float]:
    mastery: dict[uuid.UUID, float] = {}
    readable = await _selectable_tables(session, ("mastery_estimates",))
    if "mastery_estimates" in readable:
        rows = (
            await session.execute(
                text(
                    "SELECT taxonomy_node_id, mastery_probability "
                    "FROM mastery_estimates "
                    "WHERE tenant_id = :tenant_id AND membership_id = :membership_id"
                ),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        ).all()
        for row in rows:
            mastery[uuid.UUID(str(row.taxonomy_node_id))] = float(row.mastery_probability)
    if overrides:
        for node_id, prob in overrides.items():
            if not (0.0 <= float(prob) <= 1.0):
                raise LearningPathValidationError("mastery probability override must be in [0.0, 1.0]")
            mastery[node_id] = float(prob)
    return mastery


async def _load_open_error_nodes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> set[uuid.UUID]:
    readable = await _selectable_tables(session, ("error_notes",))
    if "error_notes" not in readable:
        return set()
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT primary_taxonomy_node_id FROM error_notes "
                "WHERE tenant_id = :tenant_id AND membership_id = :membership_id "
                "AND status IN ('OPEN', 'IN_REMEDIATION') AND primary_taxonomy_node_id IS NOT NULL"
            ),
            {"tenant_id": tenant_id, "membership_id": membership_id},
        )
    ).scalars().all()
    return {uuid.UUID(str(nid)) for nid in rows}


async def _compute_ordered_task_specs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    weak_node_ids: Sequence[uuid.UUID] | None,
    mastery_overrides: Mapping[uuid.UUID, float] | None,
    graph_version: int,
    prerequisite_mastery_threshold: float,
    excluded_node_ids: set[uuid.UUID] | None = None,
) -> list[tuple[uuid.UUID, str, str]]:
    if graph_version < 1:
        raise LearningPathValidationError("graph_version must be >= 1")
    if not (0.0 <= prerequisite_mastery_threshold <= 1.0):
        raise LearningPathValidationError("prerequisite_mastery_threshold must be in [0.0, 1.0]")

    mastery_map = await _load_mastery_map(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        overrides=mastery_overrides,
    )
    open_error_nodes = await _load_open_error_nodes(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
    )
    if weak_node_ids is None:
        discovered = [nid for nid, prob in mastery_map.items() if prob < prerequisite_mastery_threshold]
        for err_nid in open_error_nodes:
            if err_nid not in discovered:
                discovered.append(err_nid)
        target_weak_nodes = tuple(discovered)
    else:
        target_weak_nodes = tuple(dict.fromkeys(weak_node_ids))

    kg_edges = await list_knowledge_edges(
        session,
        tenant_id=tenant_id,
        graph_version=graph_version,
        edge_type="PREREQUISITE",
        active_only=True,
    )
    prereq_edges = tuple(
        PrerequisiteEdge(prerequisite_id=e.source_node_id, dependent_id=e.target_node_id)
        for e in kg_edges
    )

    try:
        steps = resolve_prerequisite_remediation_order(
            target_weak_nodes,
            prereq_edges,
            mastery_map,
            prerequisite_mastery_threshold=prerequisite_mastery_threshold,
        )
    except ValueError as exc:
        raise LearningPathValidationError(str(exc)) from exc

    excluded = excluded_node_ids or set()
    filtered_steps = [s for s in steps if s.node_id not in excluded]
    if not filtered_steps:
        return []

    referenced_ids: list[uuid.UUID] = []
    for step in filtered_steps:
        referenced_ids.append(step.node_id)
        if step.dependent_node_id is not None:
            referenced_ids.append(step.dependent_node_id)
    code_map = await _load_node_codes(session, referenced_ids)
    missing_nodes = [step.node_id for step in filtered_steps if step.node_id not in code_map]
    if missing_nodes:
        raise LearningPathNotFoundError(
            f"taxonomy_node(s) not found: {', '.join(str(nid) for nid in missing_nodes)}"
        )

    specs: list[tuple[uuid.UUID, str, str]] = []
    thr_pct = prerequisite_mastery_threshold * 100.0
    for step in filtered_steps:
        node_label = code_map.get(step.node_id, str(step.node_id))
        node_mastery_pct = mastery_map.get(step.node_id, 0.0) * 100.0
        if step.reason_code == "PREREQUISITE_OF":
            dep_label = (
                code_map.get(step.dependent_node_id, str(step.dependent_node_id))
                if step.dependent_node_id is not None
                else "dependent skill"
            )
            task_type = "PREREQUISITE_REVIEW"
            rationale = (
                f"Review prerequisite {node_label} (mastery {node_mastery_pct:.0f}% < {thr_pct:.0f}%) "
                f"before tackling {dep_label}."
            )
        elif step.node_id in open_error_nodes:
            task_type = "ERROR_REMEDIATION"
            rationale = (
                f"Remediate unresolved mistakes in {node_label} "
                f"(current mastery {node_mastery_pct:.0f}%)."
            )
        else:
            task_type = "PRACTICE_SET"
            rationale = (
                f"Practice weak target {node_label} "
                f"(mastery {node_mastery_pct:.0f}% < {thr_pct:.0f}%)."
            )
        specs.append((step.node_id, task_type, rationale))
    return specs


async def create_learning_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    target_framework_code: str = "THPTQG_2025",
    weak_node_ids: Sequence[uuid.UUID] | None = None,
    mastery_overrides: Mapping[uuid.UUID, float] | None = None,
    graph_version: int = 1,
    prerequisite_mastery_threshold: float = DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
) -> LearningPlanView:
    """Create a `learning_plans` record with prerequisite-first `plan_tasks`."""
    clean_framework = target_framework_code.strip()
    if not clean_framework or len(clean_framework) > 60:
        raise LearningPathValidationError("target_framework_code must be 1..60 characters")

    plan = LearningPlan(
        tenant_id=tenant_id,
        membership_id=membership_id,
        target_framework_code=clean_framework,
        status="ACTIVE",
    )
    session.add(plan)
    await session.flush()

    specs = await _compute_ordered_task_specs(
        session,
        tenant_id=tenant_id,
        membership_id=membership_id,
        weak_node_ids=weak_node_ids,
        mastery_overrides=mastery_overrides,
        graph_version=graph_version,
        prerequisite_mastery_threshold=prerequisite_mastery_threshold,
    )
    tasks: list[PlanTask] = []
    for idx, (node_id, task_type, rationale) in enumerate(specs, start=1):
        task = PlanTask(
            tenant_id=tenant_id,
            learning_plan_id=plan.id,
            taxonomy_node_id=node_id,
            task_type=task_type,
            rationale_text=rationale,
            sequence_order=idx,
            status="PENDING",
        )
        session.add(task)
        tasks.append(task)
    await session.flush()
    return _plan_view(plan, tasks)


async def get_learning_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
) -> LearningPlanView:
    """Fetch a learning plan and its ordered `plan_tasks` within the tenant."""
    plan = (
        await session.execute(
            select(LearningPlan).where(
                LearningPlan.tenant_id == tenant_id,
                LearningPlan.id == plan_id,
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        raise LearningPathNotFoundError(f"learning plan {plan_id} not found")

    tasks = (
        await session.execute(
            select(PlanTask)
            .where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.learning_plan_id == plan.id,
            )
            .order_by(PlanTask.sequence_order.asc(), PlanTask.id.asc())
        )
    ).scalars().all()
    return _plan_view(plan, tasks)


async def list_learning_plans(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
) -> tuple[LearningPlanView, ...]:
    """List learning plans in the caller's tenant."""
    stmt = (
        select(LearningPlan)
        .where(LearningPlan.tenant_id == tenant_id)
        .order_by(LearningPlan.created_at.desc(), LearningPlan.id.desc())
    )
    if membership_id is not None:
        stmt = stmt.where(LearningPlan.membership_id == membership_id)
    plans = (await session.execute(stmt)).scalars().all()
    if not plans:
        return ()

    plan_ids = [p.id for p in plans]
    all_tasks = (
        await session.execute(
            select(PlanTask)
            .where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.learning_plan_id.in_(plan_ids),
            )
            .order_by(PlanTask.sequence_order.asc(), PlanTask.id.asc())
        )
    ).scalars().all()
    tasks_by_plan: dict[uuid.UUID, list[PlanTask]] = {pid: [] for pid in plan_ids}
    for task in all_tasks:
        tasks_by_plan.setdefault(task.learning_plan_id, []).append(task)
    return tuple(_plan_view(p, tasks_by_plan.get(p.id, ())) for p in plans)


async def recalculate_learning_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    weak_node_ids: Sequence[uuid.UUID] | None = None,
    mastery_overrides: Mapping[uuid.UUID, float] | None = None,
    graph_version: int = 1,
    prerequisite_mastery_threshold: float = DEFAULT_PREREQUISITE_MASTERY_THRESHOLD,
) -> LearningPlanView:
    """Recompute uncompleted `plan_tasks` using fresh prerequisites without touching teacher assignments."""
    plan = (
        await session.execute(
            select(LearningPlan).where(
                LearningPlan.tenant_id == tenant_id,
                LearningPlan.id == plan_id,
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        raise LearningPathNotFoundError(f"learning plan {plan_id} not found")
    if plan.status == "ARCHIVED":
        raise LearningPathValidationError("cannot recalculate an ARCHIVED learning plan")

    existing_tasks = (
        await session.execute(
            select(PlanTask)
            .where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.learning_plan_id == plan.id,
            )
            .order_by(PlanTask.sequence_order.asc(), PlanTask.id.asc())
        )
    ).scalars().all()

    preserved_tasks = [t for t in existing_tasks if t.status in ("COMPLETED", "SKIPPED")]
    preserved_node_ids = {t.taxonomy_node_id for t in preserved_tasks}
    stale_task_ids = [t.id for t in existing_tasks if t.status in ("PENDING", "IN_PROGRESS")]
    if stale_task_ids:
        await session.execute(
            delete(PlanTask).where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.id.in_(stale_task_ids),
            )
        )
        await session.flush()

    for idx, task in enumerate(preserved_tasks, start=1):
        task.sequence_order = idx

    specs = await _compute_ordered_task_specs(
        session,
        tenant_id=tenant_id,
        membership_id=plan.membership_id,
        weak_node_ids=weak_node_ids,
        mastery_overrides=mastery_overrides,
        graph_version=graph_version,
        prerequisite_mastery_threshold=prerequisite_mastery_threshold,
        excluded_node_ids=preserved_node_ids,
    )
    next_order = len(preserved_tasks) + 1
    new_tasks: list[PlanTask] = []
    for offset, (node_id, task_type, rationale) in enumerate(specs):
        task = PlanTask(
            tenant_id=tenant_id,
            learning_plan_id=plan.id,
            taxonomy_node_id=node_id,
            task_type=task_type,
            rationale_text=rationale,
            sequence_order=next_order + offset,
            status="PENDING",
        )
        session.add(task)
        new_tasks.append(task)

    all_tasks = preserved_tasks + new_tasks
    if all_tasks and all(t.status in ("COMPLETED", "SKIPPED") for t in all_tasks):
        plan.status = "COMPLETED"
    else:
        plan.status = "ACTIVE"

    await session.flush()
    return _plan_view(plan, all_tasks)


async def update_plan_task_status(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    task_id: uuid.UUID,
    status: str,
) -> LearningPlanView:
    """Update one `plan_tasks` row status and auto-complete the parent plan when all tasks finish."""
    clean_status = status.strip().upper()
    if clean_status not in TASK_STATUSES:
        raise LearningPathValidationError(f"unsupported plan_task status: {status}")

    task = (
        await session.execute(
            select(PlanTask).where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.learning_plan_id == plan_id,
                PlanTask.id == task_id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise LearningPathNotFoundError(f"plan task {task_id} not found in plan {plan_id}")

    task.status = clean_status
    await session.flush()

    plan = (
        await session.execute(
            select(LearningPlan).where(
                LearningPlan.tenant_id == tenant_id,
                LearningPlan.id == plan_id,
            )
        )
    ).scalar_one()
    all_tasks = (
        await session.execute(
            select(PlanTask)
            .where(
                PlanTask.tenant_id == tenant_id,
                PlanTask.learning_plan_id == plan_id,
            )
            .order_by(PlanTask.sequence_order.asc(), PlanTask.id.asc())
        )
    ).scalars().all()

    if all_tasks and all(t.status in ("COMPLETED", "SKIPPED") for t in all_tasks):
        plan.status = "COMPLETED"
    elif plan.status == "COMPLETED":
        plan.status = "ACTIVE"
    await session.flush()
    return _plan_view(plan, all_tasks)
