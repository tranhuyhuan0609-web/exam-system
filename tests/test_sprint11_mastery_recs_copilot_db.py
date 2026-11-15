"""PostgreSQL + RLS + FastAPI integration tests for TASK-33 (Sprint 11):
- PEP 562 lazy-loading (Pure Core imports stay DB/ORM-free)
- D06 mastery (`question_irt_parameters`, `mastery_estimates`, <20 items or SE >= 0.35 hides estimates
  under `INSUFFICIENT_DATA`, 4-tier IRT calibration gate, RLS)
- E05 recommendations (`recommendations`, ranking from `error_notes` + `mastery_estimates` +
  `knowledge_edges`, transparent `evidence_json`, feedback hide/dismiss, RLS & learner isolation)
- E06 teacher_copilot (`teacher_assist_jobs`, strict teacher+class+tenant scope blocking cross-class /
  cross-teacher / cross-tenant observations, `AWAITING_TEACHER_CONFIRMATION` gate until `confirm`)
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.modules.mastery.internal.models import MasteryEstimate, QuestionIrtParameter  # noqa: F401
from app.modules.mastery.router import router as mastery_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.recommendations.internal.models import RecommendationRecord  # noqa: F401
from app.modules.recommendations.router import router as recommendations_router
from app.modules.taxonomy.internal.models import TaxonomyNode, TaxonomyNodeVersion
from app.modules.teacher_copilot.internal.models import TeacherAssistJob  # noqa: F401
from app.modules.teacher_copilot.router import router as teacher_copilot_router
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(mastery_router)
    test_app.include_router(recommendations_router)
    test_app.include_router(teacher_copilot_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["ADMIN"],
        entitlement_tier="TEACHER_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    headers = {"Authorization": f"Bearer {token}"}
    if extra:
        headers.update(extra)
    return headers


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Seed two isolated tenants with teachers, learners, and memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        u_t1, u_t2, u_s1, u_s2, u_s3, u_tb = (uuid.uuid4() for _ in range(6))
        m_t1, m_t2, m_s1, m_s2, m_s3, m_tb = (uuid.uuid4() for _ in range(6))
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t33-a-{ta.hex[:8]}", name="School 33A"),
                    Tenant(id=tb, slug=f"t33-b-{tb.hex[:8]}", name="School 33B"),
                    User(id=u_t1, email_blind_index=f"{u_t1.hex}{u_t1.hex}", pseudonym_handle="TeacherA1"),
                    User(id=u_t2, email_blind_index=f"{u_t2.hex}{u_t2.hex}", pseudonym_handle="TeacherA2"),
                    User(id=u_s1, email_blind_index=f"{u_s1.hex}{u_s1.hex}", pseudonym_handle="StudentA1"),
                    User(id=u_s2, email_blind_index=f"{u_s2.hex}{u_s2.hex}", pseudonym_handle="StudentA2"),
                    User(id=u_s3, email_blind_index=f"{u_s3.hex}{u_s3.hex}", pseudonym_handle="StudentA3"),
                    User(id=u_tb, email_blind_index=f"{u_tb.hex}{u_tb.hex}", pseudonym_handle="TeacherB1"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=m_t1, tenant_id=ta, user_id=u_t1, roles=["TEACHER", "ADMIN"]),
                    Membership(id=m_t2, tenant_id=ta, user_id=u_t2, roles=["TEACHER"]),
                    Membership(id=m_s1, tenant_id=ta, user_id=u_s1, roles=["LEARNER"]),
                    Membership(id=m_s2, tenant_id=ta, user_id=u_s2, roles=["LEARNER"]),
                    Membership(id=m_s3, tenant_id=ta, user_id=u_s3, roles=["LEARNER"]),
                    Membership(id=m_tb, tenant_id=tb, user_id=u_tb, roles=["TEACHER", "ADMIN"]),
                ]
            )
        return {
            "ta": ta,
            "tb": tb,
            "u_t1": u_t1,
            "u_t2": u_t2,
            "u_s1": u_s1,
            "u_s2": u_s2,
            "u_s3": u_s3,
            "u_tb": u_tb,
            "m_t1": m_t1,
            "m_t2": m_t2,
            "m_s1": m_s1,
            "m_s2": m_s2,
            "m_s3": m_s3,
            "m_tb": m_tb,
        }

    return run(create())


async def _insert_question(
    session,
    *,
    tenant_id: uuid.UUID,
    taxonomy_node_id: uuid.UUID,
    stable_code: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    q_id = uuid.uuid4()
    qv_id = uuid.uuid4()
    sha = hashlib.sha256(stable_code.encode("utf-8")).hexdigest()
    await session.execute(
        text(
            "INSERT INTO questions (id, tenant_id, stable_code, item_type, primary_taxonomy_node_id) "
            "VALUES (:qid, :tid, :code, 'SINGLE_CHOICE', :tax_id)"
        ),
        {"qid": q_id, "tid": tenant_id, "code": stable_code, "tax_id": taxonomy_node_id},
    )
    await session.execute(
        text(
            "INSERT INTO question_versions "
            "(id, tenant_id, question_id, version_no, stem_json, content_hash, structural_hash) "
            'VALUES (:qvid, :tid, :qid, 1, \'{"text": "Sample"}\'::jsonb, :sha, :sha)'
        ),
        {"qvid": qv_id, "tid": tenant_id, "qid": q_id, "sha": sha},
    )
    return q_id, qv_id


def test_task33_pep562_lazy_loading_keeps_pure_core_db_free() -> None:
    """Importing Pure Core exports from mastery/recommendations/teacher_copilot must not load ORM/DB modules."""
    script = """
