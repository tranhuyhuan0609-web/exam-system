"""Materialize the sessions of `seed_attempts.py` as real rows, so a GATE-05 run hits an API that knows its VUs.

`build_session_records` makes synthetic ids: every call of a k6 run on them is a 404. `materialize_sessions` makes them
true in the target database (the one `DATABASE_URL` points at, with a login that may create tenants and users: the
migrator or the owner, never the API's `ulp_app_rw`):

* one load-test tenant per `tenant_count` (slug `<prefix>-NN`), each with a teacher, its share of the learners (users
  and ACTIVE memberships), a class holding them, and a published C04 paper drawn from a MATH question pool;
* an assignment of the paper to the class, and for every learner an IN_PROGRESS attempt on it, started through the
  same `start_exam_session` the API calls (so the paper snapshot, the seed and the deadline are the real ones);
* the VU records of the k6 contract, with the real tenant, membership and attempt ids, a token signed for the real
  user, and the question versions of the paper the attempt belongs to.

Everything is idempotent and scoped to the `<prefix>-NN` tenants: a second run adds nothing and returns the same
attempts (an attempt in progress is resumed, not duplicated), and no other tenant is read or written.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal
from app.modules.assembly.public import assemble_assessment, create_assessment, publish_assessment
from app.modules.assignments.public import create_assignment, list_assignments
from app.modules.classes.public import create_class
from app.modules.delivery.public import start_exam_session
from app.ops.seed_demo import _ensure_questions, _ensure_taxonomy
from app.platform.tenancy import DbRole, tenant_transaction
from tests.load.seed_attempts import _NAMESPACE, sign_loadtest_jwt

DEFAULT_TENANT_PREFIX = "ulp-load"
PAPER_TITLE = "Đề tải GATE-05 (Toán THPT 2025)"
CLASS_NAME = "Lớp tải GATE-05"
FRAMEWORK_CODE = "THPTQG_2025"
ASSEMBLY_SEED = 20261113
DEFAULT_CHUNK = 100
SLUG_PATTERN_HINT = "lowercase letters, digits and dashes"


def _uid(seed: int, kind: str, key: object) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{seed}:{kind}:{key}")


def _blind_index(seed: int, kind: str, key: object) -> str:
    return hashlib.sha256(f"load:{seed}:{kind}:{key}".encode()).hexdigest()


async def _ensure_tenant(slug: str) -> uuid.UUID:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenants (slug, name) VALUES (:s, :n) ON CONFLICT (slug) DO NOTHING"),
            {"s": slug, "n": f"Load test {slug}"},
        )
        return (await session.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": slug})).scalar_one()


async def _ensure_people(
    tenant_id: uuid.UUID, seed: int, tenant_index: int, learner_indexes: list[int]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Users and ACTIVE memberships for a teacher and the learners; returns the teacher's (user, membership)."""
    teacher_user = _uid(seed, "teacher-user", tenant_index)
    teacher_membership = _uid(seed, "teacher-membership", tenant_index)
    people: list[dict[str, Any]] = [
        {
            "u": teacher_user,
            "m": teacher_membership,
            "b": _blind_index(seed, "teacher", tenant_index),
            "r": ["TEACHER"],
            "tier": "TEACHER_PRO",
        }
    ] + [
        {
            "u": _uid(seed, "user", idx),
            "m": _uid(seed, "membership", idx),
            "b": _blind_index(seed, "learner", idx),
            "r": ["LEARNER"],
            "tier": "FREE",
        }
        for idx in learner_indexes
    ]
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("INSERT INTO users (id, email_blind_index) VALUES (:u, :b) ON CONFLICT (id) DO NOTHING"),
            [{"u": p["u"], "b": p["b"]} for p in people],
        )
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, status, roles, entitlement_tier) "
                "VALUES (:m, :t, :u, 'ACTIVE', CAST(:r AS varchar[]), :tier) ON CONFLICT (id) DO NOTHING"
            ),
            [{"m": p["m"], "t": tenant_id, "u": p["u"], "r": p["r"], "tier": p["tier"]} for p in people],
        )
    return teacher_user, teacher_membership


async def _ensure_paper(tenant_id: uuid.UUID, teacher_user: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """The tenant's published load paper: (assessment_id, current version id)."""
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, current_version_id FROM assessments "
                    "WHERE tenant_id = :t AND title = :title AND publication_status = 'PUBLISHED'"
                ),
                {"t": tenant_id, "title": PAPER_TITLE},
            )
        ).one_or_none()
        if row is not None and row.current_version_id is not None:
            return row.id, row.current_version_id
        paper = await create_assessment(
            session,
            tenant_id=tenant_id,
            title=PAPER_TITLE,
            subject_code="math",
            grade_band="GRADE_12",
            exam_type_code="THPTQG",
            rights_status="OWNED",
        )
        await assemble_assessment(session, paper.id, framework_code=FRAMEWORK_CODE, random_seed=ASSEMBLY_SEED)
        published = await publish_assessment(session, paper.id, published_by=teacher_user)
        return paper.id, published.version.id


