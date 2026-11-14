"""F04 privacy service: tenants, memberships, parental consent and crypto-shredding erasure.

Every function runs inside a transaction bound to the caller's tenant (`tenant_transaction`) and, where
a person acts for themself, to the caller's user (`bind_user`). `users` is read under its own RLS (members
of the tenant and the signed-in user); `user_pii_encryption_keys` only through the F04 SECURITY DEFINER
functions created by the migration.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.privacy.internal.blind_index import compute_email_blind_index
from app.modules.privacy.internal.consent import (
    ConsentState,
    initial_user_status,
    is_user_minor,
    require_sensitive_profile_allowed,
    validate_consent_evidence,
)
from app.modules.privacy.internal.crypto_shredding import (
    BACKUP_RETENTION_DAYS,
    KeyEncryptionService,
    KeyShreddedError,
    decrypt_pii,
    encrypt_pii,
    encrypt_value,
    erasure_certificate,
    new_dek,
)
from app.modules.privacy.internal.models import ErasureCertificate, Membership, ParentalConsent, User
from app.platform.models import Tenant

GUARDIAN_EMAIL_PURPOSE = "parental_consents.guardian_email"
_INSUFFICIENT_PRIVILEGE = "42501"


class PrivacyNotFoundError(LookupError):
    """The row does not exist or belongs to another tenant (the two are indistinguishable on purpose)."""


class MembershipConflictError(ValueError):
    """The user already has a membership in this tenant."""


class TenantConflictError(ValueError):
    """A tenant with this slug already exists."""


class ErasureForbiddenError(PermissionError):
    """The tenant may not erase this user (not its member, or the user also belongs to another tenant)."""


@dataclass(frozen=True, slots=True)
class TenantRecord:
    id: uuid.UUID
    slug: str
    name: str
    status: str
    daily_ai_budget_usd: Decimal
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    roles: tuple[str, ...]
    entitlement_tier: str
    created_at: datetime
    left_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnrollmentResult:
    membership: MembershipRecord
    user_created: bool
    user_status: str
    is_minor: bool
    pii_stored: bool


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    learner_user_id: uuid.UUID
    guardian_relationship: str
    verification_method: str
    consent_scope_json: dict[str, Any]
    consented_at: datetime
    learner_status: str


@dataclass(frozen=True, slots=True)
class ErasureCertificateRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_user_id: uuid.UUID
    kms_kek_key_id: str
    primary_db_shredded_at: datetime
    kms_kek_revoked_at: datetime
    backup_expiry_guaranteed_at: datetime
    verification_hash: str


def _membership(row: Membership) -> MembershipRecord:
    return MembershipRecord(
        row.id, row.tenant_id, row.user_id, row.status, tuple(row.roles or ()), row.entitlement_tier,
        row.created_at, row.left_at,
    )  # fmt: skip


def _is_insufficient_privilege(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _INSUFFICIENT_PRIVILEGE or "42501" in str(exc.orig)


async def bind_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Bind the transaction to the signed-in user (read by current_app_user_id() in the users policy)."""
    await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(user_id)})


# --- tenants -------------------------------------------------------------------------------------


async def create_tenant(
    session: AsyncSession, *, slug: str, name: str, daily_ai_budget_usd: Decimal | None = None
) -> TenantRecord:
    values: dict[str, Any] = {"slug": slug, "name": name}
    if daily_ai_budget_usd is not None:
        values["daily_ai_budget_usd"] = daily_ai_budget_usd
    try:
        async with session.begin_nested():
            row = (await session.execute(insert(Tenant).values(**values).returning(Tenant))).scalar_one()
    except IntegrityError as exc:
        raise TenantConflictError(f"tenant slug {slug!r} is taken") from exc
    return TenantRecord(row.id, row.slug, row.name, row.status, row.daily_ai_budget_usd, row.created_at)


# --- memberships -----------------------------------------------------------------------------------


async def get_membership(session: AsyncSession, membership_id: uuid.UUID) -> MembershipRecord | None:
    """The membership if it belongs to the transaction's tenant (RLS), else None."""
    row = await session.scalar(
        select(Membership).where(Membership.id == membership_id).execution_options(populate_existing=True)
    )
    return None if row is None else _membership(row)


