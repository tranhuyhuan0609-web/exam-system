"""Demo data for a local run: ``python -m app.ops.seed_demo``.

Idempotent: every step looks before it writes, a second run changes nothing. It builds, in the migrated database,

* the tenant ``h2edu-demo`` and four accounts, one per role, all with the same demo password (below);
* the class ``class-12a1`` (teacher + learner), the lesson ``cong-thuc-bayes`` (published),
* 22 published, verified Bayes questions (12 single choice, 4 true/false clusters, 6 short numeric) and the paper
  "THPTQG 2025 demo" assembled from them and published, through the real API so every rule applies;
* four C01 sample questions (drafts, through `POST /api/v1/questions`) and one PENDING_REVIEW exam import (a PDF
  uploaded, extracted and structured through the API) for the question-bank and BBox-review screens;
* the assignment of that paper to the class (teacher, through the API), four error notes of the learner (one per
  stage of the two-correct-in-a-row rule) and the learner's learning plan (learner, through the API), so the web
  screens list real UUIDs from the database instead of sample ids;
* the platform catalogues: ``ai_providers`` / ``ai_models``, ``subscription_plans``, the vertical pack ``K12_VN_KNTT``
  with its ``locale_bundles``.

Local use only: it refuses to run when ``APP_ENV`` is not dev/test (the password is public), and the demo password
comes from ``SEED_DEMO_PASSWORD`` (default below). Run it as the database owner, the role migrations use.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.platform.auth_context import mint_hs256_token
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID
from app.platform.tenancy import tenant_transaction
from app.security import hash_password

TENANT_SLUG = "h2edu-demo"
TENANT_NAME = "H2EDU Demo School"
DEFAULT_PASSWORD = "Demo@12345"  # public, for localhost only
CLASS_NAME = "class-12a1"
LESSON_SLUG = "cong-thuc-bayes"
PAPER_TITLE = "Đề thi thử Toán THPT 2025 (demo)"
QUESTION_CODE_PREFIX = "DEMO-BAYES"
ASSEMBLY_SEED = 2025
# (status, wrong_count, consecutive_correct_count): untouched, one correct so far, relapsed, closed.
ERROR_NOTE_STAGES: tuple[tuple[str, int, int], ...] = (
    ("OPEN", 1, 0),
    ("IN_REMEDIATION", 2, 1),
    ("OPEN", 3, 0),
    ("RESOLVED", 2, 2),
)

ACCOUNTS: tuple[tuple[str, str, str, list[str], str], ...] = (
    # key, email, pseudonym, roles, entitlement tier
    ("platform_admin", "platform_admin@h2edu.demo", "Platform Admin", ["PLATFORM_ADMIN"], "SCHOOL_ENTERPRISE"),
    ("tenant_admin", "tenant_admin@h2edu.demo", "Tenant Admin", ["TENANT_ADMIN"], "SCHOOL_ENTERPRISE"),
    ("teacher", "teacher@h2edu.demo", "Cô Lan", ["TEACHER"], "TEACHER_PRO"),
    # the question bank and the imports are run by an author: a teacher only builds papers from the bank
    ("content_author", "content_author@h2edu.demo", "Biên tập viên", ["CONTENT_AUTHOR"], "TEACHER_PRO"),
    ("learner", "learner@h2edu.demo", "Minh", ["LEARNER"], "STUDENT_PRO"),
)

PLANS = (
    ("FREE", "Free", 0, {"BRANDED_PDF_EXPORT": 2, "AI_TUTOR_MESSAGE": 20}),
    ("STUDENT_PRO", "Student Pro", 199_000, {"BRANDED_PDF_EXPORT": 50, "AI_TUTOR_MESSAGE": 500}),
    ("TEACHER_PRO", "Teacher Pro", 499_000, {"BRANDED_PDF_EXPORT": 200, "AI_TUTOR_MESSAGE": 1000}),
)
AI_PROVIDERS = (("GEMINI", "https://generativelanguage.googleapis.com"),)
AI_MODELS = (
    ("GEMINI", "gemini-2.5-flash", "TUTOR", "0.3000", "2.5000"),
    ("GEMINI", "gemini-2.5-flash", "EXTRACTION", "0.3000", "2.5000"),
    ("GEMINI", "gemini-2.5-pro", "VERIFICATION", "1.2500", "10.0000"),
)
PACK = {
    "code": "K12_VN_KNTT",
    "name": "K12 Việt Nam - Kết nối tri thức",
    "manifest": {
        "taxonomy_root_prefix": "K12",
        "assessment_framework_code": "THPTQG_2025",
        "scoring_policy_code": "THPTQG_2025_DEFAULT",
        "default_locale": "vi-VN",
        "is_core_k12": True,
    },
}
LOCALE_BUNDLES = (
    ("vi-VN", "common", {"app.title": "H2EDU", "exam.start": "Bắt đầu làm bài", "exam.submit": "Nộp bài"}),
    ("en-US", "common", {"app.title": "H2EDU", "exam.start": "Start the exam", "exam.submit": "Submit"}),
)


def email_blind_index(email: str, secret_key: str, configured_pepper: str = "") -> str:
    """The same keyed digest as `app.modules.privacy` (ops may not import modules; a test pins the two together)."""
    source = configured_pepper or secret_key
    domain = b"f04:email-blind-index:v2" if configured_pepper else b"f04:email-blind-index:v1"
    pepper = hmac.new(source.encode(), domain, hashlib.sha256).digest()
    normalized = "".join(unicodedata.normalize("NFKC", email).split()).lower()
    return hmac.new(pepper, normalized.encode(), hashlib.sha256).hexdigest()


def bayes_questions() -> list[dict[str, Any]]:
    """22 deterministic Bayes questions: 12 SINGLE_CHOICE, 4 CLUSTER_TRUE_FALSE, 6 SHORT_NUMERIC."""
    out: list[dict[str, Any]] = []
    for i in range(12):
        pa, pba, pbn = 0.2 + 0.05 * (i % 6), 0.7 + 0.02 * (i % 5), 0.1 + 0.03 * (i % 4)
        posterior = pba * pa / (pba * pa + pbn * (1 - pa))
        wrong = [posterior * 0.5, pba * pa, 1 - posterior]
        letters = "ABCD"
        correct = letters[i % 4]
        options = {}
        wrong_iter = iter(wrong)
        for letter in letters:
            options[letter] = posterior if letter == correct else next(wrong_iter)
        out.append(
            {
                "type": "SINGLE_CHOICE",
                "stem": (
                    f"Cho P(A) = {pa:.2f}, P(B|A) = {pba:.2f}, P(B|Ā) = {pbn:.2f}. "
                    "Theo công thức Bayes, P(A|B) bằng bao nhiêu (làm tròn 3 chữ số)?"
                ),
                "options": {k: f"{v:.3f}".replace(".", ",") for k, v in options.items()},
                "answer": {"correct_option": correct},
            }
        )
    for i in range(4):
        out.append(
            {
                "type": "CLUSTER_TRUE_FALSE",
                "stem": f"Hai biến cố A, B có P(A) = 0,{3 + i}. Xét các mệnh đề sau về công thức Bayes.",
                "options": {
                    "a": "P(A|B)·P(B) = P(B|A)·P(A)",
                    "b": "P(A|B) luôn bằng P(B|A)",
                    "c": "Nếu A, B độc lập thì P(A|B) = P(A)",
                    "d": "P(A|B) có thể lớn hơn 1",
                },
                "answer": {"statements": {"a": True, "b": False, "c": True, "d": False}},
            }
        )
    for i in range(6):
        pa, pba, pbn = 0.5, 0.8, 0.2 + 0.05 * i
        posterior = pba * pa / (pba * pa + pbn * (1 - pa))
        out.append(
            {
                "type": "SHORT_NUMERIC",
                "stem": (
                    f"Một bệnh có tỉ lệ mắc P(A) = {pa:.1f}. Xét nghiệm dương tính với người bệnh P(B|A) = {pba:.1f}, "
                    f"với người khỏe P(B|Ā) = {pbn:.2f}. Tính P(A|B), làm tròn 2 chữ số (dùng dấu phẩy)."
                ),
                "options": {},
                "answer": {"value": f"{posterior:.2f}".replace(".", ",")},
            }
        )
    return out


def _bearer(tenant_id: uuid.UUID, user_id: uuid.UUID, membership_id: uuid.UUID, roles: list[str], tier: str) -> dict:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        roles=roles,
        entitlement_tier=tier,
        session_id="seed-demo",
        issued_at=int(time.time()),
        ttl_seconds=1800,
    )
    return {"Authorization": f"Bearer {token}"}


def _rows(result: Any) -> int:
    """Rows an INSERT really wrote (0 when ON CONFLICT or NOT EXISTS skipped it)."""
    return int(result.rowcount or 0)


async def _ensure_catalogues() -> dict[str, int]:
    """Global catalogues (no tenant column): plans, AI providers and models, the K12 pack and its bundles."""
    added = {"subscription_plans": 0, "ai_providers": 0, "ai_models": 0, "vertical_packs": 0, "locale_bundles": 0}
    async with SessionLocal() as session, session.begin():
        for code, name, price, quotas in PLANS:
            result = await session.execute(
                text(
                    "INSERT INTO subscription_plans (code, name, price_vnd, quotas_json) "
                    "VALUES (:c, :n, :p, CAST(:q AS jsonb)) ON CONFLICT (code) DO NOTHING"
                ),
                {"c": code, "n": name, "p": price, "q": json.dumps(quotas)},
            )
            added["subscription_plans"] += _rows(result)
        for code, base_url in AI_PROVIDERS:
            result = await session.execute(
                text("INSERT INTO ai_providers (code, base_url) VALUES (:c, :u) ON CONFLICT (code) DO NOTHING"),
                {"c": code, "u": base_url},
            )
            added["ai_providers"] += _rows(result)
        for provider, model, role, cost_in, cost_out in AI_MODELS:
            result = await session.execute(
                text(
                    "INSERT INTO ai_models (provider_code, model_name, task_role, input_cost_per_1m, "
                    "output_cost_per_1m) SELECT CAST(:p AS varchar), CAST(:m AS varchar), CAST(:r AS varchar), "
                    "CAST(:ci AS numeric), CAST(:co AS numeric) WHERE NOT EXISTS "
                    "(SELECT 1 FROM ai_models WHERE provider_code = :p AND model_name = :m AND task_role = :r)"
                ),
                {"p": provider, "m": model, "r": role, "ci": cost_in, "co": cost_out},
            )
            added["ai_models"] += _rows(result)
        result = await session.execute(
            text(
                "INSERT INTO vertical_packs (code, name, manifest_json, is_enabled) "
                "VALUES (:c, :n, CAST(:m AS jsonb), TRUE) ON CONFLICT (code) DO NOTHING"
            ),
            {"c": PACK["code"], "n": PACK["name"], "m": json.dumps(PACK["manifest"])},
        )
        added["vertical_packs"] += _rows(result)
        for locale, namespace, messages in LOCALE_BUNDLES:
            result = await session.execute(
                text(
                    "INSERT INTO locale_bundles (locale_code, namespace, messages_json) "
                    "VALUES (:l, :n, CAST(:m AS jsonb)) ON CONFLICT (locale_code, namespace) DO NOTHING"
                ),
                {"l": locale, "n": namespace, "m": json.dumps(messages)},
            )
            added["locale_bundles"] += _rows(result)
    return added


async def _ensure_tenant() -> uuid.UUID:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            text("INSERT INTO tenants (slug, name) VALUES (:s, :n) ON CONFLICT (slug) DO NOTHING"),
            {"s": TENANT_SLUG, "n": TENANT_NAME},
        )
        return (await session.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": TENANT_SLUG})).scalar_one()


async def _ensure_accounts(tenant_id: uuid.UUID, password: str) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    accounts: dict[str, dict[str, Any]] = {}
    password_hash = hash_password(password)
    async with tenant_transaction(tenant_id) as session:
        for key, email, pseudonym, roles, tier in ACCOUNTS:
            blind = email_blind_index(email, settings.secret_key, settings.blind_index_pepper)
            row = (
                await session.execute(
                    text("SELECT user_id, created FROM f04_register_user(:b, :h, NULL, :p, NULL, 'ACTIVE')"),
                    {"b": blind, "h": password_hash, "p": pseudonym},
                )
            ).one()
            await session.execute(  # a user registered earlier without a password gets the demo one, never overwritten
                text("UPDATE users SET password_hash = :h WHERE id = :u AND password_hash IS NULL"),
                {"h": password_hash, "u": row.user_id},
            )
            await session.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, roles, entitlement_tier) "
                    "VALUES (:t, :u, CAST(:r AS varchar[]), :tier) ON CONFLICT (tenant_id, user_id) DO NOTHING"
                ),
                {"t": tenant_id, "u": row.user_id, "r": roles, "tier": tier},
            )
            membership = (
                await session.execute(
                    text("SELECT id FROM memberships WHERE tenant_id = :t AND user_id = :u"),
                    {"t": tenant_id, "u": row.user_id},
                )
            ).scalar_one()
            accounts[key] = {
                "email": email,
                "user_id": row.user_id,
                "membership_id": membership,
                "roles": roles,
                "tier": tier,
            }
    return accounts


async def _ensure_taxonomy() -> uuid.UUID:
    """MATH > BAYES under the system tenant (the shared taxonomy every tenant builds on)."""
    async with tenant_transaction(SYSTEM_PUBLIC_TENANT_ID) as session:

        async def node(code: str, node_type: str, path: str, parent: uuid.UUID | None) -> uuid.UUID:
            await session.execute(
                text(
                    "INSERT INTO taxonomy_nodes (tenant_id, node_type, code, code_path, parent_id) "
                    "SELECT CAST(:t AS uuid), CAST(:nt AS varchar), CAST(:c AS varchar), "
                    "CAST(:p AS ltree), CAST(:par AS uuid) "
                    "WHERE NOT EXISTS "
                    "(SELECT 1 FROM taxonomy_nodes WHERE tenant_id = :t AND code_path = CAST(:p AS ltree))"
                ),
                {"t": SYSTEM_PUBLIC_TENANT_ID, "nt": node_type, "c": code, "p": path, "par": parent},
            )
            return (
                await session.execute(
                    text("SELECT id FROM taxonomy_nodes WHERE tenant_id = :t AND code_path = CAST(:p AS ltree)"),
                    {"t": SYSTEM_PUBLIC_TENANT_ID, "p": path},
                )
            ).scalar_one()

        math = await node("MATH", "SUBJECT", "MATH", None)
        return await node("BAYES", "TOPIC", "MATH.BAYES", math)


async def _ensure_questions(tenant_id: uuid.UUID, taxonomy_id: uuid.UUID) -> int:
    created = 0
    async with tenant_transaction(tenant_id) as session:
        for n, q in enumerate(bayes_questions(), start=1):
            code = f"{QUESTION_CODE_PREFIX}-{n:02d}"
            if (
                await session.execute(
                    text("SELECT 1 FROM questions WHERE tenant_id = :t AND stable_code = :c"),
                    {"t": tenant_id, "c": code},
                )
            ).first():
                continue
            qid, vid = uuid.uuid4(), uuid.uuid4()
            digest = hashlib.sha256(code.encode()).hexdigest()
            options_json = [{"key": k, "text": v} for k, v in q["options"].items()]
            await session.execute(
                text(
                    "INSERT INTO questions (id, tenant_id, visibility_scope, stable_code, item_type, "
                    "primary_taxonomy_node_id, verification_status, rights_status) "
                    "VALUES (:q, :t, 'TENANT_PRIVATE', :c, :it, :tx, 'VERIFIED', 'OWNED')"
                ),
                {"q": qid, "t": tenant_id, "c": code, "it": q["type"], "tx": taxonomy_id},
            )
            await session.execute(
                text(
                    "INSERT INTO question_versions (id, tenant_id, visibility_scope, question_id, version_no, "
                    "stem_json, options_json, content_hash, structural_hash) "
                    "VALUES (:v, :t, 'TENANT_PRIVATE', :q, 1, CAST(:stem AS jsonb), CAST(:opts AS jsonb), :h, :h)"
                ),
                {
                    "v": vid,
                    "t": tenant_id,
                    "q": qid,
                    "stem": json.dumps({"text": q["stem"]}),
                    "opts": json.dumps(options_json),
                    "h": digest,
                },
            )
            for order, (key, value) in enumerate(q["options"].items(), start=1):
                await session.execute(
                    text(
                        "INSERT INTO question_options (tenant_id, question_version_id, option_key, content_json, "
                        "sequence_order) VALUES (:t, :v, :k, CAST(:c AS jsonb), :o)"
                    ),
                    {"t": tenant_id, "v": vid, "k": key, "o": order, "c": json.dumps({"text": value})},
                )
            await session.execute(
                text(
                    "INSERT INTO question_version_answers (question_version_id, tenant_id, answer_schema) "
                    "VALUES (:v, :t, CAST(:a AS jsonb))"
                ),
                {"v": vid, "t": tenant_id, "a": json.dumps(q["answer"])},
            )
            await session.execute(
                text("UPDATE questions SET publication_status = 'PUBLISHED', current_version_id = :v WHERE id = :q"),
                {"v": vid, "q": qid},
            )
            created += 1
    return created


async def _ensure_class(tenant_id: uuid.UUID, teacher: uuid.UUID, learner: uuid.UUID) -> bool:
    async with tenant_transaction(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT id FROM classes WHERE tenant_id = :t AND invite_code = :c"),
                {"t": tenant_id, "c": CLASS_NAME},
            )
        ).first()
        made = row is None
        if row is None:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO classes (tenant_id, teacher_membership_id, name, academic_year, invite_code) "
                        "VALUES (:t, :m, 'Lớp 12A1', '2025-2026', :c) RETURNING id"
                    ),
                    {"t": tenant_id, "m": teacher, "c": CLASS_NAME},
                )
            ).one()
        await session.execute(
            text(
                "INSERT INTO class_members (tenant_id, class_id, membership_id) VALUES (:t, :c, :m) "
                "ON CONFLICT (class_id, membership_id) DO NOTHING"
            ),
            {"t": tenant_id, "c": row.id, "m": learner},
        )
        return made


async def _already_there(tenant_id: uuid.UUID) -> dict[str, bool]:
    async with tenant_transaction(tenant_id) as session:
        lesson = (
            await session.execute(
                text("SELECT 1 FROM content_items WHERE tenant_id = :t AND slug = :s"),
                {"t": tenant_id, "s": LESSON_SLUG},
            )
        ).first()
        paper = (
            await session.execute(
                text("SELECT 1 FROM assessments WHERE tenant_id = :t AND title = :n"),
                {"t": tenant_id, "n": PAPER_TITLE},
            )
        ).first()
    return {"lesson": lesson is not None, "paper": paper is not None}


async def _verify_content(tenant_id: uuid.UUID, item_id: uuid.UUID) -> None:
    async with tenant_transaction(tenant_id) as session:
        await session.execute(
            text("UPDATE content_items SET verification_status = 'VERIFIED' WHERE id = :i"), {"i": item_id}
        )


def _ok(response: Any) -> Any:
    """The response, or an error that carries the API's own explanation."""
    if response.status_code >= 400:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text}"
        )
    return response