async def _ensure_class_and_assignment(
    tenant_id: uuid.UUID, teacher_membership: uuid.UUID, teacher_user: uuid.UUID, version_id: uuid.UUID,
    learner_memberships: list[uuid.UUID],
) -> uuid.UUID:  # fmt: skip
    """A class of the learners and the paper assigned to it; returns the assignment id."""
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        class_id = (
            await session.execute(
                text("SELECT id FROM classes WHERE tenant_id = :t AND name = :n"), {"t": tenant_id, "n": CLASS_NAME}
            )
        ).scalar_one_or_none()
        if class_id is None:
            class_id = (
                await create_class(
                    session, tenant_id=tenant_id, teacher_membership_id=teacher_membership, name=CLASS_NAME
                )
            ).id
        await session.execute(
            text(
                "INSERT INTO class_members (tenant_id, class_id, membership_id, role_in_class, status) "
                "VALUES (:t, :c, :m, 'STUDENT', 'ACTIVE') ON CONFLICT DO NOTHING"
            ),
            [{"t": tenant_id, "c": class_id, "m": m} for m in learner_memberships],
        )
        existing = [a for a in await list_assignments(session, tenant_id=tenant_id, class_id=class_id)]
        for assignment in existing:
            if assignment.assessment_version_id == version_id:
                return assignment.id
        created = await create_assignment(
            session,
            tenant_id=tenant_id,
            assessment_version_id=version_id,
            assigned_by=teacher_user,
            class_id=class_id,
            max_attempts=3,
        )
        return created.id


async def _start_attempts(
    tenant_id: uuid.UUID, assessment_id: uuid.UUID, assignment_id: uuid.UUID, memberships: list[uuid.UUID], chunk: int
) -> dict[uuid.UUID, uuid.UUID]:
    """An IN_PROGRESS attempt per learner (a sitting already open is resumed): membership id -> attempt id."""
    attempts: dict[uuid.UUID, uuid.UUID] = {}
    for start in range(0, len(memberships), chunk):
        async with tenant_transaction(tenant_id, role=DbRole.EXAM_SESSION_RW) as session:
            for membership_id in memberships[start : start + chunk]:
                result = await start_exam_session(
                    session,
                    tenant_id=tenant_id,
                    membership_id=membership_id,
                    assessment_id=assessment_id,
                    assignment_id=assignment_id,
                )
                attempts[membership_id] = result.attempt.id
    return attempts


async def _paper_question_versions(tenant_id: uuid.UUID, version_id: uuid.UUID) -> list[str]:
    async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
        rows = await session.execute(
            text(
                "SELECT question_version_id FROM assessment_items WHERE assessment_version_id = :v "
                "ORDER BY sequence_order"
            ),
            {"v": version_id},
        )
        return [str(r.question_version_id) for r in rows]


async def materialize_sessions(
    *,
    count: int,
    tenant_count: int,
    seed: int,
    signing_secret: str,
    tenant_prefix: str = DEFAULT_TENANT_PREFIX,
    chunk: int = DEFAULT_CHUNK,
    issued_at: int | None = None,
    log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Create (or find) the tenants, people, papers, classes and attempts, and return the VU records of k6."""
    say = log or (lambda _msg: None)
    now = int(time.time()) if issued_at is None else issued_at
    taxonomy_id = await _ensure_taxonomy()
    records: dict[int, dict[str, Any]] = {}

    for t in range(tenant_count):
        slug = f"{tenant_prefix}-{t:02d}"
        learner_indexes = [idx for idx in range(count) if idx % tenant_count == t]
        if not learner_indexes:
            continue
        tenant_id = await _ensure_tenant(slug)
        await _ensure_questions(tenant_id, taxonomy_id)
        teacher_user, teacher_membership = await _ensure_people(tenant_id, seed, t, learner_indexes)
        assessment_id, version_id = await _ensure_paper(tenant_id, teacher_user)
        memberships = [_uid(seed, "membership", idx) for idx in learner_indexes]
        assignment_id = await _ensure_class_and_assignment(
            tenant_id, teacher_membership, teacher_user, version_id, memberships
        )
        attempts = await _start_attempts(tenant_id, assessment_id, assignment_id, memberships, chunk)
        questions = await _paper_question_versions(tenant_id, version_id)
        for idx in learner_indexes:
            membership_id = _uid(seed, "membership", idx)
            user_id = _uid(seed, "user", idx)
            records[idx] = {
                "vu_index": idx + 1,
                "attempt_id": str(attempts[membership_id]),
                "tenant_id": str(tenant_id),
                "membership_id": str(membership_id),
                "jwt": sign_loadtest_jwt(
                    sub=str(user_id),
                    tenant_id=str(tenant_id),
                    membership_id=str(membership_id),
                    signing_secret=signing_secret,
                    issued_at=now,
                ),
                "question_version_ids": questions,
                "assignment_id": str(assignment_id),
                "user_id": str(user_id),
            }
        say(f"{slug}: {len(learner_indexes)} learners, {len(questions)} questions, {len(attempts)} attempts")
    return [records[idx] for idx in sorted(records)]