async def set_membership_roles(
    session: AsyncSession, membership_id: uuid.UUID, roles: Sequence[str]
) -> MembershipRecord | None:
    """Replace the roles of a membership of the transaction's tenant; None when there is none (or not ours)."""
    row = (
        await session.execute(
            update(Membership)
            .where(Membership.id == membership_id)
            .values(roles=list(roles))
            .returning(Membership)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return None if row is None else _membership(row)


async def enroll_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    pepper: bytes,
    keys: KeyEncryptionService,
    today: date,
    birth_year: int | None = None,
    display_name: str | None = None,
    phone: str | None = None,
    pseudonym_handle: str | None = None,
    roles: Iterable[str] | None = None,
    entitlement_tier: str = "FREE",
) -> EnrollmentResult:
    """Add a person to the tenant, creating the user (and their key) when the e-mail is new.

    The e-mail is never stored in clear: only its blind index, and inside the PII ciphertext. For a new
    learner under 16 no PII is stored at all until a parental consent is recorded.
    """
    blind = compute_email_blind_index(email, pepper)
    minor = is_user_minor(birth_year, today)
    status = initial_user_status(birth_year, today)
    registered = (
        await session.execute(
            text("SELECT user_id, created FROM f04_register_user(:b, NULL, NULL, :ps, :by, :st)"),
            {"b": blind, "ps": pseudonym_handle or "Learner", "by": birth_year, "st": status},
        )
    ).one()
    user_id, created = registered.user_id, registered.created

    values: dict[str, Any] = {"tenant_id": tenant_id, "user_id": user_id, "entitlement_tier": entitlement_tier}
    if roles is not None:
        values["roles"] = list(roles)
    try:
        async with session.begin_nested():
            membership = (await session.execute(insert(Membership).values(**values).returning(Membership))).scalar_one()
    except IntegrityError as exc:
        if "uq_memberships_tenant_id_user_id" in str(exc.orig):
            raise MembershipConflictError("this person is already a member of the tenant") from exc
        raise

    pii_stored = False
    if created:
        dek = new_dek()
        kek_id = keys.create_key(user_id)
        await session.execute(
            text("SELECT f04_store_user_key(:u, :k, :d)"), {"u": user_id, "k": kek_id, "d": keys.wrap(kek_id, dek)}
        )
        if not minor:
            pii = {"email": email, **({"display_name": display_name} if display_name else {}),
                   **({"phone": phone} if phone else {})}  # fmt: skip
            await session.execute(
                update(User).where(User.id == user_id).values(pii_ciphertext_bytea=encrypt_pii(dek, user_id, pii))
            )
            pii_stored = True
    user_status = await session.scalar(select(User.status).where(User.id == user_id))
    return EnrollmentResult(_membership(membership), created, user_status or status, minor, pii_stored)


# --- keys and profile ------------------------------------------------------------------------------


async def _load_dek(session: AsyncSession, user_id: uuid.UUID, keys: KeyEncryptionService) -> bytes:
    try:
        row = (await session.execute(text("SELECT * FROM f04_load_user_key(:u)"), {"u": user_id})).one_or_none()
    except DBAPIError as exc:
        if _is_insufficient_privilege(exc):
            raise PrivacyNotFoundError("user not found") from exc
        raise
    if row is None:
        raise PrivacyNotFoundError("user has no encryption key")
    if row.key_status != "ACTIVE" or row.encrypted_dek_bytea is None:
        raise KeyShreddedError("the user's personal data was erased")
    return keys.unwrap(row.kms_kek_key_id, row.encrypted_dek_bytea)


async def consent_state(session: AsyncSession, user_id: uuid.UUID, today: date) -> ConsentState:
    birth_year = await session.scalar(select(User.birth_year).where(User.id == user_id))
    active = await session.scalar(
        select(ParentalConsent.id)
        .where(ParentalConsent.learner_user_id == user_id, ParentalConsent.revoked_at.is_(None))
        .limit(1)
    )
    return ConsentState(is_minor=is_user_minor(birth_year, today), has_active_consent=active is not None)


async def store_sensitive_profile(
    session: AsyncSession,
    user_id: uuid.UUID,
    pii: Mapping[str, Any],
    *,
    keys: KeyEncryptionService,
    today: date,
) -> None:
    """Encrypt and store the user's PII; raises ParentalConsentRequiredError for a minor without consent."""
    if await session.scalar(select(User.id).where(User.id == user_id)) is None:
        raise PrivacyNotFoundError("user not found")
    require_sensitive_profile_allowed(await consent_state(session, user_id, today))
    dek = await _load_dek(session, user_id, keys)
    await session.execute(
        update(User).where(User.id == user_id).values(pii_ciphertext_bytea=encrypt_pii(dek, user_id, pii))
    )


async def read_profile(
    session: AsyncSession, user_id: uuid.UUID, *, keys: KeyEncryptionService
) -> dict[str, Any] | None:
    """The decrypted PII, None when none is stored; KeyShreddedError after erasure."""
    ciphertext = await session.scalar(select(User.pii_ciphertext_bytea).where(User.id == user_id))
    dek = await _load_dek(session, user_id, keys)
    return None if ciphertext is None else decrypt_pii(dek, user_id, ciphertext)


# --- parental consent ------------------------------------------------------------------------------


async def record_parental_consent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    learner_user_id: uuid.UUID,
    guardian_email: str,
    guardian_relationship: str,
    verification_method: str,
    pepper: bytes,
    keys: KeyEncryptionService,
    consent_scope_json: Mapping[str, Any] | None = None,
) -> ConsentRecord:
    """Record a consent for a learner of this tenant and lift PENDING_PARENTAL_CONSENT."""
    validate_consent_evidence(guardian_relationship, verification_method)
    learner = await session.scalar(select(User).where(User.id == learner_user_id))
    if learner is None:
        raise PrivacyNotFoundError("learner not found")
    dek = await _load_dek(session, learner_user_id, keys)
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "learner_user_id": learner_user_id,
        "guardian_email_blind_index": compute_email_blind_index(guardian_email, pepper),
        "guardian_email_ciphertext": encrypt_value(dek, learner_user_id, guardian_email, GUARDIAN_EMAIL_PURPOSE),
        "guardian_relationship": guardian_relationship,
        "verification_method": verification_method,
    }
    if consent_scope_json is not None:
        values["consent_scope_json"] = dict(consent_scope_json)
    consent = (await session.execute(insert(ParentalConsent).values(**values).returning(ParentalConsent))).scalar_one()
    status = learner.status
    if status == "PENDING_PARENTAL_CONSENT":
        await session.execute(update(User).where(User.id == learner_user_id).values(status="ACTIVE"))
        status = "ACTIVE"
    return ConsentRecord(
        consent.id, consent.tenant_id, consent.learner_user_id, consent.guardian_relationship,
        consent.verification_method, dict(consent.consent_scope_json), consent.consented_at, status,
    )  # fmt: skip