async def _through_api[T](work: Callable[[Any], T]) -> T:
    """Run `work(client)` against the real app (TestClient, so authoring and publishing rules apply).

    The client lives in a worker thread with its own event loop, and a pooled asyncpg connection cannot cross
    loops; so the pool is emptied before the thread starts, by the thread before it ends, and again after it.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    def runner() -> T:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            try:
                return work(client)
            finally:
                assert client.portal is not None  # set while inside the `with` block
                client.portal.call(engine.dispose)

    await engine.dispose()
    try:
        return await asyncio.to_thread(runner)
    finally:
        await engine.dispose()


def _headers(tenant_id: uuid.UUID, account: dict[str, Any]) -> dict[str, str]:
    return _bearer(tenant_id, account["user_id"], account["membership_id"], account["roles"], account["tier"])


async def _ensure_lesson_and_paper(
    tenant_id: uuid.UUID,
    teacher: dict[str, Any],
    author: dict[str, Any],
    taxonomy_id: uuid.UUID,
    there: dict[str, bool],
) -> dict[str, bool]:
    """The lesson (by the author) and the paper (by the teacher) go through the HTTP API, so the rules apply.

    A teacher no longer writes content or the taxonomy: the lesson belongs to the content author.
    """
    headers = _headers(tenant_id, teacher)
    author_headers = _headers(tenant_id, author)
    done = {"lesson": False, "paper": False}

    def create_lesson(client: Any) -> dict[str, str]:
        created = client.post(
            "/api/v1/content",
            headers=author_headers,
            json={
                "content_type": "LESSON",
                "slug": LESSON_SLUG,
                "title": "Công thức Bayes",
                "summary_md": "Xác suất có điều kiện và công thức Bayes.",
                "primary_taxonomy_node_id": str(taxonomy_id),
                "rights_status": "OWNED",
                "blocks": [
                    {
                        "block_order": 0,
                        "block_type": "THEORY_SUMMARY",
                        "payload_json": {"text": "P(A|B) cho biết khả năng xảy ra A khi đã biết B xảy ra."},
                    },
                    {
                        "block_order": 1,
                        "block_type": "FORMULA_BOX",
                        "payload_json": {"latex": "P(A|B)=\\frac{P(B|A)P(A)}{P(B)}"},
                    },
                    {
                        "block_order": 2,
                        "block_type": "WORKED_EXAMPLE",
                        "payload_json": {"text": "P(A)=0,3; P(B|A)=0,8; P(B|Ā)=0,2 ⇒ P(A|B)≈0,632."},
                    },
                ],
            },
        )
        _ok(created)
        return {"item": created.json()["item"]["id"], "version": created.json()["version"]["id"]}

    def publish_lesson(client: Any, version_id: str) -> None:
        _ok(client.post(f"/api/v1/content-versions/{version_id}/publish", headers=author_headers))

    def make_paper(client: Any) -> None:
        made = client.post(
            "/api/v1/assessments",
            headers=headers,
            json={
                "title": PAPER_TITLE,
                "subject_code": "math",
                "grade_band": "GRADE_12",
                "exam_type_code": "THPTQG",
                "rights_status": "OWNED",
            },
        )
        _ok(made)
        assessment_id = made.json()["id"]
        _ok(
            client.post(
                f"/api/v1/assessments/{assessment_id}/assemble", headers=headers, json={"random_seed": ASSEMBLY_SEED}
            )
        )
        _ok(client.post(f"/api/v1/assessments/{assessment_id}/publish", headers=headers))

    if not there["lesson"]:
        ids = await _through_api(create_lesson)
        # The reviewer's decision (A09), recorded here as the demo's editorial approval.
        await _verify_content(tenant_id, uuid.UUID(ids["item"]))
        await _through_api(lambda client: publish_lesson(client, ids["version"]))
        done["lesson"] = True
    if not there["paper"]:
        await _through_api(make_paper)
        done["paper"] = True
    return done


async def _lookup_ids(tenant_id: uuid.UUID, learner: uuid.UUID) -> dict[str, Any]:
    """The published paper's version, the demo class and what is already seeded for them."""
    async with tenant_transaction(tenant_id) as session:
        paper = (
            await session.execute(
                text("SELECT id, current_version_id FROM assessments WHERE tenant_id = :t AND title = :n"),
                {"t": tenant_id, "n": PAPER_TITLE},
            )
        ).one()
        class_id = (
            await session.execute(
                text("SELECT id FROM classes WHERE tenant_id = :t AND invite_code = :c"),
                {"t": tenant_id, "c": CLASS_NAME},
            )
        ).scalar_one()
        has_assignment = (
            await session.execute(
                text(
                    "SELECT 1 FROM assessment_assignments "
                    "WHERE tenant_id = :t AND class_id = :c AND assessment_version_id = :v"
                ),
                {"t": tenant_id, "c": class_id, "v": paper.current_version_id},
            )
        ).first()
        has_plan = (
            await session.execute(
                text("SELECT 1 FROM learning_plans WHERE tenant_id = :t AND membership_id = :m"),
                {"t": tenant_id, "m": learner},
            )
        ).first()
    return {
        "assessment_id": paper.id,
        "version_id": paper.current_version_id,
        "class_id": class_id,
        "has_assignment": has_assignment is not None,
        "has_plan": has_plan is not None,
    }