import sys
from app.modules.mastery.public import select_irt_model_tier, IrtCalibrationMetrics
from app.modules.recommendations.public import rank_next_step_recommendations, apply_recommendation_feedback
from app.modules.teacher_copilot.public import generate_class_copilot_summary, confirm_remedial_draft

forbidden = [
    "sqlalchemy.ext.asyncio",
    "app.db",
    "app.modules.mastery.internal.models",
    "app.modules.mastery.internal.service",
    "app.modules.recommendations.internal.models",
    "app.modules.recommendations.internal.service",
    "app.modules.teacher_copilot.internal.models",
    "app.modules.teacher_copilot.internal.service",
]
loaded = [m for m in forbidden if m in sys.modules]
assert not loaded, f"Unexpected DB modules loaded on Pure Core import: {loaded}"

# Trigger PEP 562 lazy loading explicitly on demand.
from app.modules.mastery import public as mastery_pub
from app.modules.recommendations import public as rec_pub
from app.modules.teacher_copilot import public as tc_pub
assert mastery_pub.MasteryEstimate.__tablename__ == "mastery_estimates"
assert rec_pub.RecommendationRecord.__tablename__ == "recommendations"
assert tc_pub.TeacherAssistJob.__tablename__ == "teacher_assist_jobs"
print("OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    assert "OK" in proc.stdout


def test_mastery_insufficient_data_hides_estimates_and_rls(world: dict[str, uuid.UUID]) -> None:
    """D06 hides estimates under INSUFFICIENT_DATA (<20 items OR SE >= 0.35), unlocks when calibrated, enforces RLS."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_s1 = _auth_headers(tenant_id=ta, membership_id=world["m_s1"], user_id=world["u_s1"], roles=["LEARNER"])
    headers_tb = _auth_headers(tenant_id=tb, membership_id=world["m_tb"], user_id=world["u_tb"], roles=["TEACHER"])

    async def seed_tax() -> uuid.UUID:
        tax_id = uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=tax_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="TOPIC",
                    code=f"TRIG_{tax_id.hex[:6]}",
                    code_path="MATH.TRIG.FUNC",
                    is_active=True,
                )
            )
            await session.flush()
            session.add(
                TaxonomyNodeVersion(
                    tenant_id=ta,
                    taxonomy_node_id=tax_id,
                    version_no=1,
                    name="Trigonometric Functions",
                )
            )
        return tax_id

    tax_id = run(seed_tax())

    # 1) Spoofing guard
    spoof = client.post(
        "/api/v1/mastery/recompute",
        headers=_auth_headers(
            tenant_id=ta,
            membership_id=world["m_s1"],
            user_id=world["u_s1"],
            roles=["LEARNER"],
            extra={"X-Tenant-Id": str(tb)},
        ),
        json={"taxonomy_node_id": str(tax_id), "observations": [{"is_correct": True}]},
    )
    assert spoof.status_code == 403

    # 2) Fewer than 20 items -> INSUFFICIENT_DATA with theta_estimate=None and mastery_probability=None
    obs_12 = [{"is_correct": i % 3 != 0} for i in range(12)]
    res_low = client.post(
        "/api/v1/mastery/recompute",
        headers=headers_s1,
        json={"taxonomy_node_id": str(tax_id), "observations": obs_12},
    )
    assert res_low.status_code == 200, res_low.text
    low_body = res_low.json()
    assert low_body["status"] == "INSUFFICIENT_DATA"
    assert low_body["evidence_count"] == 12
    assert low_body["theta_estimate"] is None
    assert low_body["mastery_probability"] is None

    # GET endpoint also hides uncalibrated estimates
    res_get_low = client.get("/api/v1/learners/me/mastery", headers=headers_s1)
    assert res_get_low.status_code == 200
    assert len(res_get_low.json()["items"]) == 1
    assert res_get_low.json()["items"][0]["status"] == "INSUFFICIENT_DATA"
    assert res_get_low.json()["items"][0]["theta_estimate"] is None
    assert res_get_low.json()["items"][0]["mastery_probability"] is None

    # 3) >= 20 items AND SE >= 0.35 -> still INSUFFICIENT_DATA & hidden
    obs_25 = [{"is_correct": i % 4 != 0} for i in range(25)]
    res_high_se = client.post(
        "/api/v1/mastery/recompute",
        headers=headers_s1,
        json={"taxonomy_node_id": str(tax_id), "observations": obs_25, "override_se_theta": "0.350"},
    )
    assert res_high_se.status_code == 200
    assert res_high_se.json()["status"] == "INSUFFICIENT_DATA"
    assert res_high_se.json()["evidence_count"] == 25
    assert Decimal(str(res_high_se.json()["standard_error"])) == Decimal("0.350")
    assert res_high_se.json()["theta_estimate"] is None
    assert res_high_se.json()["mastery_probability"] is None

    # 4) >= 20 items AND empirical SE < 0.35 -> CALIBRATED with exposed estimates
    res_cal = client.post(
        "/api/v1/mastery/recompute",
        headers=headers_s1,
        json={"taxonomy_node_id": str(tax_id), "observations": obs_25},
    )
    assert res_cal.status_code == 200, res_cal.text
    cal_body = res_cal.json()
    assert cal_body["status"] == "CALIBRATED"
    assert Decimal(str(cal_body["standard_error"])) < Decimal("0.350")
    assert cal_body["theta_estimate"] is not None
    assert Decimal("0") <= Decimal(str(cal_body["mastery_probability"])) <= Decimal("1")

    # 5) RLS isolation across tenants
    res_foreign = client.get("/api/v1/learners/me/mastery", headers=headers_tb)
    assert res_foreign.status_code == 200
    assert res_foreign.json()["items"] == []

    async def check_rls_db() -> None:
        async with tenant_transaction(tb, role=DbRole.APP_RW) as session_b:
            rows_b = (await session_b.execute(text("SELECT count(*) FROM mastery_estimates"))).scalar_one()
            assert rows_b == 0

    run(check_rls_db())


def test_irt_calibration_4tier_gate_and_tenant_scope(world: dict[str, uuid.UUID]) -> None:
    """D06 persists calibrated IRT parameters only when Tier L2/L3/L4 statistical gates pass."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_t1 = _auth_headers(tenant_id=ta, membership_id=world["m_t1"], user_id=world["u_t1"], roles=["TEACHER"])
    headers_tb = _auth_headers(tenant_id=tb, membership_id=world["m_tb"], user_id=world["u_tb"], roles=["TEACHER"])

    async def seed_q() -> uuid.UUID:
        tax_id = uuid.uuid4()
        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=tax_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="SKILL",
                    code=f"IRT_{tax_id.hex[:6]}",
                    code_path="MATH.IRT.CALIB",
                    is_active=True,
                )
            )
            await session.flush()
            _, qv_id = await _insert_question(
                session,
                tenant_id=ta,
                taxonomy_node_id=tax_id,
                stable_code=f"Q-IRT-{tax_id.hex[:6]}",
            )
        return qv_id

    qv_id = run(seed_q())

    # Fail L2 sample_size < 150 -> stays at RULE_WEIGHTED_L1, persisted=False, nothing written in DB
    fail_l2 = client.post(
        "/api/v1/mastery/calibration",
        headers=headers_t1,
        json={
            "question_version_id": str(qv_id),
            "sample_size": 90,
            "se_b": 0.20,
            "learner_count": 100,
        },
    )
    assert fail_l2.status_code == 200, fail_l2.text
    assert fail_l2.json()["persisted"] is False
    assert fail_l2.json()["selected_tier"] == "RULE_WEIGHTED_L1"
    assert fail_l2.json()["failed_next_tier_criteria"]

    async def count_irt_rows() -> str | None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session_a:
            return (
                await session_a.execute(
                    text("SELECT model_type FROM question_irt_parameters WHERE question_version_id = :qvid"),
                    {"qvid": qv_id},
                )
            ).scalar_one_or_none()

    assert run(count_irt_rows()) is None

    # Pass L2 -> RASCH_1PL_L2 persisted
    pass_l2 = client.post(
        "/api/v1/mastery/calibration",
        headers=headers_t1,
        json={
            "question_version_id": str(qv_id),
            "sample_size": 220,
            "se_b": 0.22,
            "learner_count": 180,
            "ability_sd": 0.75,
            "infit_msq": 1.02,
            "max_abs_dif": 0.15,
            "difficulty_b": "0.4500",
        },
    )
    assert pass_l2.status_code == 200, pass_l2.text
    l2_body = pass_l2.json()
    assert l2_body["persisted"] is True
    assert l2_body["selected_tier"] == "RASCH_1PL_L2"

    # Pass L3 -> upgrades to IRT_2PL_GPCM_L3
    pass_l3 = client.post(
        "/api/v1/mastery/calibration",
        headers=headers_t1,
        json={
            "question_version_id": str(qv_id),
            "sample_size": 650,
            "se_b": 0.12,
            "learner_count": 500,
            "ability_sd": 0.85,
            "infit_msq": 0.98,
            "max_abs_dif": 0.10,
            "difficulty_b": "0.4000",
            "discrimination_a": "1.3500",
        },
    )
    assert pass_l3.status_code == 200, pass_l3.text
    assert pass_l3.json()["selected_tier"] == "IRT_2PL_GPCM_L3"

    # Non-converged L4 attempt -> falls back to RULE_WEIGHTED_L1, persisted=False without overwriting L3 row
    bad_l4 = client.post(
        "/api/v1/mastery/calibration",
        headers=headers_t1,
        json={
            "question_version_id": str(qv_id),
            "sample_size": 1500,
            "se_b": 0.09,
            "learner_count": 1200,
            "converged": False,
            "difficulty_b": "1.5000",
        },
    )
    assert bad_l4.status_code == 200, bad_l4.text
    assert bad_l4.json()["persisted"] is False
    assert bad_l4.json()["selected_tier"] == "RULE_WEIGHTED_L1"
    assert run(count_irt_rows()) == "IRT_2PL_GPCM_L3"

    # Foreign tenant cannot calibrate tenant A's question version
    foreign = client.post(
        "/api/v1/mastery/calibration",
        headers=headers_tb,
        json={
            "question_version_id": str(qv_id),
            "sample_size": 250,
            "se_b": 0.18,
            "learner_count": 200,
        },
    )
    assert foreign.status_code == 404


