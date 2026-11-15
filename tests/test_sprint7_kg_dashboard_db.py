"""PostgreSQL + RLS + FastAPI integration tests for TASK-28 (Sprint 7 Part 1):
- B05 knowledge_graph (`knowledge_edges`, `graph_validation_runs`, DAG cycle rejection, H-01 active-endpoint RLS)
- D01 dashboard (`activity_events`, `learner_dashboard_projections`, Asia/Ho_Chi_Minh streaks, role/RLS isolation)
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.modules.content.internal.models import ContentItem, ContentVersion
from app.modules.dashboard.internal.models import (  # noqa: F401
    ActivityEvent,
    LearnerDashboardProjectionModel,
)
from app.modules.dashboard.public import (
    recompute_learner_dashboard_projection,
    record_activity_event,
)
from app.modules.dashboard.router import router as dashboard_router
from app.modules.knowledge_graph.internal.models import (  # noqa: F401
    GraphValidationRun,
    KnowledgeEdge,
)
from app.modules.knowledge_graph.router import router as knowledge_graph_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.taxonomy.internal.models import TaxonomyNode
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID, Tenant
from app.platform.tenancy import DbRole, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(knowledge_graph_router)
    test_app.include_router(dashboard_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    roles: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["LEARNER"],
        entitlement_tier="STUDENT_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Create two isolated tenants with users and memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        ua, ub = uuid.uuid4(), uuid.uuid4()
        ma, mb = uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t28-a-{ta.hex[:8]}", name="School A"),
                    Tenant(id=tb, slug=f"t28-b-{tb.hex[:8]}", name="School B"),
                    User(id=ua, email_blind_index=f"{ua.hex}{ua.hex}", pseudonym_handle="LearnerA"),
                    User(id=ub, email_blind_index=f"{ub.hex}{ub.hex}", pseudonym_handle="LearnerB"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=ma, tenant_id=ta, user_id=ua, roles=["TEACHER", "LEARNER"]),
                    Membership(id=mb, tenant_id=tb, user_id=ub, roles=["LEARNER"]),
                ]
            )
        return {"ta": ta, "tb": tb, "ua": ua, "ub": ub, "ma": ma, "mb": mb}

    return run(create())


def test_pep562_lazy_public_exports_keep_core_imports_db_free() -> None:
    """Importing Pure Core functions from public.py must not load internal ORM models."""
    code = (
        "import sys\n"
        "from app.modules.knowledge_graph.public import would_create_cycle, find_cycle\n"
        "from app.modules.dashboard.public import build_learner_dashboard_projection\n"
        "loaded = set(sys.modules)\n"
        "assert 'app.modules.knowledge_graph.internal.models' not in loaded\n"
        "assert 'app.modules.dashboard.internal.models' not in loaded\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_knowledge_graph_dag_cycle_detection_and_path_endpoints(world: dict[str, uuid.UUID]) -> None:
    """B05 API persists DAG edges, blocks cycles with 422 PREREQUISITE_CYCLE_DETECTED, and resolves paths."""
    ta = world["ta"]

    async def seed_nodes() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        n1, n2, n3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    TaxonomyNode(
                        id=n1,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="M_BASE",
                        code_path="MATH.BASE",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=n2,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="M_MID",
                        code_path="MATH.MID",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=n3,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="CONCEPT",
                        code="M_ADV",
                        code_path="MATH.ADV",
                        is_active=True,
                    ),
                ]
            )
        return n1, n2, n3

    n1, n2, n3 = run(seed_nodes())
    client = _api_client()
    teacher_headers = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"], roles=["TEACHER"])
    learner_headers = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"], roles=["LEARNER"])

    forbidden = client.post(
        "/api/v1/knowledge-graph/edges",
        headers=learner_headers,
        json={"source_node_id": str(n1), "target_node_id": str(n2), "edge_type": "PREREQUISITE"},
    )
    assert forbidden.status_code == 403

    r1 = client.post(
        "/api/v1/knowledge-graph/edges",
        headers=teacher_headers,
        json={"source_node_id": str(n1), "target_node_id": str(n2), "edge_type": "PREREQUISITE", "confidence": "0.950"},
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/api/v1/knowledge-graph/edges",
        headers=teacher_headers,
        json={"source_node_id": str(n2), "target_node_id": str(n3), "edge_type": "PREREQUISITE"},
    )
    assert r2.status_code == 201, r2.text

    # Adding n3 -> n1 creates cycle n1 -> n2 -> n3 -> n1 and must fail with 422 PREREQUISITE_CYCLE_DETECTED.
    cycle_resp = client.post(
        "/api/v1/knowledge-graph/edges",
        headers=teacher_headers,
        json={"source_node_id": str(n3), "target_node_id": str(n1), "edge_type": "PREREQUISITE"},
    )
    assert cycle_resp.status_code == 422
    body = cycle_resp.json()
    assert body["error_code"] == "PREREQUISITE_CYCLE_DETECTED"
    assert len(body["cycle_path"]) >= 4

    # Non-prerequisite edge in reverse direction is allowed (not part of PREREQUISITE DAG).
    related = client.post(
        "/api/v1/knowledge-graph/edges",
        headers=teacher_headers,
        json={"source_node_id": str(n3), "target_node_id": str(n1), "edge_type": "RELATED_TO"},
    )
    assert related.status_code == 201

    val_resp = client.post("/api/v1/knowledge-graph/validate", headers=teacher_headers, json={"graph_version": 1})
    assert val_resp.status_code == 201
    assert val_resp.json()["is_dag_valid"] is True
    assert val_resp.json()["cycle_path"] == []

    path_resp = client.get(f"/api/v1/knowledge-graph/path?target_node_id={n3}", headers=learner_headers)
    assert path_resp.status_code == 200
    assert path_resp.json()["prerequisite_node_ids"] == [str(n1), str(n2)]


def test_knowledge_edges_hybrid_rls_hides_inactive_endpoints_and_blocks_cross_tenant(
    world: dict[str, uuid.UUID],
) -> None:
    """H-01 hybrid_read_policy hides SYSTEM_PUBLIC edges whenever source or target has is_active = FALSE."""
    ta, tb = world["ta"], world["tb"]

    async def seed_public_and_private() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
        s1, s2, b1, b2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    TaxonomyNode(
                        id=s1,
                        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                        visibility_scope="SYSTEM_PUBLIC",
                        node_type="TOPIC",
                        code="PUB_N1",
                        code_path="PUB.N1",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=s2,
                        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                        visibility_scope="SYSTEM_PUBLIC",
                        node_type="TOPIC",
                        code="PUB_N2",
                        code_path="PUB.N2",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=b1,
                        tenant_id=tb,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="TOPIC",
                        code="TB_N1",
                        code_path="TB.N1",
                        is_active=True,
                    ),
                    TaxonomyNode(
                        id=b2,
                        tenant_id=tb,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="TOPIC",
                        code="TB_N2",
                        code_path="TB.N2",
                        is_active=True,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    KnowledgeEdge(
                        tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                        source_node_id=s1,
                        target_node_id=s2,
                        edge_type="PREREQUISITE",
                    ),
                    KnowledgeEdge(
                        tenant_id=tb,
                        source_node_id=b1,
                        target_node_id=b2,
                        edge_type="PREREQUISITE",
                    ),
                ]
            )
        return s1, s2, b1, b2

    s1, s2, _b1, _b2 = run(seed_public_and_private())

    async def visible_to_ta() -> list[uuid.UUID]:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            rows = (await sa.execute(select(KnowledgeEdge))).scalars().all()
            return [r.tenant_id for r in rows]

    visible_to_ta_initial = run(visible_to_ta())
    assert visible_to_ta_initial == [SYSTEM_PUBLIC_TENANT_ID]

    # Tenant A cannot insert a SYSTEM_PUBLIC knowledge edge under hybrid_write_policy.
    async def attempt_spoof_public_write() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            sa.add(
                KnowledgeEdge(
                    tenant_id=SYSTEM_PUBLIC_TENANT_ID,
                    source_node_id=s2,
                    target_node_id=s1,
                    edge_type="EXTENDS",
                )
            )
            await sa.flush()

    with pytest.raises(DBAPIError):
        run(attempt_spoof_public_write())

    # Deactivate target endpoint s2 -> RLS hybrid_read_policy immediately hides the edge.
    async def set_node_active(node_id: uuid.UUID, active: bool) -> None:
        async with SessionLocal() as session, session.begin():
            await session.execute(
                text("UPDATE taxonomy_nodes SET is_active = :active WHERE id = :id"),
                {"active": active, "id": node_id},
            )

    run(set_node_active(s2, False))
    assert run(visible_to_ta()) == []

    client = _api_client()
    headers_a = _auth_headers(tenant_id=ta, membership_id=world["ma"], user_id=world["ua"], roles=["LEARNER"])
    edge_list = client.get("/api/v1/knowledge-graph/edges", headers=headers_a)
    assert edge_list.status_code == 200
    assert edge_list.json() == []

    run(set_node_active(s2, True))
    assert run(visible_to_ta()) == [SYSTEM_PUBLIC_TENANT_ID]


def test_dashboard_activity_streaks_projection_and_rls(world: dict[str, uuid.UUID]) -> None:
    """D01 records activity events, builds Asia/Ho_Chi_Minh streaks & summary_json, and isolates tenants."""
    ta, tb = world["ta"], world["tb"]
    ma, mb = world["ma"], world["mb"]
    lesson_id = uuid.uuid4()
    version_id = uuid.uuid4()

    async def seed_content() -> None:
        async with SessionLocal() as session, session.begin():
            session.add(
                ContentItem(
                    id=lesson_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    content_type="LESSON",
                    slug="ham-so-don-dieu",
                    verification_status="VERIFIED",
                    rights_status="OWNED",
                )
            )
            await session.flush()
            session.add(
                ContentVersion(
                    id=version_id,
                    tenant_id=ta,
                    content_item_id=lesson_id,
                    version_no=1,
                    title="Tính đơn điệu của hàm số",
                    status="DRAFT",
                )
            )
            await session.execute(
                text("UPDATE content_items SET current_version_id = :vid WHERE id = :cid"),
                {"vid": version_id, "cid": lesson_id},
            )

    run(seed_content())

    # Anchor Day 0 to 08:00 Asia/Ho_Chi_Minh (+07:00) on the current VN date so
    # GET /dashboard?refresh=true (which uses current wall-clock now) sees a 3-day streak.
    from datetime import timezone as _tz

    vn_tz = _tz(timedelta(hours=7))
    now_utc = datetime.now(vn_tz).replace(hour=8, minute=0, second=0, microsecond=0).astimezone(UTC)

    async def seed_activity_and_test_roles() -> None:
        # Exam session role can record activity_events directly (`GRANT SELECT, INSERT`).
        async with tenant_transaction(ta, role=DbRole.EXAM_SESSION_RW) as sa:
            await record_activity_event(
                sa,
                tenant_id=ta,
                membership_id=ma,
                activity_type="LESSON_VIEW",
                entity_id=lesson_id,
                duration_seconds=300,
                occurred_at=now_utc - timedelta(days=2),
            )
            await record_activity_event(
                sa,
                tenant_id=ta,
                membership_id=ma,
                activity_type="LESSON_VIEW",
                entity_id=lesson_id,
                duration_seconds=450,
                occurred_at=now_utc - timedelta(days=1),
            )
        # App runtime role reads activity_events and builds initial streak_days = 2 projection.
        async with tenant_transaction(ta, role=DbRole.APP_RW) as sa:
            initial_proj = await recompute_learner_dashboard_projection(
                sa,
                tenant_id=ta,
                membership_id=ma,
                now=now_utc,
            )
            assert initial_proj.streak_days == 2
        # Worker grading role updates learner_dashboard_projections under least privilege,
        # preserving existing streak_days without needing SELECT on activity_events.
        async with tenant_transaction(ta, role=DbRole.WORKER_GRADING) as sa:
            proj = await recompute_learner_dashboard_projection(
                sa,
                tenant_id=ta,
                membership_id=ma,
                now=now_utc,
                open_error_notes_count=3,
                fsrs_cards_due_today=5,
            )
            assert proj.streak_days == 2
            assert proj.summary_json["open_error_notes_count"] == 3
            assert proj.summary_json["fsrs_cards_due_today"] == 5

    run(seed_activity_and_test_roles())

    client = _api_client()
    headers_a = _auth_headers(tenant_id=ta, membership_id=ma, user_id=world["ua"], roles=["LEARNER"])
    headers_b = _auth_headers(tenant_id=tb, membership_id=mb, user_id=world["ub"], roles=["LEARNER"])

    post_act = client.post(
        "/api/v1/learners/me/activity",
        headers=headers_a,
        json={
            "activity_type": "LESSON_VIEW",
            "entity_id": str(lesson_id),
            "duration_seconds": 600,
            "occurred_at": now_utc.isoformat(),
        },
    )
    assert post_act.status_code == 201, post_act.text

    dash_a = client.get("/api/v1/learners/me/dashboard?refresh=true", headers=headers_a)
    assert dash_a.status_code == 200
    payload_a = dash_a.json()
    assert payload_a["streak_days"] == 3
    assert payload_a["summary_json"]["activity_event_count"] == 3
    assert payload_a["summary_json"]["recent_lessons"][0]["content_item_id"] == str(lesson_id)
    assert Decimal(payload_a["summary_json"]["recent_lessons"][0]["completion_pct"]) == Decimal("1.0000")

    act_a = client.get("/api/v1/learners/me/activity", headers=headers_a)
    assert act_a.status_code == 200
    assert len(act_a.json()) == 3

    # Tenant B cannot see Tenant A's activity events or dashboard state; header spoofing gets 403.
    act_b = client.get("/api/v1/learners/me/activity", headers=headers_b)
    assert act_b.status_code == 200
    assert act_b.json() == []

    spoofed = client.get(
        "/api/v1/learners/me/dashboard",
        headers=_auth_headers(
            tenant_id=tb,
            membership_id=mb,
            user_id=world["ub"],
            extra={"X-Tenant-Id": str(ta)},
        ),
    )
    assert spoofed.status_code == 403