async def _ensure_error_notes(tenant_id: uuid.UUID, learner: uuid.UUID) -> int:
    """Four notes of the learner, one per stage of the rule "two correct in a row closes a note"."""
    created = 0
    async with tenant_transaction(tenant_id) as session:
        for n, (status, wrong, streak) in enumerate(ERROR_NOTE_STAGES, start=1):
            code = f"{QUESTION_CODE_PREFIX}-{n:02d}"
            question = (
                await session.execute(
                    text(
                        "SELECT current_version_id, primary_taxonomy_node_id FROM questions "
                        "WHERE tenant_id = :t AND stable_code = :c"
                    ),
                    {"t": tenant_id, "c": code},
                )
            ).one()
            result = await session.execute(
                text(
                    "INSERT INTO error_notes (tenant_id, membership_id, question_version_id, "
                    "primary_taxonomy_node_id, wrong_count, consecutive_correct_count, status, resolved_at) "
                    "VALUES (:t, :m, :v, :n, :w, :s, CAST(:st AS varchar), "
                    "CASE WHEN CAST(:st AS varchar) = 'RESOLVED' THEN now() END) "
                    "ON CONFLICT (membership_id, question_version_id) DO NOTHING"
                ),
                {
                    "t": tenant_id,
                    "m": learner,
                    "v": question.current_version_id,
                    "n": question.primary_taxonomy_node_id,
                    "w": wrong,
                    "s": streak,
                    "st": status,
                },
            )
            created += _rows(result)
    return created