def test_recommendations_signals_evidence_feedback_and_rls(world: dict[str, uuid.UUID]) -> None:
    """E05 ranks Top-3 from error_notes + mastery_estimates + knowledge_edges, records feedback & enforces RLS."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_s1 = _auth_headers(tenant_id=ta, membership_id=world["m_s1"], user_id=world["u_s1"], roles=["LEARNER"])
    headers_s2 = _auth_headers(tenant_id=ta, membership_id=world["m_s2"], user_id=world["u_s2"], roles=["LEARNER"])
    headers_tb = _auth_headers(tenant_id=tb, membership_id=world["m_tb"], user_id=world["u_tb"], roles=["TEACHER"])

    async def seed_signals() -> dict[str, uuid.UUID]:
        t1, t2, t3, t4, t5 = (uuid.uuid4() for _ in range(5))
        async with SessionLocal() as session, session.begin():
            for idx, (tid_node, code, title) in enumerate(
                [
                    (t1, f"R1_{t1.hex[:6]}", "Linear Fundamentals"),
                    (t2, f"R2_{t2.hex[:6]}", "Quadratic Equations"),
                    (t3, f"R3_{t3.hex[:6]}", "Probability Bayes"),
                    (t4, f"R4_{t4.hex[:6]}", "Inequalities"),
                    (t5, f"R5_{t5.hex[:6]}", "Mastered Topic"),
                ],
                start=1,
            ):
                session.add(
                    TaxonomyNode(
                        id=tid_node,
                        tenant_id=ta,
                        visibility_scope="TENANT_PRIVATE",
                        node_type="TOPIC",
                        code=code,
                        code_path=f"MATH.REC.N{idx}",
                        is_active=True,
                    )
                )
                await session.flush()
                session.add(
                    TaxonomyNodeVersion(
                        tenant_id=ta,
                        taxonomy_node_id=tid_node,
                        version_no=1,
                        name=title,
                    )
                )

            # Prerequisite edge: T1 -> T2
            await session.execute(
                text(
                    "INSERT INTO knowledge_edges (tenant_id, source_node_id, target_node_id, edge_type) "
                    "VALUES (:tid, :src, :dst, 'PREREQUISITE')"
                ),
                {"tid": ta, "src": t1, "dst": t2},
            )

            # Mastery estimates for student 1: T1 is weak prereq (0.35), T2 is weak (0.45), T5 mastered (0.90)
            for node_id, prob in [
                (t1, Decimal("0.350")),
                (t2, Decimal("0.450")),
                (t3, Decimal("0.550")),
                (t4, Decimal("0.650")),
                (t5, Decimal("0.900")),
            ]:
                await session.execute(
                    text(
                        "INSERT INTO mastery_estimates "
                        "(tenant_id, membership_id, taxonomy_node_id, model_code, mastery_probability, status) "
                        "VALUES (:tid, :mid, :nid, 'RULE_WEIGHTED_L1', :prob, 'INSUFFICIENT_DATA')"
                    ),
                    {"tid": ta, "mid": world["m_s1"], "nid": node_id, "prob": prob},
                )

            # Unresolved error_notes on T2 and T3
            _, qv2 = await _insert_question(session, tenant_id=ta, taxonomy_node_id=t2, stable_code=f"E2-{t2.hex[:6]}")
            _, qv3 = await _insert_question(session, tenant_id=ta, taxonomy_node_id=t3, stable_code=f"E3-{t3.hex[:6]}")
            for qvid, nid in ((qv2, t2), (qv3, t3)):
                await session.execute(
                    text(
                        "INSERT INTO error_notes "
                        "(tenant_id, membership_id, question_version_id, primary_taxonomy_node_id, status) "
                        "VALUES (:tid, :mid, :qvid, :nid, 'OPEN')"
                    ),
                    {"tid": ta, "mid": world["m_s1"], "qvid": qvid, "nid": nid},
                )
        return {"t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5}

    tax = run(seed_signals())

    # First GET computes and persists Top 3
    res_get = client.get("/api/v1/recommendations", headers=headers_s1)
    assert res_get.status_code == 200, res_get.text
    items = res_get.json()["items"]
    assert len(items) == 3
    recommended_targets = [it["target_entity_id"] for it in items]
    assert str(tax["t5"]) not in recommended_targets
    assert str(tax["t2"]) in recommended_targets

    rec_t2 = next(it for it in items if it["target_entity_id"] == str(tax["t2"]))
    assert rec_t2["recommended_action_type"] == "REVIEW_PREREQUISITE"
    assert str(tax["t1"]) in rec_t2["evidence_json"]["weak_prerequisite_ids"]
    assert len(rec_t2["evidence_json"]["failed_question_ids"]) == 1
    assert rec_t2["evidence_json"]["rationale_text"]

    # Learner s2 does NOT see s1's recommendations and cannot submit feedback on them
    s2_forbid = client.post(
        f"/api/v1/recommendations/{rec_t2['id']}/feedback",
        headers=headers_s2,
        json={"feedback": "DISMISSED"},
    )
    assert s2_forbid.status_code == 404

    # HELPFUL keeps recommendation active & visible
    rec_first = items[0]
    res_helpful = client.post(
        f"/api/v1/recommendations/{rec_first['id']}/feedback",
        headers=headers_s1,
        json={"feedback": "HELPFUL", "note": "Good next step"},
    )
    assert res_helpful.status_code == 200
    assert res_helpful.json()["hidden"] is False
    assert res_helpful.json()["status"] == "ACTIVE"

    # DISMISSED hides recommendation permanently from GET and refresh
    rec_dismiss = items[1]
    res_dismiss = client.post(
        f"/api/v1/recommendations/{rec_dismiss['id']}/feedback",
        headers=headers_s1,
        json={"feedback": "DISMISSED"},
    )
    assert res_dismiss.status_code == 200
    assert res_dismiss.json()["hidden"] is True
    assert res_dismiss.json()["status"] == "DISMISSED"

    after_dismiss = client.get("/api/v1/recommendations", headers=headers_s1)
    assert after_dismiss.status_code == 200
    remaining_ids = [it["id"] for it in after_dismiss.json()["items"]]
    assert rec_dismiss["id"] not in remaining_ids

    refreshed = client.get("/api/v1/recommendations?refresh=true", headers=headers_s1)
    assert refreshed.status_code == 200
    refreshed_targets = [it["target_entity_id"] for it in refreshed.json()["items"]]
    assert rec_dismiss["target_entity_id"] not in refreshed_targets

    # RLS isolates Tenant B completely
    res_tb = client.get("/api/v1/recommendations", headers=headers_tb)
    assert res_tb.status_code == 200
    assert res_tb.json()["items"] == []


def test_teacher_copilot_class_scope_and_confirmation_gate(world: dict[str, uuid.UUID]) -> None:
    """E06 clusters class-scoped errors from DB/payload, blocks cross-class/teacher/tenant, requires confirm."""
    client = _api_client()
    ta, tb = world["ta"], world["tb"]
    headers_t1 = _auth_headers(tenant_id=ta, membership_id=world["m_t1"], user_id=world["u_t1"], roles=["TEACHER"])
    headers_t2 = _auth_headers(tenant_id=ta, membership_id=world["m_t2"], user_id=world["u_t2"], roles=["TEACHER"])
    headers_s1 = _auth_headers(tenant_id=ta, membership_id=world["m_s1"], user_id=world["u_s1"], roles=["LEARNER"])
    headers_tb = _auth_headers(tenant_id=tb, membership_id=world["m_tb"], user_id=world["u_tb"], roles=["TEACHER"])

    async def seed_lms_and_attempts() -> dict[str, uuid.UUID]:
        class_a1, class_a2 = uuid.uuid4(), uuid.uuid4()
        assess_id, av_id, scheme_id, assign_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        tax_id = uuid.uuid4()

        async with SessionLocal() as session, session.begin():
            session.add(
                TaxonomyNode(
                    id=tax_id,
                    tenant_id=ta,
                    visibility_scope="TENANT_PRIVATE",
                    node_type="TOPIC",
                    code=f"TC_{tax_id.hex[:6]}",
                    code_path="MATH.TC.ERR",
                    is_active=True,
                )
            )
            await session.flush()
            _, qv_id = await _insert_question(
                session,
                tenant_id=ta,
                taxonomy_node_id=tax_id,
                stable_code=f"TCQ-{tax_id.hex[:6]}",
            )

            await session.execute(
                text(
                    "INSERT INTO classes (id, tenant_id, teacher_membership_id, name, academic_year) "
                    "VALUES (:cid1, :tid, :t1, 'Class 12A1', '2026'), (:cid2, :tid, :t2, 'Class 12A2', '2026')"
                ),
                {"cid1": class_a1, "cid2": class_a2, "tid": ta, "t1": world["m_t1"], "t2": world["m_t2"]},
            )
            await session.execute(
                text(
                    "INSERT INTO class_members (tenant_id, class_id, membership_id, role_in_class, status) "
                    "VALUES (:tid, :c1, :s1, 'STUDENT', 'ACTIVE'), "
                    "(:tid, :c1, :s2, 'STUDENT', 'ACTIVE'), "
                    "(:tid, :c2, :s3, 'STUDENT', 'ACTIVE')"
                ),
                {
                    "tid": ta,
                    "c1": class_a1,
                    "c2": class_a2,
                    "s1": world["m_s1"],
                    "s2": world["m_s2"],
                    "s3": world["m_s3"],
                },
            )
            await session.execute(
                text(
                    "INSERT INTO assessments (id, tenant_id, title, subject_code, grade_band, exam_type_code) "
                    "VALUES (:aid, :tid, 'Midterm', 'MATH', 'THPT', 'THPT_2025')"
                ),
                {"aid": assess_id, "tid": ta},
            )
            await session.execute(
                text("INSERT INTO scoring_scheme_versions (id, code, rules_json) VALUES (:sid, :scode, '{}'::jsonb)"),
                {"sid": scheme_id, "scode": f"SCHEME-{scheme_id.hex[:8]}"},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_versions "
                    "(id, tenant_id, assessment_id, version_no, scoring_scheme_id, "
                    "duration_seconds, structure_snapshot_json) "
                    "VALUES (:avid, :tid, :aid, 1, :sid, 3600, '{}'::jsonb)"
                ),
                {"avid": av_id, "tid": ta, "aid": assess_id, "sid": scheme_id},
            )
            await session.execute(
                text(
                    "INSERT INTO assessment_assignments (id, tenant_id, assessment_version_id, class_id, assigned_by) "
                    "VALUES (:asgid, :tid, :avid, :c1, :u_t1)"
                ),
                {"asgid": assign_id, "tid": ta, "avid": av_id, "c1": class_a1, "u_t1": world["u_t1"]},
            )

            # Wrong responses for class_a1 students (s1, s2) AND an attempt from outside class_a2 student (s3)
            for mid, chosen in ((world["m_s1"], "B"), (world["m_s2"], "B"), (world["m_s3"], "C")):
                att_id, grade_id = uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO attempts "
                        "(id, tenant_id, assessment_version_id, assignment_id, membership_id, "
                        "status, attempt_seed, question_order_ids, deadline_at) "
                        "VALUES (:att, :tid, :avid, :asgid, :mid, 'GRADED', 42, ARRAY[:qvid]::uuid[], NOW())"
                    ),
                    {"att": att_id, "tid": ta, "avid": av_id, "asgid": assign_id, "mid": mid, "qvid": qv_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO responses (tenant_id, attempt_id, question_version_id, response_payload) "
                        "VALUES (:tid, :att, :qvid, jsonb_build_object('chosen_option', CAST(:opt AS text)))"
                    ),
                    {"tid": ta, "att": att_id, "qvid": qv_id, "opt": chosen},
                )
                await session.execute(
                    text(
                        "INSERT INTO grades "
                        "(id, tenant_id, attempt_id, scoring_scheme_id, total_score, "
                        "section_breakdown_json, item_details_json) "
                        "VALUES (:gid, :tid, :att, :sid, 0.00, '{}'::jsonb, '[]'::jsonb)"
                    ),
                    {"gid": grade_id, "tid": ta, "att": att_id, "sid": scheme_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO grading_details "
                        "(tenant_id, grade_id, question_version_id, earned_points, max_points, is_correct) "
                        "VALUES (:tid, :gid, :qvid, 0.00, 1.00, FALSE)"
                    ),
                    {"tid": ta, "gid": grade_id, "qvid": qv_id},
                )
        return {"class_a1": class_a1, "class_a2": class_a2, "assign_id": assign_id, "tax_id": tax_id, "qv_id": qv_id}

    seeded = run(seed_lms_and_attempts())

    # 1) Another teacher in SAME tenant cannot run copilot on class_a1
    other_teacher = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_t2,
        json={"class_id": str(seeded["class_a1"]), "assignment_id": str(seeded["assign_id"])},
    )
    assert other_teacher.status_code == 403

    # 2) Student role is blocked
    student_try = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_s1,
        json={"class_id": str(seeded["class_a1"]), "assignment_id": str(seeded["assign_id"])},
    )
    assert student_try.status_code == 403

    # 3) Foreign tenant teacher gets 404 (RLS hides class_a1)
    foreign_try = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_tb,
        json={"class_id": str(seeded["class_a1"]), "assignment_id": str(seeded["assign_id"])},
    )
    assert foreign_try.status_code == 404

    # 4) Cross-class or non-roster student observations are rejected with 403
    cross_class_obs = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_t1,
        json={
            "class_id": str(seeded["class_a1"]),
            "assignment_id": str(seeded["assign_id"]),
            "observations": [
                {
                    "tenant_id": str(ta),
                    "class_id": str(seeded["class_a2"]),
                    "student_id": str(world["m_s3"]),
                    "taxonomy_node_id": str(seeded["tax_id"]),
                    "question_id": str(seeded["qv_id"]),
                    "chosen_option": "C",
                    "correct_option": "A",
                }
            ],
        },
    )
    assert cross_class_obs.status_code == 403

    non_roster_obs = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_t1,
        json={
            "class_id": str(seeded["class_a1"]),
            "assignment_id": str(seeded["assign_id"]),
            "observations": [
                {
                    "tenant_id": str(ta),
                    "class_id": str(seeded["class_a1"]),
                    "student_id": str(world["m_s3"]),
                    "taxonomy_node_id": str(seeded["tax_id"]),
                    "question_id": str(seeded["qv_id"]),
                    "chosen_option": "C",
                    "correct_option": "A",
                }
            ],
        },
    )
    assert non_roster_obs.status_code == 403

    # 5) Owning teacher runs AI assist reading DB attempts: strictly class_a1 students (s1, s2), NEVER s3!
    res_create = client.post(
        "/api/v1/teacher/ai-assist",
        headers=headers_t1,
        json={"class_id": str(seeded["class_a1"]), "assignment_id": str(seeded["assign_id"])},
    )
    assert res_create.status_code == 201, res_create.text
    job = res_create.json()
    job_id = job["id"]
    assert job["status"] == "READY_FOR_TEACHER"
    assert len(job["clusters"]) == 1
    assert job["clusters"][0]["wrong_response_count"] == 2
    assert job["clusters"][0]["top_distractor"] == "B"
    assert str(world["m_s3"]) not in job["clusters"][0]["affected_student_ids"]

    # Every draft stays AWAITING_TEACHER_CONFIRMATION until explicit confirm
    assert len(job["draft_assignments"]) == 1
    assert job["draft_assignments"][0]["status"] == "AWAITING_TEACHER_CONFIRMATION"
    assert job["draft_assignments"][0]["confirmed_by_teacher_id"] is None

    # GET verifies state remains unconfirmed and cross-teacher / cross-tenant GET is blocked
    assert client.get(f"/api/v1/teacher/ai-assist/{job_id}", headers=headers_t2).status_code == 403
    assert client.get(f"/api/v1/teacher/ai-assist/{job_id}", headers=headers_tb).status_code == 404
    fetched = client.get(f"/api/v1/teacher/ai-assist/{job_id}", headers=headers_t1)
    assert fetched.status_code == 200
    assert fetched.json()["draft_assignments"][0]["status"] == "AWAITING_TEACHER_CONFIRMATION"

    # Cross-teacher confirm blocked (403); foreign tenant confirm blocked (404)
    assert client.post(f"/api/v1/teacher/ai-assist/{job_id}/confirm", headers=headers_t2).status_code == 403
    assert client.post(f"/api/v1/teacher/ai-assist/{job_id}/confirm", headers=headers_tb).status_code == 404

    # 6) Explicit owning-teacher confirm flips drafts to CONFIRMED
    res_confirm = client.post(f"/api/v1/teacher/ai-assist/{job_id}/confirm", headers=headers_t1)
    assert res_confirm.status_code == 200, res_confirm.text
    confirmed = res_confirm.json()
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["draft_assignments"][0]["status"] == "CONFIRMED"
    assert confirmed["draft_assignments"][0]["confirmed_by_teacher_id"] == str(world["m_t1"])

    # Re-confirming already confirmed draft -> 409 Conflict
    re_confirm = client.post(f"/api/v1/teacher/ai-assist/{job_id}/confirm", headers=headers_t1)
    assert re_confirm.status_code == 409