# --- right to erasure -----------------------------------------------------------------------------


async def erase_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subject_user_id: uuid.UUID,
    keys: KeyEncryptionService,
    backup_retention: timedelta = timedelta(days=BACKUP_RETENTION_DAYS),
) -> ErasureCertificateRecord:
    """Crypto-shred the subject (KEK destroyed, wrapped DEK and PII nulled, blind index randomised) and
    file the erasure certificate in this tenant."""
    try:
        shredded = (
            await session.execute(
                text("SELECT * FROM f04_shred_user_key(:u, :r)"), {"u": subject_user_id, "r": backup_retention}
            )
        ).one_or_none()
    except DBAPIError as exc:
        if _is_insufficient_privilege(exc):
            raise ErasureForbiddenError(str(exc.orig).splitlines()[0]) from exc
        raise
    if shredded is None:
        raise PrivacyNotFoundError("user has no encryption key")
    keys.destroy(shredded.kms_kek_key_id)

    left = (
        await session.execute(select(User.pii_ciphertext_bytea, User.password_hash).where(User.id == subject_user_id))
    ).one_or_none()
    remaining = () if left is None else tuple(
        name for name, value in (("pii_ciphertext_bytea", left[0]), ("password_hash", left[1])) if value is not None
    )  # fmt: skip
    result = erasure_certificate(
        tenant_id=tenant_id,
        subject_user_id=subject_user_id,
        key_destroyed=True,
        remaining_plaintext_pii_fields=remaining,
    )
    if not result.compliant:
        raise RuntimeError(f"erasure incomplete: {', '.join(result.blocking_reasons)}")
    certificate = (
        await session.execute(
            insert(ErasureCertificate)
            .values(
                tenant_id=tenant_id,
                subject_user_id=subject_user_id,
                kms_kek_key_id=shredded.kms_kek_key_id,
                primary_db_shredded_at=shredded.shredded_at,
                backup_expiry_guaranteed_at=shredded.backup_retention_expires_at,
                verification_hash=result.certificate_sha256,
            )
            .returning(ErasureCertificate)
        )
    ).scalar_one()
    return ErasureCertificateRecord(
        certificate.id, certificate.tenant_id, certificate.subject_user_id, certificate.kms_kek_key_id,
        certificate.primary_db_shredded_at, certificate.kms_kek_revoked_at,
        certificate.backup_expiry_guaranteed_at, certificate.verification_hash,
    )  # fmt: skip