async def _ensure_assignment_and_plan(
    tenant_id: uuid.UUID, teacher: dict[str, Any], learner: dict[str, Any], ids: dict[str, Any]
) -> dict[str, bool]:
    """The class assignment (teacher) and the learner's plan (learner), both through the API."""
    teacher_headers, learner_headers = _headers(tenant_id, teacher), _headers(tenant_id, learner)
    done = {"assignment": False, "learning_plan": False}

    def assign(client: Any) -> None:
        _ok(
            client.post(
                "/api/v1/assignments",
                headers=teacher_headers,
                json={
                    "assessment_version_id": str(ids["version_id"]),
                    "class_id": str(ids["class_id"]),
                    "max_attempts": 3,
                    "reveal_policy": "IMMEDIATE",
                },
            )
        )

    def plan(client: Any) -> None:
        _ok(client.post("/api/v1/learning-plans", headers=learner_headers, json={}))

    if not ids["has_assignment"]:
        await _through_api(assign)
        done["assignment"] = True
    if not ids["has_plan"]:
        await _through_api(plan)
        done["learning_plan"] = True
    return done


C01_SAMPLES: tuple[dict[str, Any], ...] = (
    {
        "stable_code": "DEMO-C01-01",
        "item_type": "SINGLE_CHOICE",
        "difficulty_level": "NB",
        "stem_text": "Cho hai biến cố A, B với P(B) > 0. Công thức Bayes cho P(A|B) là gì?",
        "options": [
            {"key": "A", "text": "P(B|A)·P(A) / P(B)"},
            {"key": "B", "text": "P(A)·P(B)"},
            {"key": "C", "text": "P(B|A) / P(A)"},
            {"key": "D", "text": "P(A) + P(B) - P(A∩B)"},
        ],
        "answer_schema": {"correct_option": "A"},
    },
    {
        "stable_code": "DEMO-C01-02",
        "item_type": "CLUSTER_TRUE_FALSE",
        "difficulty_level": "TH",
        "stem_text": "Cho P(A) = 0,4 và P(B|A) = 0,5. Xét các mệnh đề sau.",
        "options": [
            {"key": "a", "text": "P(A∩B) = 0,2"},
            {"key": "b", "text": "P(A|B) luôn bằng P(B|A)"},
            {"key": "c", "text": "Nếu A, B độc lập thì P(A|B) = P(A)"},
            {"key": "d", "text": "P(A∩B) có thể lớn hơn P(A)"},
        ],
        "answer_schema": {"statements": {"a": True, "b": False, "c": True, "d": False}},
    },
    {
        "stable_code": "DEMO-C01-03",
        "item_type": "SHORT_NUMERIC",
        "difficulty_level": "VD",
        "stem_text": "Một xét nghiệm có P(B|A) = 0,9, P(B|Ā) = 0,1 và tỉ lệ mắc P(A) = 0,5. Tính P(A|B).",
        "options": [],
        "answer_schema": {"value": "0,9"},
    },
    {
        "stable_code": "DEMO-C01-04",
        "item_type": "ESSAY",
        "difficulty_level": "VDC",
        "stem_text": "Chứng minh công thức xác suất toàn phần rồi suy ra công thức Bayes.",
        "options": [],
        "answer_schema": {
            "rubric": "1 điểm: phân hoạch không gian mẫu; 1 điểm: công thức xác suất toàn phần",
            "max_points": 2.0,
        },
    },
)
EXAM_IMPORT_SECTION_TITLE = "Phan I. Trac nghiem (demo)"


