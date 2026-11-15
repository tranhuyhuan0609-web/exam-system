"""PostgreSQL + RLS + FastAPI integration tests for TASK-30 (Sprint 8):
- F01 classes (`groups`, `classes`, `class_members` soft-delete INACTIVE, history preserved, recipients filtered)
- F02 assignments (`assessment_assignments`, windows/max_attempts, 4 reveal policies, `ulp_exam_session_rw` SELECT)
- F03 gradebook (`gradebook_projections`, analytics + Top 5, `ulp_worker_grading`, INACTIVE + CSV escape)
- F06 notifications (`notifications`, `notification_deliveries` Decision #7 `tenant_id`, dedup, inbox, prefs)
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
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

from app.config import get_settings
from app.db import SessionLocal, engine
from app.modules.assignments.internal.models import AssessmentAssignment  # noqa: F401
from app.modules.assignments.public import get_assignment
from app.modules.assignments.router import router as assignments_router
from app.modules.classes.internal.models import ClassMember, Group, SchoolClass  # noqa: F401
from app.modules.classes.public import get_active_class_recipients
from app.modules.classes.router import router as classes_router
from app.modules.gradebook.internal.models import GradebookProjection  # noqa: F401
from app.modules.gradebook.public import (
    ItemResponseObservation,
    StudentAttemptGrade,
    recompute_gradebook_projection,
)
from app.modules.gradebook.router import router as gradebook_router
from app.modules.notifications.internal.models import Notification, NotificationDelivery  # noqa: F401
from app.modules.notifications.public import (
    NotificationCategory,
    NotificationChannel,
    NotificationCommand,
    dispatch_notification,
    handle_attempt_graded_notification,
)
from app.modules.notifications.router import router as notifications_router
from app.modules.privacy.internal.models import Membership, User
from app.modules.taxonomy.internal.models import TaxonomyNode
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def _api_client() -> TestClient:
    test_app = FastAPI()
    test_app.include_router(classes_router)
    test_app.include_router(assignments_router)
    test_app.include_router(gradebook_router)
    test_app.include_router(notifications_router)
    return TestClient(test_app)


def _auth_headers(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: list[str] | None = None,
) -> dict[str, str]:
    settings = get_settings()
    token = mint_hs256_token(
        secret=settings.secret_key,
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles or ["LEARNER"],
        entitlement_tier="STUDENT_PRO",
        issued_at=int(time.time()),
        ttl_seconds=900,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module", autouse=True)
def _ensure_task30_schema(_database: None):
    """Create F01+F02+F03+F06 tables & missing companion stubs; DROP CASCADE on teardown."""
    core_tables = (
        "groups",
        "classes",
        "class_members",
        "assessment_assignments",
        "gradebook_projections",
        "notifications",
        "notification_deliveries",
    )
    stub_ddl = {
        "assessments": """
            CREATE TABLE IF NOT EXISTS assessments (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                title VARCHAR(255) NOT NULL DEFAULT 'Sample Assessment',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (id, tenant_id)
            )
        """,
        "assessment_versions": """
            CREATE TABLE IF NOT EXISTS assessment_versions (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL,
                assessment_id UUID NOT NULL,
                version_no INT NOT NULL DEFAULT 1,
                is_immutable BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (id, tenant_id),
                FOREIGN KEY (assessment_id, tenant_id) REFERENCES assessments(id, tenant_id) ON DELETE CASCADE
            )
        """,
        "scoring_scheme_versions": """
            CREATE TABLE IF NOT EXISTS scoring_scheme_versions (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                code VARCHAR(60) NOT NULL DEFAULT 'DEFAULT_10',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (id, tenant_id)
            )
        """,
        "attempts": """
            CREATE TABLE IF NOT EXISTS attempts (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
                assessment_version_id UUID NOT NULL REFERENCES assessment_versions(id) ON DELETE RESTRICT,
                assignment_id UUID,
                membership_id UUID NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'IN_PROGRESS'
                    CHECK (status IN ('IN_PROGRESS', 'SUBMITTED', 'AUTO_SUBMITTED', 'GRADED', 'VOIDED')),
                attempt_seed BIGINT NOT NULL DEFAULT 42,
                question_order_ids UUID[] NOT NULL DEFAULT '{}',
                option_permutations_json JSONB NOT NULL DEFAULT '{}',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deadline_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                submitted_at TIMESTAMPTZ,
                submit_idempotency_key VARCHAR(128) UNIQUE,
                UNIQUE (id, tenant_id),
                FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
            )
        """,
        "responses": """
            CREATE TABLE IF NOT EXISTS responses (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL,
                attempt_id UUID NOT NULL,
                question_version_id UUID NOT NULL REFERENCES question_versions(id) ON DELETE RESTRICT,
                current_sequence BIGINT NOT NULL DEFAULT 0,
                current_revision_no INT NOT NULL DEFAULT 0,
                last_idempotency_key VARCHAR(128),
                response_payload JSONB NOT NULL,
                is_flagged BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (attempt_id, question_version_id),
                UNIQUE (id, tenant_id),
                FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE CASCADE
            )
        """,
        "grades": """
            CREATE TABLE IF NOT EXISTS grades (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL,
                attempt_id UUID NOT NULL,
                revision_no INT NOT NULL DEFAULT 1,
                is_current BOOLEAN NOT NULL DEFAULT TRUE,
                grade_reason VARCHAR(40) NOT NULL DEFAULT 'INITIAL'
                    CHECK (grade_reason IN ('INITIAL', 'APPEAL_REGRADE', 'ERRATA_REGRADE', 'MANUAL_OVERRIDE')),
                scoring_scheme_id UUID NOT NULL REFERENCES scoring_scheme_versions(id),
                total_score NUMERIC(6,2) NOT NULL,
                section_breakdown_json JSONB NOT NULL DEFAULT '{}',
                item_details_json JSONB NOT NULL DEFAULT '[]',
                graded_by UUID REFERENCES users(id),
                graded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                released_at TIMESTAMPTZ,
                UNIQUE (attempt_id, revision_no),
                UNIQUE (id, tenant_id),
                FOREIGN KEY (attempt_id, tenant_id) REFERENCES attempts(id, tenant_id) ON DELETE RESTRICT
            )
        """,
        "grading_details": """
            CREATE TABLE IF NOT EXISTS grading_details (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
                tenant_id UUID NOT NULL,
                grade_id UUID NOT NULL,
                question_version_id UUID NOT NULL REFERENCES question_versions(id),
                earned_points NUMERIC(5,2) NOT NULL,
                max_points NUMERIC(5,2) NOT NULL,
                is_correct BOOLEAN NOT NULL,
                sub_item_breakdown_json JSONB NOT NULL DEFAULT '{}',
                FOREIGN KEY (grade_id, tenant_id) REFERENCES grades(id, tenant_id) ON DELETE CASCADE
            )
        """,
    }
    module_ddl = (
        """
        CREATE TABLE IF NOT EXISTS groups (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS classes (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            group_id UUID,
            teacher_membership_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL,
            academic_year VARCHAR(30),
            invite_code VARCHAR(30) UNIQUE,
            status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'ARCHIVED')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (group_id, tenant_id) REFERENCES groups(id, tenant_id) ON DELETE SET NULL,
            FOREIGN KEY (teacher_membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS class_members (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            class_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            role_in_class VARCHAR(30) NOT NULL DEFAULT 'STUDENT'
                CHECK (role_in_class IN ('STUDENT', 'TEACHING_ASSISTANT')),
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'INACTIVE')),
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            left_at TIMESTAMPTZ,
            UNIQUE (class_id, membership_id),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE,
            FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS assessment_assignments (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            assessment_version_id UUID NOT NULL,
            class_id UUID,
            group_id UUID,
            assigned_by UUID NOT NULL REFERENCES users(id),
            available_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            due_at TIMESTAMPTZ,
            max_attempts INT NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
            reveal_policy VARCHAR(40) NOT NULL DEFAULT 'AFTER_DUE'
                CHECK (reveal_policy IN ('IMMEDIATE', 'AFTER_DUE', 'MANUAL_RELEASE', 'NEVER')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (assessment_version_id, tenant_id) REFERENCES assessment_versions(id, tenant_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS gradebook_projections (
            class_id UUID NOT NULL,
            assignment_id UUID NOT NULL,
            tenant_id UUID NOT NULL,
            stats_json JSONB NOT NULL DEFAULT '{}',
            top_wrong_questions_json JSONB NOT NULL DEFAULT '[]',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (class_id, assignment_id),
            FOREIGN KEY (class_id, tenant_id) REFERENCES classes(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL,
            membership_id UUID NOT NULL,
            dedup_key VARCHAR(160) UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            body_text TEXT NOT NULL,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (id, tenant_id),
            FOREIGN KEY (membership_id, tenant_id) REFERENCES memberships(id, tenant_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            notification_id UUID NOT NULL,
            channel VARCHAR(30) NOT NULL CHECK (channel IN ('IN_APP', 'EMAIL')),
            status VARCHAR(30) NOT NULL DEFAULT 'SENT',
            delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (notification_id, channel),
            FOREIGN KEY (notification_id, tenant_id) REFERENCES notifications(id, tenant_id) ON DELETE CASCADE
        )
        """,
    )

    created_stubs: list[str] = []

    async def create() -> None:
        async with engine.begin() as conn:
            preexisting = set(
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:names)"
                        ),
                        {"names": list(stub_ddl)},
                    )
                )
                .scalars()
                .all()
            )
            for name, ddl in stub_ddl.items():
                if name not in preexisting:
                    created_stubs.append(name)
                    await conn.execute(text(ddl))
                    await conn.execute(text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
                    await conn.execute(text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
                    await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {name}"))
                    await conn.execute(
                        text(
                            f"CREATE POLICY tenant_isolation_policy ON {name} "
                            "USING (tenant_id = (SELECT current_app_tenant_id())) "
                            "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                        )
                    )
                    await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO ulp_app_rw"))

            for ddl in module_ddl:
                await conn.execute(text(ddl))
            for tbl in core_tables:
                await conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
                await conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
                await conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {tbl}"))
                await conn.execute(
                    text(
                        f"CREATE POLICY tenant_isolation_policy ON {tbl} "
                        "USING (tenant_id = (SELECT current_app_tenant_id())) "
                        "WITH CHECK (tenant_id = (SELECT current_app_tenant_id()))"
                    )
                )
                await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO ulp_app_rw"))

            await conn.execute(text("GRANT SELECT ON assessment_assignments TO ulp_exam_session_rw"))
            await conn.execute(text("GRANT SELECT, INSERT, UPDATE ON gradebook_projections TO ulp_worker_grading"))
            for tbl in ("assessment_versions", "attempts", "responses", "scoring_scheme_versions"):
                if tbl in created_stubs:
                    await conn.execute(text(f"GRANT SELECT ON {tbl} TO ulp_worker_grading"))
            for tbl in ("grades", "grading_details"):
                if tbl in created_stubs:
                    await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {tbl} TO ulp_worker_grading"))
            if "attempts" in created_stubs:
                await conn.execute(text("GRANT SELECT, INSERT, UPDATE ON attempts TO ulp_exam_session_rw"))
            if "assessment_versions" in created_stubs:
                await conn.execute(text("GRANT SELECT ON assessment_versions TO ulp_exam_session_rw"))

    async def drop() -> None:
        async with engine.begin() as conn:
            to_drop = [
                "notification_deliveries",
                "notifications",
                "gradebook_projections",
                "assessment_assignments",
                "class_members",
                "classes",
                "groups",
                *reversed(created_stubs),
            ]
            await conn.execute(text(f"DROP TABLE IF EXISTS {', '.join(to_drop)} CASCADE"))

    run(create())
    yield
    run(drop())


@pytest.fixture
def world() -> dict[str, uuid.UUID]:
    """Seed two isolated tenants with teacher, students, and TA memberships."""

    async def create() -> dict[str, uuid.UUID]:
        ta, tb = uuid.uuid4(), uuid.uuid4()
        u_teacher, u_s1, u_s2, u_s3, u_s4, u_ta, u_b = (uuid.uuid4() for _ in range(7))
        m_teacher, m_s1, m_s2, m_s3, m_s4, m_ta, m_b = (uuid.uuid4() for _ in range(7))
        async with SessionLocal() as session, session.begin():
            session.add_all(
                [
                    Tenant(id=ta, slug=f"t30-a-{ta.hex[:8]}", name="School 30A"),
                    Tenant(id=tb, slug=f"t30-b-{tb.hex[:8]}", name="School 30B"),
                    User(id=u_teacher, email_blind_index=f"{u_teacher.hex}{u_teacher.hex}", pseudonym_handle="Cô Lan"),
                    User(
                        id=u_s1,
                        email_blind_index=f"{u_s1.hex}{u_s1.hex}",
                        pseudonym_handle="Nguyễn Minh An",
                    ),
                    User(
                        id=u_s2,
                        email_blind_index=f"{u_s2.hex}{u_s2.hex}",
                        pseudonym_handle='=HYPERLINK("http://evil.local","Trần Bảo")',
                    ),
                    User(id=u_s3, email_blind_index=f"{u_s3.hex}{u_s3.hex}", pseudonym_handle="+1+1"),
                    User(id=u_s4, email_blind_index=f"{u_s4.hex}{u_s4.hex}", pseudonym_handle="Phạm Hùng"),
                    User(id=u_ta, email_blind_index=f"{u_ta.hex}{u_ta.hex}", pseudonym_handle="Trợ Giảng"),
                    User(id=u_b, email_blind_index=f"{u_b.hex}{u_b.hex}", pseudonym_handle="TenantB User"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Membership(id=m_teacher, tenant_id=ta, user_id=u_teacher, roles=["TEACHER"]),
                    Membership(id=m_s1, tenant_id=ta, user_id=u_s1, roles=["LEARNER"]),
                    Membership(id=m_s2, tenant_id=ta, user_id=u_s2, roles=["LEARNER"]),
                    Membership(id=m_s3, tenant_id=ta, user_id=u_s3, roles=["LEARNER"]),
                    Membership(id=m_s4, tenant_id=ta, user_id=u_s4, roles=["LEARNER"]),
                    Membership(id=m_ta, tenant_id=ta, user_id=u_ta, roles=["LEARNER"]),
                    Membership(id=m_b, tenant_id=tb, user_id=u_b, roles=["TEACHER"]),
                ]
            )
        return {
            "ta": ta,
            "tb": tb,
            "u_teacher": u_teacher,
            "u_s1": u_s1,
            "u_s2": u_s2,
            "u_s3": u_s3,
            "u_s4": u_s4,
            "u_ta": u_ta,
            "u_b": u_b,
            "m_teacher": m_teacher,
            "m_s1": m_s1,
            "m_s2": m_s2,
            "m_s3": m_s3,
            "m_s4": m_s4,
            "m_ta": m_ta,
            "m_b": m_b,
        }

    return run(create())


async def _seed_assessment_bundle(
    session,
    *,
    tenant_id: uuid.UUID,
    stable_prefix: str,
    question_count: int = 1,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Seed assessment + assessment_version + scoring_scheme + N published question versions."""
    tax_id = uuid.uuid4()
    session.add(
        TaxonomyNode(
            id=tax_id,
            tenant_id=tenant_id,
            visibility_scope="TENANT_PRIVATE",
            node_type="CONCEPT",
            code=f"TAX_{stable_prefix}",
            code_path=f"TOAN.TAX_{stable_prefix}",
            is_active=True,
        )
    )
    await session.flush()

    qv_ids: list[uuid.UUID] = []
    for idx in range(question_count):
        qid, qvid = uuid.uuid4(), uuid.uuid4()
        code = f"{stable_prefix}_Q{idx + 1}_{qid.hex[:6]}"
        chash = hashlib.sha256(f"c:{code}".encode()).hexdigest()
        shash = hashlib.sha256(f"s:{code}".encode()).hexdigest()
        await session.execute(
            text(
                "INSERT INTO questions ("
                "id, tenant_id, visibility_scope, stable_code, item_type, primary_taxonomy_node_id, "
                "verification_status, rights_status, publication_status"
                ") VALUES ("
                ":qid, :tid, 'TENANT_PRIVATE', :code, 'SINGLE_CHOICE', :nid, "
                "'VERIFIED', 'OWNED', 'DRAFT')"
            ),
            {"qid": qid, "tid": tenant_id, "code": code, "nid": tax_id},
        )
        await session.execute(
            text(
                "INSERT INTO question_versions ("
                "id, tenant_id, visibility_scope, question_id, version_no, "
                "stem_json, options_json, content_hash, structural_hash"
                ") VALUES ("
                ":qvid, :tid, 'TENANT_PRIVATE', :qid, 1, "
                "CAST(:stem AS jsonb), CAST(:opts AS jsonb), :ch, :sh)"
            ),
            {
                "qvid": qvid,
                "tid": tenant_id,
                "qid": qid,
                "stem": json.dumps({"text_md": f"Question {idx + 1}"}),
                "opts": json.dumps([{"key": "A", "text": "A"}, {"key": "B", "text": "B"}]),
                "ch": chash,
                "sh": shash,
            },
        )
        await session.execute(
            text("UPDATE questions SET current_version_id = :qvid, publication_status = 'PUBLISHED' WHERE id = :qid"),
            {"qvid": qvid, "qid": qid},
        )
        qv_ids.append(qvid)

    assessment_id, assessment_version_id, scheme_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await session.execute(
        text("INSERT INTO assessments (id, tenant_id, title) VALUES (:aid, :tid, :title)"),
        {"aid": assessment_id, "tid": tenant_id, "title": f"Exam {stable_prefix}"},
    )
    await session.execute(
        text(
            "INSERT INTO assessment_versions (id, tenant_id, assessment_id, version_no) "
            "VALUES (:avid, :tid, :aid, 1)"
        ),
        {"avid": assessment_version_id, "tid": tenant_id, "aid": assessment_id},
    )
    await session.execute(
        text("INSERT INTO scoring_scheme_versions (id, tenant_id, code) VALUES (:sid, :tid, :code)"),
        {"sid": scheme_id, "tid": tenant_id, "code": f"SC_{stable_prefix}"},
    )
    return assessment_version_id, scheme_id, qv_ids


def test_pep562_lazy_public_exports_keep_core_imports_db_free() -> None:
    """Pure Core callers importing F01/F02/F03/F06 symbols must not load SQLAlchemy or ORM models."""
    code = (
        "import sys\n"
        "from app.modules.classes.public import soft_leave_class, ClassMemberState\n"
        "from app.modules.assignments.public import RevealPolicy, assert_can_start_assignment\n"
        "from app.modules.gradebook.public import compute_class_analytics, render_gradebook_csv\n"
        "from app.modules.notifications.public import dispatch_notification_idempotent, NotificationChannel\n"
        "loaded = set(sys.modules)\n"
        "for mod in ('classes', 'assignments', 'gradebook', 'notifications'):\n"
        "    assert f'app.modules.{mod}.internal.models' not in loaded, f'{mod} loaded ORM!'\n"
        "    assert f'app.modules.{mod}.internal.service' not in loaded, f'{mod} loaded service!'\n"
        "from app.modules.classes.public import create_class\n"
        "from app.modules.assignments.public import create_assignment\n"
        "from app.modules.gradebook.public import recompute_gradebook_projection\n"
        "from app.modules.notifications.public import dispatch_notification\n"
        "assert callable(create_class) and callable(create_assignment)\n"
        "assert callable(recompute_gradebook_projection) and callable(dispatch_notification)\n"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr or res.stdout


def test_f01_classes_soft_delete_preserves_history_and_rls(world: dict[str, uuid.UUID]) -> None:
    """F01 soft-deletes members (`INACTIVE`), keeps historical attempts/grades intact, and isolates tenants."""
    ta, tb = world["ta"], world["tb"]
    u_teacher, m_teacher = world["u_teacher"], world["m_teacher"]
    u_s1, m_s1, m_s2, m_ta = world["u_s1"], world["m_s1"], world["m_s2"], world["m_ta"]
    u_b, m_b = world["u_b"], world["m_b"]

    client = _api_client()
    t_headers = _auth_headers(tenant_id=ta, membership_id=m_teacher, user_id=u_teacher, roles=["TEACHER"])
    s1_headers = _auth_headers(tenant_id=ta, membership_id=m_s1, user_id=u_s1, roles=["LEARNER"])
    tb_headers = _auth_headers(tenant_id=tb, membership_id=m_b, user_id=u_b, roles=["TEACHER"])

    # Learners cannot create groups or classes
    forbidden = client.post("/api/v1/groups", json={"name": "12A"}, headers=s1_headers)
    assert forbidden.status_code == 403

    grp_res = client.post("/api/v1/groups", json={"name": "Khối 12 Toán"}, headers=t_headers)
    assert grp_res.status_code == 201, grp_res.text
    group_id = uuid.UUID(grp_res.json()["id"])

    cls_res = client.post(
        "/api/v1/classes",
        json={
            "name": "12A1 Chuyên Toán",
            "group_id": str(group_id),
            "academic_year": "2026-2027",
            "invite_code": f"INV-{ta.hex[:6]}",
        },
        headers=t_headers,
    )
    assert cls_res.status_code == 201, cls_res.text
    class_id = uuid.UUID(cls_res.json()["id"])

    for mid, role in ((m_s1, "STUDENT"), (m_s2, "STUDENT"), (m_ta, "TEACHING_ASSISTANT")):
        m_res = client.post(
            f"/api/v1/classes/{class_id}/members",
            json={"membership_id": str(mid), "role_in_class": role},
            headers=t_headers,
        )
        assert m_res.status_code == 201, m_res.text

    detail = client.get(f"/api/v1/classes/{class_id}", headers=t_headers)
    assert detail.status_code == 200
    assert detail.json()["active_student_count"] == 2

    # Seed historical submitted attempt + grade for Student 2 before leaving class
    async def seed_history() -> uuid.UUID:
        async with SessionLocal() as session, session.begin():
            av_id, scheme_id, _ = await _seed_assessment_bundle(session, tenant_id=ta, stable_prefix="F01HIST")
            att_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO attempts (id, tenant_id, assessment_version_id, membership_id, status, submitted_at) "
                    "VALUES (:aid, :tid, :avid, :mid, 'GRADED', NOW())"
                ),
                {"aid": att_id, "tid": ta, "avid": av_id, "mid": m_s2},
            )
            await session.execute(
                text(
                    "INSERT INTO grades (tenant_id, attempt_id, scoring_scheme_id, total_score) "
                    "VALUES (:tid, :aid, :sid, 8.50)"
                ),
                {"tid": ta, "aid": att_id, "sid": scheme_id},
            )
            return att_id

    hist_attempt_id = run(seed_history())

    # Soft-delete Student 2 via DELETE
    del_res = client.delete(f"/api/v1/classes/{class_id}/members/{m_s2}", headers=t_headers)
    assert del_res.status_code == 200, del_res.text
    del_body = del_res.json()
    assert del_body["status"] == "INACTIVE"
    assert del_body["left_at"] is not None

    async def verify_db_and_recipients() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session:
            # Row is NEVER physically deleted; historical grade stays linked cleanly
            row = (
                await session.execute(
                    select(ClassMember).where(ClassMember.class_id == class_id, ClassMember.membership_id == m_s2)
                )
            ).scalar_one()
            assert row.status == "INACTIVE"
            grade_val = (
                await session.execute(
                    text("SELECT total_score FROM grades WHERE attempt_id = :aid"),
                    {"aid": hist_attempt_id},
                )
            ).scalar_one()
            assert Decimal(str(grade_val)) == Decimal("8.50")

            recipients = await get_active_class_recipients(session, tenant_id=ta, class_id=class_id)
            assert [r.membership_id for r in recipients] == [str(m_s1)]

    run(verify_db_and_recipients())

    # Rejoin Student 2 sets status=ACTIVE and clears left_at
    rejoin_res = client.post(
        f"/api/v1/classes/{class_id}/members",
        json={"membership_id": str(m_s2), "role_in_class": "STUDENT"},
        headers=t_headers,
    )
    assert rejoin_res.status_code == 201
    assert rejoin_res.json()["status"] == "ACTIVE"
    assert rejoin_res.json()["left_at"] is None

    # Tenant B cannot see Tenant A's class under RLS
    assert client.get(f"/api/v1/classes/{class_id}", headers=tb_headers).status_code == 404
    assert client.get("/api/v1/groups", headers=tb_headers).json() == []


def test_f02_assignments_windows_attempts_reveal_policies_and_exam_role(world: dict[str, uuid.UUID]) -> None:
    """F02 enforces window/attempts/INACTIVE block, all 4 reveal policies, and ulp_exam_session_rw SELECT."""
    ta, tb = world["ta"], world["tb"]
    u_teacher, m_teacher = world["u_teacher"], world["m_teacher"]
    u_s1, m_s1, u_s2, m_s2 = world["u_s1"], world["m_s1"], world["u_s2"], world["m_s2"]
    u_b, m_b = world["u_b"], world["m_b"]

    async def seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        async with SessionLocal() as session, session.begin():
            av_id, scheme_id, _ = await _seed_assessment_bundle(session, tenant_id=ta, stable_prefix="F02TEST")
            cid = uuid.uuid4()
            session.add(
                SchoolClass(id=cid, tenant_id=ta, teacher_membership_id=m_teacher, name="12A2", status="ACTIVE")
            )
            await session.flush()
            session.add_all(
                [
                    ClassMember(
                        tenant_id=ta, class_id=cid, membership_id=m_s1, role_in_class="STUDENT", status="ACTIVE"
                    ),
                    ClassMember(
                        tenant_id=ta,
                        class_id=cid,
                        membership_id=m_s2,
                        role_in_class="STUDENT",
                        status="INACTIVE",
                        left_at=datetime.now(UTC) - timedelta(days=1),
                    ),
                ]
            )
            return av_id, scheme_id, cid

    av_id, scheme_id, class_id = run(seed())

    client = _api_client()
    t_headers = _auth_headers(tenant_id=ta, membership_id=m_teacher, user_id=u_teacher, roles=["TEACHER"])
    s1_headers = _auth_headers(tenant_id=ta, membership_id=m_s1, user_id=u_s1, roles=["LEARNER"])
    s2_headers = _auth_headers(tenant_id=ta, membership_id=m_s2, user_id=u_s2, roles=["LEARNER"])
    tb_headers = _auth_headers(tenant_id=tb, membership_id=m_b, user_id=u_b, roles=["TEACHER"])

    base_now = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    avail = base_now + timedelta(hours=1)
    due = base_now + timedelta(hours=2)

    asg_res = client.post(
        "/api/v1/assignments",
        json={
            "assessment_version_id": str(av_id),
            "class_id": str(class_id),
            "available_from": avail.isoformat(),
            "due_at": due.isoformat(),
            "max_attempts": 2,
            "reveal_policy": "AFTER_DUE",
        },
        headers=t_headers,
    )
    assert asg_res.status_code == 201, asg_res.text
    asg_body = asg_res.json()
    assignment_id = uuid.UUID(asg_body["id"])
    # INACTIVE leaver s2 excluded from new assignment recipients
    assert asg_body["active_recipient_membership_ids"] == [str(m_s1)]

    # 1. Start-check gates
    inactive_res = client.post(
        f"/api/v1/assignments/{assignment_id}/start-check",
        json={"now": (avail + timedelta(minutes=10)).isoformat()},
        headers=s2_headers,
    )
    assert inactive_res.status_code == 403
    assert inactive_res.json()["policy_code"] == "MEMBER_INACTIVE"

    before_open = client.post(
        f"/api/v1/assignments/{assignment_id}/start-check",
        json={"now": (avail - timedelta(minutes=5)).isoformat()},
        headers=s1_headers,
    )
    assert before_open.status_code == 403
    assert before_open.json()["policy_code"] == "ASSIGNMENT_NOT_OPEN_YET"

    past_due = client.post(
        f"/api/v1/assignments/{assignment_id}/start-check",
        json={"now": (due + timedelta(minutes=1)).isoformat()},
        headers=s1_headers,
    )
    assert past_due.status_code == 403
    assert past_due.json()["policy_code"] == "ASSIGNMENT_PAST_DUE"

    # Seed 1 SUBMITTED attempt + 1 VOIDED attempt (VOIDED does not consume max_attempts) + unreleased grade
    async def seed_attempts(count_valid: int) -> None:
        async with SessionLocal() as session, session.begin():
            if count_valid == 1:
                att_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO attempts "
                        "(id, tenant_id, assessment_version_id, assignment_id, membership_id, status, submitted_at) "
                        "VALUES (:id, :tid, :avid, :aid, :mid, 'GRADED', :sub)"
                    ),
                    {"id": att_id, "tid": ta, "avid": av_id, "aid": assignment_id, "mid": m_s1, "sub": avail},
                )
                await session.execute(
                    text(
                        "INSERT INTO grades (tenant_id, attempt_id, scoring_scheme_id, total_score) "
                        "VALUES (:tid, :att, :sid, 9.00)"
                    ),
                    {"tid": ta, "att": att_id, "sid": scheme_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO attempts "
                        "(tenant_id, assessment_version_id, assignment_id, membership_id, status) "
                        "VALUES (:tid, :avid, :aid, :mid, 'VOIDED')"
                    ),
                    {"tid": ta, "avid": av_id, "aid": assignment_id, "mid": m_s1},
                )
            else:
                await session.execute(
                    text(
                        "INSERT INTO attempts "
                        "(tenant_id, assessment_version_id, assignment_id, membership_id, status, submitted_at) "
                        "VALUES (:tid, :avid, :aid, :mid, 'SUBMITTED', :sub)"
                    ),
                    {"tid": ta, "avid": av_id, "aid": assignment_id, "mid": m_s1, "sub": avail},
                )

    run(seed_attempts(1))
    mid_window = avail + timedelta(minutes=15)
    ok_res = client.post(
        f"/api/v1/assignments/{assignment_id}/start-check",
        json={"now": mid_window.isoformat()},
        headers=s1_headers,
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["allowed"] is True
    assert ok_res.json()["attempts_used"] == 1

    run(seed_attempts(2))
    exhausted = client.post(
        f"/api/v1/assignments/{assignment_id}/start-check",
        json={"now": mid_window.isoformat()},
        headers=s1_headers,
    )
    assert exhausted.status_code == 403
    assert exhausted.json()["policy_code"] == "MAX_ATTEMPTS_EXCEEDED"

    # 2. All 4 RevealPolicy variants
    # AFTER_DUE: False before due, True at/after due
    r_before = client.get(
        f"/api/v1/assignments/{assignment_id}/reveal-status",
        params={"now": mid_window.isoformat()},
        headers=s1_headers,
    )
    assert r_before.status_code == 200 and r_before.json()["can_reveal_answers"] is False
    r_after = client.get(
        f"/api/v1/assignments/{assignment_id}/reveal-status",
        params={"now": due.isoformat()},
        headers=s1_headers,
    )
    assert r_after.status_code == 200 and r_after.json()["can_reveal_answers"] is True

    # IMMEDIATE: True when learner_submitted=True before due, False if unsubmitted
    assert (
        client.patch(
            f"/api/v1/assignments/{assignment_id}",
            json={"reveal_policy": "IMMEDIATE"},
            headers=t_headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/assignments/{assignment_id}/reveal-status",
            params={"now": mid_window.isoformat()},
            headers=s1_headers,
        ).json()["can_reveal_answers"]
        is True
    )
    assert (
        client.get(
            f"/api/v1/assignments/{assignment_id}/reveal-status",
            params={"now": mid_window.isoformat(), "learner_submitted": False},
            headers=s1_headers,
        ).json()["can_reveal_answers"]
        is False
    )

    # MANUAL_RELEASE: False until manually_released=True
    assert (
        client.patch(
            f"/api/v1/assignments/{assignment_id}",
            json={"reveal_policy": "MANUAL_RELEASE", "manually_released": False},
            headers=t_headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/assignments/{assignment_id}/reveal-status",
            params={"now": due.isoformat()},
            headers=s1_headers,
        ).json()["can_reveal_answers"]
        is False
    )
    rel_patch = client.patch(
        f"/api/v1/assignments/{assignment_id}",
        json={"manually_released": True},
        headers=t_headers,
    )
    assert rel_patch.status_code == 200 and rel_patch.json()["manually_released"] is True
    assert (
        client.get(
            f"/api/v1/assignments/{assignment_id}/reveal-status",
            params={"now": mid_window.isoformat()},
            headers=s1_headers,
        ).json()["can_reveal_answers"]
        is True
    )

    # NEVER: always False
    assert (
        client.patch(
            f"/api/v1/assignments/{assignment_id}",
            json={"reveal_policy": "NEVER"},
            headers=t_headers,
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/assignments/{assignment_id}/reveal-status",
            params={"now": (due + timedelta(days=5)).isoformat()},
            headers=s1_headers,
        ).json()["can_reveal_answers"]
        is False
    )

    # 3. ulp_exam_session_rw can SELECT from assessment_assignments directly
    async def check_exam_role() -> None:
        async with tenant_transaction(ta, role=DbRole.EXAM_SESSION_RW) as session:
            found = await get_assignment(session, tenant_id=ta, assignment_id=assignment_id)
            assert found.id == assignment_id
            assert found.max_attempts == 2

    run(check_exam_role())

    # 4. Tenant B RLS isolation
    assert client.get(f"/api/v1/assignments/{assignment_id}", headers=tb_headers).status_code == 404


def test_f03_gradebook_analytics_worker_grading_inactive_export_and_csv_escape(
    world: dict[str, uuid.UUID],
) -> None:
    """F03 preserves INACTIVE leavers, Top 5 distractors, CSV formula escape & WORKER_GRADING."""
    ta, tb = world["ta"], world["tb"]
    u_teacher, m_teacher = world["u_teacher"], world["m_teacher"]
    m_s1, m_s2, m_s3, m_s4 = world["m_s1"], world["m_s2"], world["m_s3"], world["m_s4"]
    u_b, m_b = world["u_b"], world["m_b"]

    due_at = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    async def seed() -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
        async with SessionLocal() as session, session.begin():
            av_id, scheme_id, qv_ids = await _seed_assessment_bundle(
                session, tenant_id=ta, stable_prefix="F03TEST", question_count=6
            )
            class_id = uuid.uuid4()
            assignment_id = uuid.uuid4()
            session.add(
                SchoolClass(id=class_id, tenant_id=ta, teacher_membership_id=m_teacher, name="12A3", status="ACTIVE")
            )
            await session.flush()
            session.add_all(
                [
                    ClassMember(
                        tenant_id=ta, class_id=class_id, membership_id=m_s1, role_in_class="STUDENT", status="ACTIVE"
                    ),
                    ClassMember(
                        tenant_id=ta,
                        class_id=class_id,
                        membership_id=m_s2,
                        role_in_class="STUDENT",
                        status="INACTIVE",
                        left_at=due_at + timedelta(hours=1),
                    ),
                    ClassMember(
                        tenant_id=ta, class_id=class_id, membership_id=m_s3, role_in_class="STUDENT", status="ACTIVE"
                    ),
                    ClassMember(
                        tenant_id=ta, class_id=class_id, membership_id=m_s4, role_in_class="STUDENT", status="ACTIVE"
                    ),
                ]
            )
            await session.flush()
            session.add(
                AssessmentAssignment(
                    id=assignment_id,
                    tenant_id=ta,
                    assessment_version_id=av_id,
                    class_id=class_id,
                    assigned_by=u_teacher,
                    available_from=due_at - timedelta(days=1),
                    due_at=due_at,
                    max_attempts=1,
                    reveal_policy="AFTER_DUE",
                )
            )
            await session.flush()

            # s1: on-time 9.00, s2 (INACTIVE leaver): on-time 7.50, s3: late 5.00, s4: unsubmitted
            specs = [
                (m_s1, due_at - timedelta(minutes=30), Decimal("9.00"), [True, True, False, True, True, True], "B"),
                (m_s2, due_at - timedelta(minutes=10), Decimal("7.50"), [False, False, False, True, True, True], "C"),
                (m_s3, due_at + timedelta(minutes=20), Decimal("5.00"), [False, False, False, False, False, True], "C"),
            ]
            for mid, sub_at, score, correctness, wrong_opt in specs:
                att_id, grade_id = uuid.uuid4(), uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO attempts (id, tenant_id, assessment_version_id, assignment_id, membership_id, "
                        "status, submitted_at) VALUES (:id, :tid, :avid, :aid, :mid, 'GRADED', :sub)"
                    ),
                    {"id": att_id, "tid": ta, "avid": av_id, "aid": assignment_id, "mid": mid, "sub": sub_at},
                )
                await session.execute(
                    text(
                        "INSERT INTO grades (id, tenant_id, attempt_id, scoring_scheme_id, total_score) "
                        "VALUES (:gid, :tid, :att, :sid, :score)"
                    ),
                    {"gid": grade_id, "tid": ta, "att": att_id, "sid": scheme_id, "score": score},
                )
                for qvid, is_ok in zip(qv_ids, correctness, strict=True):
                    chosen = "A" if is_ok else wrong_opt
                    await session.execute(
                        text(
                            "INSERT INTO responses (tenant_id, attempt_id, question_version_id, response_payload) "
                            "VALUES (:tid, :att, :qvid, CAST(:payload AS jsonb))"
                        ),
                        {
                            "tid": ta,
                            "att": att_id,
                            "qvid": qvid,
                            "payload": json.dumps({"selected_option": chosen}),
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO grading_details "
                            "(tenant_id, grade_id, question_version_id, earned_points, max_points, is_correct) "
                            "VALUES (:tid, :gid, :qvid, :ep, 1.00, :ok)"
                        ),
                        {"tid": ta, "gid": grade_id, "qvid": qvid, "ep": 1.00 if is_ok else 0.00, "ok": is_ok},
                    )

            return class_id, assignment_id, qv_ids

    class_id, assignment_id, qv_ids = run(seed())
    client = _api_client()
    t_headers = _auth_headers(tenant_id=ta, membership_id=m_teacher, user_id=u_teacher, roles=["TEACHER"])
    tb_headers = _auth_headers(tenant_id=tb, membership_id=m_b, user_id=u_b, roles=["TEACHER"])

    gb_res = client.get(
        f"/api/v1/classes/{class_id}/gradebook",
        params={"assignment_id": str(assignment_id)},
        headers=t_headers,
    )
    assert gb_res.status_code == 200, gb_res.text
    gb_body = gb_res.json()
    stats = gb_body["stats_json"]
    assert stats["total_submissions"] == 3
    assert stats["on_time_submissions"] == 2
    assert stats["mean_score"] == "7.1667"
    assert stats["score_histogram_2pt_bins"] == {
        "[0, 2)": 0,
        "[2, 4)": 0,
        "[4, 6)": 1,
        "[6, 8)": 1,
        "[8, 10]": 1,
    }
    rows_by_mid = {r["membership_id"]: r for r in gb_body["rows"]}
    assert rows_by_mid[str(m_s2)]["is_historical_inactive_member"] is True
    assert rows_by_mid[str(m_s2)]["score"] == "7.50"

    ia_res = client.get(
        f"/api/v1/classes/{class_id}/item-analysis",
        params={"assignment_id": str(assignment_id)},
        headers=t_headers,
    )
    assert ia_res.status_code == 200
    hardest = ia_res.json()["top_5_hardest_questions"]
    assert len(hardest) == 5
    # Question 3 (index 2) had 3/3 wrong with dominant wrong distractor 'C'
    assert hardest[0]["wrong_count"] == 3
    assert hardest[0]["top_wrong_distractor"] == "C"
    assert hardest[0]["top_wrong_distractor_count"] == 2

    exp_res = client.get(
        f"/api/v1/classes/{class_id}/gradebook/export",
        params={"assignment_id": str(assignment_id)},
        headers=t_headers,
    )
    assert exp_res.status_code == 200
    assert exp_res.headers["content-type"].startswith("text/csv")
    csv_text = exp_res.content.decode("utf-8")
    parsed_rows = list(csv.reader(io.StringIO(csv_text)))
    assert parsed_rows[0] == ["membership_id", "student_label", "score", "submitted_at", "historical_inactive"]
    by_mid_csv = {r[0]: r for r in parsed_rows[1:]}
    assert by_mid_csv[str(m_s1)][1] == "Nguyễn Minh An"
    assert by_mid_csv[str(m_s2)][1].startswith("'=HYPERLINK(")
    assert by_mid_csv[str(m_s2)][4] == "true"
    assert by_mid_csv[str(m_s3)][1] == "'+1+1"
    assert by_mid_csv[str(m_s4)][2] == ""

    # ulp_worker_grading computes/upserts gradebook_projections cleanly
    async def check_worker_role() -> None:
        async with tenant_transaction(ta, role=DbRole.WORKER_GRADING) as session:
            proj = await recompute_gradebook_projection(
                session,
                tenant_id=ta,
                class_id=class_id,
                assignment_id=assignment_id,
                grades=(
                    StudentAttemptGrade(str(m_s1), "Nguyễn Minh An", Decimal("9.50"), due_at, due_at),
                ),
                item_observations=(
                    ItemResponseObservation(str(qv_ids[0]), False, "D"),
                ),
            )
            assert proj.stats_json["total_submissions"] == 1
            assert proj.top_wrong_questions_json[0]["top_wrong_distractor"] == "D"

    run(check_worker_role())

    # Tenant B isolation
    assert (
        client.get(
            f"/api/v1/classes/{class_id}/gradebook",
            params={"assignment_id": str(assignment_id)},
            headers=tb_headers,
        ).status_code
        == 404
    )


def test_f06_notifications_decision7_dedup_replay_read_and_preferences(
    world: dict[str, uuid.UUID],
) -> None:
    """F06 verifies Decision #7 `tenant_id` on deliveries, 3x dedup replay idempotency, inbox read & prefs."""
    ta, tb = world["ta"], world["tb"]
    u_s1, m_s1, u_b, m_b = world["u_s1"], world["m_s1"], world["u_b"], world["m_b"]

    async def check_decision7_schema_and_replay() -> uuid.UUID:
        attempt_id = uuid.uuid4()
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session:
            cols = (
                await session.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'notification_deliveries'"
                    )
                )
            ).mappings().all()
            col_map = {r["column_name"]: r["is_nullable"] for r in cols}
            assert col_map["tenant_id"] == "NO"

            rls = (
                await session.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'notification_deliveries'"
                    )
                )
            ).mappings().one()
            assert rls["relrowsecurity"] is True and rls["relforcerowsecurity"] is True

            first = await handle_attempt_graded_notification(
                session,
                tenant_id=ta,
                membership_id=m_s1,
                attempt_id=attempt_id,
                revision_no=1,
                total_score=Decimal("8.75"),
            )
            assert first.created is True and first.notification is not None
            assert len(first.deliveries) == 2
            assert all(d.tenant_id == ta for d in first.deliveries)

            # Replay same event twice more: must produce 0 new notifications and 0 new deliveries
            replay2 = await handle_attempt_graded_notification(
                session, tenant_id=ta, membership_id=m_s1, attempt_id=attempt_id, revision_no=1
            )
            replay3 = await handle_attempt_graded_notification(
                session, tenant_id=ta, membership_id=m_s1, attempt_id=attempt_id, revision_no=1
            )
            assert replay2.created is False and replay2.deliveries == ()
            assert replay3.created is False and replay3.deliveries == ()

            notif_count = int((await session.execute(text("SELECT COUNT(*) FROM notifications"))).scalar() or 0)
            deliv_count = int(
                (await session.execute(text("SELECT COUNT(*) FROM notification_deliveries"))).scalar() or 0
            )
            assert notif_count == 1
            assert deliv_count == 2
            return first.notification.id

    notif_id = run(check_decision7_schema_and_replay())

    client = _api_client()
    s1_headers = _auth_headers(tenant_id=ta, membership_id=m_s1, user_id=u_s1, roles=["LEARNER"])
    tb_headers = _auth_headers(tenant_id=tb, membership_id=m_b, user_id=u_b, roles=["TEACHER"])

    inbox_res = client.get("/api/v1/inbox", headers=s1_headers)
    assert inbox_res.status_code == 200, inbox_res.text
    assert inbox_res.json()["unread_count"] == 1
    assert len(inbox_res.json()["items"]) == 1
    assert len(inbox_res.json()["items"][0]["deliveries"]) == 2

    read_res = client.post(f"/api/v1/inbox/{notif_id}/read", headers=s1_headers)
    assert read_res.status_code == 200
    assert read_res.json()["is_read"] is True

    unread_after = client.get("/api/v1/inbox", params={"unread_only": True}, headers=s1_headers)
    assert unread_after.status_code == 200
    assert unread_after.json()["unread_count"] == 0
    assert unread_after.json()["items"] == []

    # Preferences update & filtering
    patch_res = client.patch(
        "/api/v1/notifications/preferences",
        json={"enabled_channels": ["IN_APP"], "muted_categories": ["ATTEMPT_GRADED"]},
        headers=s1_headers,
    )
    assert patch_res.status_code == 200
    prefs_get = client.get("/api/v1/notifications/preferences", headers=s1_headers)
    assert prefs_get.status_code == 200
    assert prefs_get.json()["enabled_channels"] == ["IN_APP"]
    assert prefs_get.json()["muted_categories"] == ["ATTEMPT_GRADED"]

    async def check_preferences_and_cross_tenant_rls() -> None:
        async with tenant_transaction(ta, role=DbRole.APP_RW) as session:
            muted_res = await dispatch_notification(
                session,
                tenant_id=ta,
                command=NotificationCommand(
                    tenant_id=str(ta),
                    membership_id=str(m_s1),
                    category=NotificationCategory.ATTEMPT_GRADED,
                    dedup_key=f"attempt.graded:{uuid.uuid4()}:rev-1",
                    title="Muted notice",
                    body_text="Should be filtered out",
                ),
            )
            assert muted_res.created is False

            in_app_only = await dispatch_notification(
                session,
                tenant_id=ta,
                command=NotificationCommand(
                    tenant_id=str(ta),
                    membership_id=str(m_s1),
                    category=NotificationCategory.ASSIGNMENT_CREATED,
                    dedup_key=f"asg.created:{uuid.uuid4()}",
                    title="Bài tập mới",
                    body_text="Giáo viên vừa giao bài tập",
                ),
            )
            assert in_app_only.created is True
            assert [d.channel for d in in_app_only.deliveries] == [NotificationChannel.IN_APP.value]

        # Tenant B sees 0 notifications and 0 deliveries under direct RLS
        async with tenant_transaction(tb, role=DbRole.APP_RW) as session:
            assert int((await session.execute(text("SELECT COUNT(*) FROM notifications"))).scalar() or 0) == 0
            assert int((await session.execute(text("SELECT COUNT(*) FROM notification_deliveries"))).scalar() or 0) == 0

    run(check_preferences_and_cross_tenant_rls())
    assert client.post(f"/api/v1/inbox/{notif_id}/read", headers=tb_headers).status_code == 404
    assert client.get("/api/v1/inbox", headers=tb_headers).json()["items"] == []
