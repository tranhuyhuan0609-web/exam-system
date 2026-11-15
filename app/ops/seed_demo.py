"""Demo data for a local run: ``python -m app.ops.seed_demo``.

Idempotent: every step looks before it writes, a second run changes nothing. It builds, in the migrated database,

* the tenant ``h2edu-demo`` and four accounts, one per role, all with the same demo password (below);
* the class ``class-12a1`` (teacher + learner), the lesson ``cong-thuc-bayes`` (published),
* 22 published, verified Bayes questions (12 single choice, 4 true/false clusters, 6 short numeric) and the paper
  "THPTQG 2025 demo" assembled from them and published, through the real API so every rule applies;
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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
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

ACCOUNTS: tuple[tuple[str, str, str, list[str], str], ...] = (
    # key, email, pseudonym, roles, entitlement tier
    ("platform_admin", "platform_admin@h2edu.demo", "Platform Admin", ["PLATFORM_ADMIN"], "SCHOOL_ENTERPRISE"),
    ("tenant_admin", "tenant_admin@h2edu.demo", "Tenant Admin", ["TENANT_ADMIN"], "SCHOOL_ENTERPRISE"),
    ("teacher", "teacher@h2edu.demo", "Cô Lan", ["TEACHER"], "TEACHER_PRO"),
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


def _ensure_lesson_and_paper(
    tenant_id: uuid.UUID, teacher: dict[str, Any], taxonomy_id: uuid.UUID, there: dict[str, bool]
) -> dict[str, bool]:
    """The lesson and the paper go through the HTTP API (TestClient), so authoring and publishing rules apply."""
    from fastapi.testclient import TestClient

    from app.main import app

    headers = _bearer(tenant_id, teacher["user_id"], teacher["membership_id"], teacher["roles"], teacher["tier"])
    done = {"lesson": False, "paper": False}
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        if not there["lesson"]:
            created = client.post(
                "/api/v1/content",
                headers=headers,
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
            # The reviewer's decision (A09), recorded here as the demo's editorial approval.
            asyncio.run(_verify_content(tenant_id, uuid.UUID(created.json()["item"]["id"])))
            published = client.post(
                f"/api/v1/content-versions/{created.json()['version']['id']}/publish", headers=headers
            )
            _ok(published)
            done["lesson"] = True
        if not there["paper"]:
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
                    f"/api/v1/assessments/{assessment_id}/assemble",
                    headers=headers,
                    json={"random_seed": ASSEMBLY_SEED},
                )
            )
            _ok(client.post(f"/api/v1/assessments/{assessment_id}/publish", headers=headers))
            done["paper"] = True
    return done


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
    content = await asyncio.to_thread(_ensure_lesson_and_paper, tenant_id, accounts["teacher"], taxonomy_id, there)
    return {
        "tenant": {"slug": TENANT_SLUG, "id": str(tenant_id)},
        "accounts": {k: v["email"] for k, v in accounts.items()},
        "created": {"questions": questions, "class": class_created, **content, **catalogues},
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