def _demo_exam_pdf() -> bytes:
    """A one-page exam paper, byte-identical on every run (so the upload deduplicates), ASCII text only."""
    import io

    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    top = A4[1]
    lines = (
        (200, "Cau 1. Cho P(A) = 0,3; P(B|A) = 0,8. Tinh P(A and B)."),
        (225, "A. 0,24    B. 0,38    C. 0,11    D. 0,50"),
        (290, "Cau 2. Hai bien co A, B doc lap thi P(A|B) bang?"),
        (315, "A. P(A)    B. P(B)    C. 0    D. 1"),
        (380, "Cau 3. P(A|B) lon nhat bang bao nhieu?"),
        (405, "A. 0    B. 0,5    C. 1    D. 2"),
    )
    for y_from_top, text_line in lines:
        page.drawString(50, top - y_from_top, text_line)
    page.save()
    return buffer.getvalue()


async def _ensure_c01_questions(tenant_id: uuid.UUID, teacher: dict[str, Any], taxonomy_id: uuid.UUID) -> int:
    """Four DRAFT questions written through `POST /api/v1/questions`, so the bank shows drafts beside the published."""
    async with tenant_transaction(tenant_id) as session:
        have = {
            r.stable_code
            for r in await session.execute(
                text("SELECT stable_code FROM questions WHERE tenant_id = :t AND stable_code LIKE 'DEMO-C01-%'"),
                {"t": tenant_id},
            )
        }
    todo = [q for q in C01_SAMPLES if q["stable_code"] not in have]
    if not todo:
        return 0
    headers = _headers(tenant_id, teacher)

    def create(client: Any) -> None:
        for sample in todo:
            _ok(
                client.post(
                    "/api/v1/questions",
                    headers=headers,
                    json={**sample, "primary_taxonomy_node_id": str(taxonomy_id), "rights_status": "OWNED"},
                )
            )

    await _through_api(create)
    return len(todo)


