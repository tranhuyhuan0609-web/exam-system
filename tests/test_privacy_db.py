"""F04 privacy (TASK-07): users -> legacy_users, identity tables, consent, crypto-shredding, RLS and the API.

DoD: a learner under 16 without parental consent gets no sensitive PII stored; destroying the key yields
a valid erasure certificate and the data can no longer be decrypted; the legacy (Jinja) user table keeps
working as legacy_users. Also covers PUT /api/v1/memberships/{id}/roles (F05) and cross-tenant IDOR.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.access.public import create_role
from app.modules.privacy.internal.crypto_shredding import blind_index_pepper
from app.modules.privacy.public import (
    InMemoryKeyEncryptionService,
    KeyShreddedError,
    ParentalConsentRequiredError,
    bind_user,
    compute_email_blind_index,
    read_profile,
    set_key_service,
    store_sensitive_profile,
)
from app.platform.auth_context import mint_hs256_token
from app.platform.models import Tenant
from app.platform.tenancy import DbRole, tenant_transaction

THIS_YEAR = datetime.now(UTC).year
PEPPER = blind_index_pepper(get_settings().secret_key)
SEEDED_PERMISSIONS = ("document:import", "exam:publish", "question:approve")


def run(coro):
    return asyncio.run(coro)


async def _scalar(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _execute(sql: str, **params) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(text(sql), params)


async def _row(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).one_or_none()


@pytest.fixture(autouse=True)
def keys():
    """A fresh key service per test (the KEK store lives outside the database)."""
    service = InMemoryKeyEncryptionService()
    previous = set_key_service(service)
    yield service
    set_key_service(previous)


@pytest.fixture(autouse=True)
def _permission_catalog():
    async def seed():
        async with SessionLocal() as session, session.begin():
            for code in SEEDED_PERMISSIONS:
                await session.execute(
                    text("INSERT INTO permissions (code, description) VALUES (:c, :c) ON CONFLICT DO NOTHING"),
                    {"c": code},
                )

    run(seed())


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    async def create():
        suffix = uuid.uuid4().hex[:8]
        async with SessionLocal() as session, session.begin():
            a, b = Tenant(slug=f"prv-a-{suffix}", name="A"), Tenant(slug=f"prv-b-{suffix}", name="B")
            session.add_all([a, b])
            await session.flush()
            return a.id, b.id

    return run(create())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _headers(
    tenant_id: uuid.UUID,
    roles: list[str],
    *,
    user_id: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
) -> dict[str, str]:
    token = mint_hs256_token(
        secret=get_settings().secret_key,
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        membership_id=membership_id or uuid.uuid4(),
        roles=roles,
        issued_at=int(time.time()),
    )
    return {"Authorization": f"Bearer {token}"}


def _enroll(client, tenant_id, email, **body):
    response = client.post(
        "/api/v1/memberships", headers=_headers(tenant_id, ["TENANT_ADMIN"]), json={"email": email, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- schema: rename, tables, RLS, privileges -------------------------------------------------------


def test_the_legacy_table_was_renamed_with_its_keys_and_foreign_keys():
    assert run(_scalar("SELECT to_regclass('legacy_users') IS NOT NULL"))
    constraints = run(
        _scalar(
            "SELECT array_agg(conname ORDER BY conname) FROM pg_constraint WHERE conrelid = 'legacy_users'::regclass"
        )
    )
    assert {"legacy_users_pkey", "legacy_users_email_key"} <= set(constraints)
    assert run(_scalar("SELECT pg_get_serial_sequence('legacy_users', 'id')")) == "public.legacy_users_id_seq"
    referencing = run(_scalar("SELECT count(*) FROM pg_constraint WHERE confrelid = 'legacy_users'::regclass"))
    assert referencing == 6
    legacy_children = ["exams", "exam_attempts", "import_jobs", "exam_enrollments", "crawl_runs", "library_views"]
    on_new_users = (
        "SELECT count(*) FROM pg_constraint "
        "WHERE confrelid = 'users'::regclass AND conrelid::regclass::text = ANY(:children)"
    )
    assert run(_scalar(on_new_users, children=legacy_children)) == 0


def test_the_legacy_login_still_works(client, make_user):
    make_user("legacy@example.com", "password123")
    response = client.post(
        "/login", data={"email": "legacy@example.com", "password": "password123"}, follow_redirects=False
    )
    assert response.status_code in (302, 303)
    assert run(_scalar("SELECT count(*) FROM legacy_users WHERE email = 'legacy@example.com'")) == 1
    assert run(_scalar("SELECT count(*) FROM users")) == 0


@pytest.mark.parametrize("table", ["memberships", "parental_consents", "erasure_certificates"])
def test_tenant_tables_use_the_strict_policy(table):
    assert run(_scalar("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t", t=table))
    qual = run(
        _scalar("SELECT qual FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation_policy'", t=table)
    )
    assert "( SELECT current_app_tenant_id() AS current_app_tenant_id)" in qual


def test_key_table_is_closed_to_every_runtime_role():
    assert run(
        _scalar(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = 'user_pii_encryption_keys'"
        )
    )
    for role in DbRole:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not run(
                _scalar("SELECT has_table_privilege(:r, 'user_pii_encryption_keys', :p)", r=role.value, p=privilege)
            ), (role, privilege)


def test_definer_functions_are_executable_by_the_api_role_only():
    functions = (
        "f04_find_user_id",
        "f04_register_user",
        "f04_store_user_key",
        "f04_load_user_key",
        "f04_shred_user_key",
    )
    for name in functions:
        rows = run(
            _row(
                "SELECT p.prosecdef, has_function_privilege('ulp_app_rw', p.oid, 'EXECUTE') AS api, "
                "has_function_privilege('ulp_exam_session_rw', p.oid, 'EXECUTE') AS exam, "
                "has_function_privilege('ulp_worker_ingestion', p.oid, 'EXECUTE') AS ingest, "
                "has_function_privilege('ulp_worker_grading', p.oid, 'EXECUTE') AS grading, "
                "has_function_privilege('ulp_analytics_ro', p.oid, 'EXECUTE') AS analytics "
                "FROM pg_proc p WHERE p.proname = :n",
                n=name,
            )
        )
        assert rows.prosecdef is True and rows.api is True, name
        assert not (rows.exam or rows.ingest or rows.grading or rows.analytics), name


def test_users_privileges_follow_decision_7():
    assert run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'users', 'SELECT')"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'users', 'INSERT')"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'users', 'DELETE')"))
    assert not run(_scalar("SELECT has_column_privilege('ulp_app_rw', 'users', 'email_blind_index', 'UPDATE')"))
    assert run(_scalar("SELECT has_table_privilege('ulp_exam_session_rw', 'memberships', 'SELECT')"))
    assert run(_scalar("SELECT has_table_privilege('ulp_worker_grading', 'memberships', 'SELECT')"))
    assert not run(_scalar("SELECT has_table_privilege('ulp_app_rw', 'erasure_certificates', 'UPDATE')"))


def test_is_user_minor_and_the_status_view():
    assert run(_scalar("SELECT is_user_minor(:y)", y=THIS_YEAR - 12)) is True
    assert run(_scalar("SELECT is_user_minor(:y)", y=THIS_YEAR - 16)) is False
    assert run(_scalar("SELECT is_user_minor(NULL)")) is False
    options = run(_scalar("SELECT reloptions FROM pg_class WHERE relname = 'v_users_status'"))
    assert "security_invoker=true" in options


# --- tenants ---------------------------------------------------------------------------------------


def test_platform_admins_create_tenants(client, tenants):
    tenant_a, _ = tenants
    slug = f"truong-{uuid.uuid4().hex[:6]}"
    created = client.post(
        "/api/v1/tenants",
        headers=_headers(tenant_a, ["PLATFORM_ADMIN"]),
        json={"slug": slug, "name": "THPT Chu Văn An", "daily_ai_budget_usd": "25.50"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == slug and created.json()["daily_ai_budget_usd"] == "25.50"
    again = client.post(
        "/api/v1/tenants", headers=_headers(tenant_a, ["PLATFORM_ADMIN"]), json={"slug": slug, "name": "x"}
    )
    assert again.status_code == 409
    tenant_admin = client.post(
        "/api/v1/tenants", headers=_headers(tenant_a, ["TENANT_ADMIN"]), json={"slug": "abc-x", "name": "x"}
    )
    assert tenant_admin.status_code == 403
    bad = client.post(
        "/api/v1/tenants", headers=_headers(tenant_a, ["PLATFORM_ADMIN"]), json={"slug": "Bad Slug", "name": "x"}
    )
    assert bad.status_code == 422


# --- memberships, no plaintext PII -----------------------------------------------------------------


def test_an_adult_member_is_stored_without_any_plaintext_pii(client, tenants, keys):
    tenant_a, _ = tenants
    body = _enroll(
        client, tenant_a, "Teacher.Lan@Example.com", birth_year=1990, display_name="Nguyễn Thị Lan", phone="0901234567"
    )
    assert body["user_created"] is True and body["pii_stored"] is True and body["user_status"] == "ACTIVE"
    assert body["is_minor"] is False and body["parental_consent_required"] is False
    assert body["membership"]["tenant_id"] == str(tenant_a) and body["membership"]["roles"] == ["LEARNER"]
    user_id = uuid.UUID(body["membership"]["user_id"])

    row = run(_row("SELECT email_blind_index, pii_ciphertext_bytea, password_hash FROM users WHERE id = :u", u=user_id))
    assert row.email_blind_index == compute_email_blind_index("teacher.lan@example.com", PEPPER)
    for clear in (b"teacher.lan", "Nguyễn".encode(), b"0901234567"):
        assert clear not in row.pii_ciphertext_bytea
    assert row.password_hash is None

    async def profile():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            return await read_profile(session, user_id, keys=keys)

    assert run(profile()) == {
        "email": "Teacher.Lan@example.com",  # EmailStr lower-cases the domain
        "display_name": "Nguyễn Thị Lan",
        "phone": "0901234567",
    }
    key = run(
        _row(
            "SELECT kms_kek_key_id, key_status, encrypted_dek_bytea FROM user_pii_encryption_keys WHERE user_id = :u",
            u=user_id,
        )
    )
    assert key.kms_kek_key_id == f"vault://transit/keys/user-{user_id}" and key.key_status == "ACTIVE"
    assert key.encrypted_dek_bytea is not None and len(key.encrypted_dek_bytea) > 32  # wrapped, not the raw DEK


def test_the_same_person_joins_a_second_tenant_as_the_same_user(client, tenants):
    tenant_a, tenant_b = tenants
    first = _enroll(client, tenant_a, "hs@example.com", birth_year=2000)
    second = _enroll(client, tenant_b, "HS@example.com")
    assert second["user_created"] is False and second["pii_stored"] is False
    assert second["membership"]["user_id"] == first["membership"]["user_id"]
    duplicate = client.post(
        "/api/v1/memberships", headers=_headers(tenant_a, ["ADMIN"]), json={"email": "hs@example.com"}
    )
    assert duplicate.status_code == 409


def test_membership_roles_must_be_roles_of_the_tenant(client, tenants):
    tenant_a, tenant_b = tenants

    async def make_role():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await create_role(session, tenant_id=tenant_b, code="TEACHER", name="Teacher")

    run(make_role())
    response = client.post(
        "/api/v1/memberships",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"email": "x@example.com", "roles": ["TEACHER"]},
    )
    assert response.status_code == 422  # TEACHER exists only in tenant B
    assert run(_scalar("SELECT count(*) FROM memberships")) == 0


def test_only_tenant_admins_enroll(client, tenants):
    tenant_a, _ = tenants
    assert (
        client.post(
            "/api/v1/memberships", headers=_headers(tenant_a, ["TEACHER"]), json={"email": "a@example.com"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/memberships", json={"email": "a@example.com"}).status_code == 401
    bad = client.post("/api/v1/memberships", headers=_headers(tenant_a, ["ADMIN"]), json={"email": "not-an-email"})
    assert bad.status_code == 422


# --- under 16: consent gate (DoD) ----------------------------------------------------------------


def test_a_minor_without_consent_gets_no_sensitive_pii_until_a_guardian_consents(client, tenants, keys):
    tenant_a, _ = tenants
    body = _enroll(
        client, tenant_a, "be.an@example.com", birth_year=THIS_YEAR - 12, display_name="Bé An", phone="0911111111"
    )
    assert body["is_minor"] is True and body["pii_stored"] is False
    assert body["user_status"] == "PENDING_PARENTAL_CONSENT" and body["parental_consent_required"] is True
    learner = uuid.UUID(body["membership"]["user_id"])
    assert run(_scalar("SELECT pii_ciphertext_bytea FROM users WHERE id = :u", u=learner)) is None
    assert run(_scalar("SELECT is_minor FROM v_users_status WHERE id = :u", u=learner)) is True

    async def store():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await store_sensitive_profile(
                session, learner, {"display_name": "Bé An"}, keys=keys, today=datetime.now(UTC).date()
            )

    with pytest.raises(ParentalConsentRequiredError):
        run(store())
    assert run(_scalar("SELECT pii_ciphertext_bytea FROM users WHERE id = :u", u=learner)) is None

    consent = client.post(
        "/api/v1/privacy/parental-consents",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={
            "learner_user_id": str(learner),
            "guardian_email": "me.cua.an@example.com",
            "guardian_relationship": "PARENT",
            "verification_method": "OTP_EMAIL",
        },
    )
    assert consent.status_code == 201, consent.text
    assert consent.json()["learner_status"] == "ACTIVE"
    assert consent.json()["consent_scope_json"] == {"learning_history": True, "ai_tutor": True}
    stored = run(_row("SELECT guardian_email_blind_index, guardian_email_ciphertext FROM parental_consents"))
    assert stored.guardian_email_blind_index == compute_email_blind_index("me.cua.an@example.com", PEPPER)
    assert b"me.cua.an" not in stored.guardian_email_ciphertext

    run(store())  # now allowed
    assert run(_scalar("SELECT status FROM users WHERE id = :u", u=learner)) == "ACTIVE"


@pytest.mark.parametrize(
    ("relationship", "method"),
    [("UNCLE", "OTP_EMAIL"), ("PARENT", "PHONE_CALL")],
)
def test_consent_evidence_must_use_the_spec_values(client, tenants, relationship, method):
    tenant_a, _ = tenants
    learner = _enroll(client, tenant_a, "c@example.com", birth_year=THIS_YEAR - 10)["membership"]["user_id"]
    response = client.post(
        "/api/v1/privacy/parental-consents",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"learner_user_id": learner, "guardian_email": "p@example.com", "guardian_relationship": relationship,
              "verification_method": method},
    )  # fmt: skip
    assert response.status_code == 422
    assert run(_scalar("SELECT count(*) FROM parental_consents")) == 0


def test_a_tenant_cannot_consent_for_another_tenants_learner(client, tenants):
    tenant_a, tenant_b = tenants
    learner = _enroll(client, tenant_b, "b@example.com", birth_year=THIS_YEAR - 10)["membership"]["user_id"]
    response = client.post(
        "/api/v1/privacy/parental-consents",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"learner_user_id": learner, "guardian_email": "p@example.com", "guardian_relationship": "PARENT",
              "verification_method": "OTP_EMAIL"},
    )  # fmt: skip
    assert response.status_code == 404
    assert run(_scalar("SELECT status FROM users WHERE id = :u", u=learner)) == "PENDING_PARENTAL_CONSENT"


# --- right to erasure (DoD) ------------------------------------------------------------------------


def test_self_erasure_shreds_the_key_and_files_a_valid_certificate(client, tenants, keys):
    tenant_a, tenant_b = tenants
    body = _enroll(client, tenant_a, "xoa@example.com", birth_year=1995, display_name="Người Xoá")
    user_id = uuid.UUID(body["membership"]["user_id"])
    membership_id = uuid.UUID(body["membership"]["id"])
    kek = f"vault://transit/keys/user-{user_id}"
    assert keys.has_key(kek)

    response = client.post(
        "/api/v1/privacy/data-erasure-requests",
        headers=_headers(tenant_a, ["LEARNER"], user_id=user_id, membership_id=membership_id),
        json={"reason": "Tôi muốn xoá dữ liệu"},
    )
    assert response.status_code == 201, response.text
    certificate = response.json()
    assert certificate["subject_user_id"] == str(user_id) and certificate["tenant_id"] == str(tenant_a)
    assert certificate["kms_kek_key_id"] == kek and re.fullmatch(r"[0-9a-f]{64}", certificate["verification_hash"])
    assert certificate["backup_expiry_guaranteed_at"] > certificate["primary_db_shredded_at"]

    assert not keys.has_key(kek)  # the KEK is gone from the key service
    key = run(
        _row(
            "SELECT key_status, encrypted_dek_bytea, shredded_at FROM user_pii_encryption_keys WHERE user_id = :u",
            u=user_id,
        )
    )
    assert key.key_status == "SHREDDED" and key.encrypted_dek_bytea is None and key.shredded_at is not None
    user = run(
        _row(
            "SELECT status, pii_ciphertext_bytea, pseudonym_handle, email_blind_index FROM users WHERE id = :u",
            u=user_id,
        )
    )
    assert user.status == "ANONYMIZED" and user.pii_ciphertext_bytea is None and user.pseudonym_handle == "Anonymized"
    assert user.email_blind_index != compute_email_blind_index("xoa@example.com", PEPPER)

    async def profile():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            return await read_profile(session, user_id, keys=keys)

    with pytest.raises(KeyShreddedError):
        run(profile())

    async def certificates(tenant):
        async with tenant_transaction(tenant, role=DbRole.APP_RW) as session:
            return (await session.execute(text("SELECT count(*) FROM erasure_certificates"))).scalar()

    assert run(certificates(tenant_a)) == 1 and run(certificates(tenant_b)) == 0
    # The e-mail is free again: enrolling it makes a new person.
    again = _enroll(client, tenant_a, "xoa2@example.com")
    assert again["membership"]["user_id"] != str(user_id)


def test_erasure_digest_is_the_pdp_compliance_digest(client, tenants):
    from app.ops.pdp_compliance import evaluate_crypto_shredding_erasure

    tenant_a, _ = tenants
    user_id = _enroll(client, tenant_a, "d@example.com", birth_year=1990)["membership"]["user_id"]
    response = client.post(
        "/api/v1/privacy/data-erasure-requests",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"subject_user_id": user_id},
    )
    assert response.status_code == 201, response.text
    expected = evaluate_crypto_shredding_erasure(
        tenant_id=str(tenant_a), subject_id=user_id, vault_key_destroyed=True,
        remaining_plaintext_pii_fields=(), audit_trail_preserved=True,
    )  # fmt: skip
    assert expected.compliant and response.json()["verification_hash"] == expected.certificate_sha256


def test_erasure_permissions(client, tenants, keys):
    tenant_a, tenant_b = tenants
    shared = _enroll(client, tenant_a, "shared@example.com", birth_year=1990)["membership"]["user_id"]
    _enroll(client, tenant_b, "shared@example.com")
    only_b = _enroll(client, tenant_b, "onlyb@example.com", birth_year=1990)["membership"]["user_id"]

    # A tenant may not erase a person another tenant also serves, nor a person it does not know.
    for subject in (shared, only_b):
        response = client.post(
            "/api/v1/privacy/data-erasure-requests",
            headers=_headers(tenant_a, ["TENANT_ADMIN"]),
            json={"subject_user_id": subject},
        )
        assert response.status_code == 404, subject
    # A non-administrator may only erase themself.
    teacher = client.post(
        "/api/v1/privacy/data-erasure-requests",
        headers=_headers(tenant_a, ["TEACHER"]),
        json={"subject_user_id": shared},
    )
    assert teacher.status_code == 403
    assert run(_scalar("SELECT count(*) FROM erasure_certificates")) == 0
    assert run(_scalar("SELECT count(*) FROM user_pii_encryption_keys WHERE key_status = 'SHREDDED'")) == 0
    assert keys.has_key(f"vault://transit/keys/user-{shared}")


# --- the database itself, as the runtime role --------------------------------------------------


def test_tenant_b_sees_neither_users_nor_keys_of_tenant_a(client, tenants):
    tenant_a, tenant_b = tenants
    user_a = uuid.UUID(_enroll(client, tenant_a, "a1@example.com", birth_year=1990)["membership"]["user_id"])

    async def as_b():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            users = (await session.execute(text("SELECT count(*) FROM users"))).scalar()
            memberships = (await session.execute(text("SELECT count(*) FROM memberships"))).scalar()
            updated = (
                await session.execute(text("UPDATE users SET pseudonym_handle = 'x' WHERE id = :u"), {"u": user_a})
            ).rowcount
            return users, memberships, updated

    assert run(as_b()) == (0, 0, 0)

    async def read_keys_directly():
        async with tenant_transaction(tenant_a, role=DbRole.APP_RW) as session:
            await session.execute(text("SELECT * FROM user_pii_encryption_keys"))

    with pytest.raises(DBAPIError, match="permission denied"):
        run(read_keys_directly())

    async def load_key_from_b():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await session.execute(text("SELECT * FROM f04_load_user_key(:u)"), {"u": user_a})

    with pytest.raises(DBAPIError, match="not visible"):
        run(load_key_from_b())


def test_the_signed_in_user_sees_themself_without_a_membership_in_the_tenant(client, tenants):
    tenant_a, tenant_b = tenants
    user_a = uuid.UUID(_enroll(client, tenant_a, "self@example.com", birth_year=1990)["membership"]["user_id"])

    async def as_self_in_b():
        async with tenant_transaction(tenant_b, role=DbRole.APP_RW) as session:
            await bind_user(session, user_a)
            return (await session.execute(text("SELECT count(*) FROM users"))).scalar()

    assert run(as_self_in_b()) == 1


def test_registration_needs_a_tenant_context():
    async def anonymous():
        async with SessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE ulp_app_rw"))
            await session.execute(
                text("SELECT * FROM f04_register_user(:b, NULL, NULL, 'x', NULL, 'ACTIVE')"), {"b": "0" * 64}
            )

    with pytest.raises(DBAPIError, match="tenant context"):
        run(anonymous())
    assert run(_scalar("SELECT count(*) FROM users")) == 0


# --- PUT /api/v1/memberships/{id}/roles (F05 over F04 memberships) ---------------------------------


def _role(tenant_id, code, permissions):
    async def create():
        async with tenant_transaction(tenant_id, role=DbRole.APP_RW) as session:
            await create_role(session, tenant_id=tenant_id, code=code, name=code, permission_codes=permissions)

    run(create())


def test_roles_of_a_membership_drive_permissions_at_once(client, tenants):
    tenant_a, _ = tenants
    _role(tenant_a, "TEACHER", ["exam:publish"])
    body = _enroll(client, tenant_a, "gv@example.com", birth_year=1985)
    membership_id, user_id = uuid.UUID(body["membership"]["id"]), uuid.UUID(body["membership"]["user_id"])
    me = _headers(tenant_a, ["TEACHER"], user_id=user_id, membership_id=membership_id)  # token claims TEACHER

    before = client.get("/api/v1/permissions/me", headers=me).json()
    assert before["source"] == "membership" and before["roles"] == ["LEARNER"] and before["permissions"] == []

    put = client.put(
        f"/api/v1/memberships/{membership_id}/roles",
        headers=_headers(tenant_a, ["TENANT_ADMIN"]),
        json={"roles": ["teacher"]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["roles"] == ["TEACHER"] and put.json()["tenant_id"] == str(tenant_a)
    assert client.get("/api/v1/permissions/me", headers=me).json()["permissions"] == ["exam:publish"]

    client.put(
        f"/api/v1/memberships/{membership_id}/roles", headers=_headers(tenant_a, ["TENANT_ADMIN"]), json={"roles": []}
    )
    assert client.get("/api/v1/permissions/me", headers=me).json()["permissions"] == []  # revoked without a new token


def test_a_suspended_membership_has_no_permissions(client, tenants):
    tenant_a, _ = tenants
    _role(tenant_a, "TEACHER", ["exam:publish"])
    body = _enroll(client, tenant_a, "s@example.com", birth_year=1985, roles=["TEACHER"])
    membership_id, user_id = body["membership"]["id"], uuid.UUID(body["membership"]["user_id"])
    run(_execute("UPDATE memberships SET status = 'SUSPENDED' WHERE id = :m", m=membership_id))
    me = client.get(
        "/api/v1/permissions/me",
        headers=_headers(tenant_a, ["TEACHER"], user_id=user_id, membership_id=uuid.UUID(membership_id)),
    )
    assert me.json()["membership_status"] == "SUSPENDED" and me.json()["permissions"] == []


def test_a_token_naming_someone_elses_membership_gets_nothing(client, tenants):
    tenant_a, _ = tenants
    _role(tenant_a, "TEACHER", ["exam:publish"])
    body = _enroll(client, tenant_a, "owner@example.com", birth_year=1985, roles=["TEACHER"])
    stolen = _headers(tenant_a, ["TEACHER"], membership_id=uuid.UUID(body["membership"]["id"]))  # another user id
    assert client.get("/api/v1/permissions/me", headers=stolen).json()["permissions"] == []


def test_another_tenants_membership_is_not_found_and_unchanged(client, tenants):
    tenant_a, tenant_b = tenants
    _role(tenant_a, "TEACHER", [])
    _role(tenant_b, "TEACHER", [])
    theirs = _enroll(client, tenant_b, "b2@example.com", birth_year=1990)["membership"]["id"]
    response = client.put(
        f"/api/v1/memberships/{theirs}/roles", headers=_headers(tenant_a, ["TENANT_ADMIN"]), json={"roles": ["TEACHER"]}
    )
    assert response.status_code == 404
    assert run(_scalar("SELECT roles FROM memberships WHERE id = :m", m=theirs)) == ["LEARNER"]
    missing = client.put(
        f"/api/v1/memberships/{uuid.uuid4()}/roles", headers=_headers(tenant_a, ["TENANT_ADMIN"]), json={"roles": []}
    )
    assert missing.status_code == 404


def test_membership_roles_validation_and_authorization(client, tenants):
    tenant_a, _ = tenants
    mine = _enroll(client, tenant_a, "m@example.com", birth_year=1990)["membership"]["id"]
    url = f"/api/v1/memberships/{mine}/roles"
    assert client.put(url, headers=_headers(tenant_a, ["TENANT_ADMIN"]), json={"roles": ["GHOST"]}).status_code == 422
    assert client.put(url, headers=_headers(tenant_a, ["TEACHER"]), json={"roles": []}).status_code == 403
    assert client.put(url, json={"roles": []}).status_code == 401
    assert run(_scalar("SELECT roles FROM memberships WHERE id = :m", m=mine)) == ["LEARNER"]