async def _ensure_exam_import(tenant_id: uuid.UUID, teacher: dict[str, Any]) -> bool:
    """One exam paper uploaded, extracted and structured: a PENDING_REVIEW session to open in the BBox review."""
    async with tenant_transaction(tenant_id) as session:
        there = (
            await session.execute(
                text(
                    "SELECT 1 FROM exam_import_sessions "
                    "WHERE tenant_id = :t AND proposed_structure_json->'sections'->0->>'title' = :title"
                ),
                {"t": tenant_id, "title": EXAM_IMPORT_SECTION_TITLE},
            )
        ).first()
    if there is not None:
        return False
    headers = _headers(tenant_id, teacher)
    pdf = _demo_exam_pdf()

    def create(client: Any) -> None:
        uploaded = _ok(
            client.post(
                "/api/v1/documents", headers=headers, files={"file": ("de-mau-bayes-demo.pdf", pdf, "application/pdf")}
            )
        )
        extracted = _ok(
            client.post(f"/api/v1/documents/{uploaded.json()['source_document_id']}/extract", headers=headers)
        )

        def question(no: int, points: float, y0: int, answer: str) -> dict[str, Any]:
            return {
                "question_no": no,
                "points": points,
                "page_number": 1,
                "paired_answer": answer,
                "stem_md": f"Cau {no}.",
                "options_md": ["A", "B", "C", "D"],
                "bbox_json": {"x0": 50, "y0": y0, "x1": 540, "y1": y0 + 55},
            }

        _ok(
            client.post(
                "/api/v1/exam-imports",
                headers=headers,
                json={
                    "extraction_run_id": extracted.json()["id"],
                    "rights_status_override": "OWNED",
                    "expected_total_points": 10.0,
                    "create_question_candidates": False,
                    "sections": [
                        {
                            "section_code": "PART_I_MCQ",
                            "title": EXAM_IMPORT_SECTION_TITLE,
                            "questions": [
                                question(1, 4.0, 190, "A"),
                                question(2, 3.0, 280, "A"),
                                question(3, 3.0, 370, "C"),
                            ],
                        }
                    ],
                },
            )
        )

    await _through_api(create)
    return True


async def _lookup_review_ids(tenant_id: uuid.UUID) -> dict[str, list[str]]:
    async with tenant_transaction(tenant_id) as session:
        imports = [
            str(r.id)
            for r in await session.execute(
                text("SELECT id FROM exam_import_sessions WHERE tenant_id = :t ORDER BY created_at, id"),
                {"t": tenant_id},
            )
        ]
        questions = [
            str(r.id)
            for r in await session.execute(
                text(
                    "SELECT id FROM questions "
                    "WHERE tenant_id = :t AND stable_code LIKE 'DEMO-C01-%' ORDER BY stable_code"
                ),
                {"t": tenant_id},
            )
        ]
    return {"exam_import_ids": imports, "c01_question_ids": questions}


async def seed_demo(password: str | None = None) -> dict[str, Any]:
    password = password or os.environ.get("SEED_DEMO_PASSWORD") or DEFAULT_PASSWORD
    catalogues = await _ensure_catalogues()
    tenant_id = await _ensure_tenant()
    accounts = await _ensure_accounts(tenant_id, password)
    taxonomy_id = await _ensure_taxonomy()
    questions = await _ensure_questions(tenant_id, taxonomy_id)
    class_created = await _ensure_class(
        tenant_id, accounts["teacher"]["membership_id"], accounts["learner"]["membership_id"]
    )
    there = await _already_there(tenant_id)
    content = await _ensure_lesson_and_paper(
        tenant_id, accounts["teacher"], accounts["content_author"], taxonomy_id, there
    )
    learner = accounts["learner"]
    notes = await _ensure_error_notes(tenant_id, learner["membership_id"])
    ids = await _lookup_ids(tenant_id, learner["membership_id"])
    classroom = await _ensure_assignment_and_plan(tenant_id, accounts["teacher"], learner, ids)
    c01 = await _ensure_c01_questions(tenant_id, accounts["content_author"], taxonomy_id)
    exam_import = await _ensure_exam_import(tenant_id, accounts["content_author"])
    review = await _lookup_review_ids(tenant_id)
    await engine.dispose()
    return {
        "tenant": {"slug": TENANT_SLUG, "id": str(tenant_id)},
        "accounts": {k: v["email"] for k, v in accounts.items()},
        "entities": {
            "class_id": str(ids["class_id"]),
            "assessment_id": str(ids["assessment_id"]),
            "assessment_version_id": str(ids["version_id"]),
            **review,
        },
        "created": {
            "questions": questions,
            "class": class_created,
            "error_notes": notes,
            "c01_questions": c01,
            "exam_import": exam_import,
            **content,
            **classroom,
            **catalogues,
        },
        "finished_at": datetime.now(UTC).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demo data for a local run (idempotent).")
    parser.add_argument(
        "--password", help="demo password for the four accounts (default: SEED_DEMO_PASSWORD or built-in)"
    )
    args = parser.parse_args(argv)
    if get_settings().app_env.lower() not in ("dev", "development", "test", "testing"):
        print(
            "seed_demo refuses to run outside APP_ENV=dev/test: its accounts share a public password", file=sys.stderr
        )
        return 2
    report = asyncio.run(seed_demo(args.password))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
