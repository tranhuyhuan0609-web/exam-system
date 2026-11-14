"""SQLAlchemy ORM models for F04 privacy (spec chapter 4, identity group).

`users` holds no plaintext PII: `email_blind_index` (HMAC-SHA256) for login lookup and
`pii_ciphertext_bytea` (AES-256-GCM under the user's own DEK). The DEK is stored only wrapped by a KEK
that lives outside PostgreSQL (`user_pii_encryption_keys`); destroying the KEK crypto-shreds the user.
`user_pii_encryption_keys` is not reachable by any runtime role: the F04 SECURITY DEFINER functions
are its only door (decision #7).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7

USER_STATUSES = ("ACTIVE", "LOCKED", "PENDING_PARENTAL_CONSENT", "ANONYMIZED")
MEMBERSHIP_STATUSES = ("ACTIVE", "INACTIVE", "SUSPENDED")
ENTITLEMENT_TIERS = ("FREE", "STUDENT_PRO", "TEACHER_PRO", "SCHOOL_ENTERPRISE")
GUARDIAN_RELATIONSHIPS = ("PARENT", "LEGAL_GUARDIAN", "SCHOOL_CONTRACT")
KEY_STATUSES = ("ACTIVE", "SHREDDED")


class User(Base):
    """A person, across tenants. Known to a tenant only through a membership (RLS on `users`)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1920 AND birth_year <= 2100)", name="ck_users_birth_year"
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'LOCKED', 'PENDING_PARENTAL_CONSENT', 'ANONYMIZED')", name="ck_users_status"
        ),
        UniqueConstraint("email_blind_index", name="uq_users_email_blind_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    email_blind_index: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pii_ciphertext_bytea: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pseudonym_handle: Mapped[str] = mapped_column(String(80), nullable=False, server_default="Learner")
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class UserPiiEncryptionKey(Base):
    """The user's DEK wrapped by a KEK on the KMS/Vault (envelope encryption, AUD-006)."""

    __tablename__ = "user_pii_encryption_keys"
    __table_args__ = (
        CheckConstraint("key_status IN ('ACTIVE', 'SHREDDED')", name="ck_user_pii_encryption_keys_key_status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kms_kek_key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_dek_bytea: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_algorithm: Mapped[str] = mapped_column(String(30), nullable=False, server_default="AES_256_GCM_ENVELOPE")
    key_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    shredded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backup_retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ErasureCertificate(Base):
    """Proof that a user's personal data was crypto-shredded (right to erasure)."""

    __tablename__ = "erasure_certificates"
    __table_args__ = (UniqueConstraint("id", "tenant_id", name="uq_erasure_certificates_id_tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kms_kek_key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_db_shredded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    kms_kek_revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    backup_expiry_guaranteed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verification_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class Membership(Base):
    """A user's place in one tenant: roles (RBAC, F05) and entitlement tier (commerce, G05) kept apart."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')", name="ck_memberships_status"),
        CheckConstraint(
            "entitlement_tier IN ('FREE', 'STUDENT_PRO', 'TEACHER_PRO', 'SCHOOL_ENTERPRISE')",
            name="ck_memberships_entitlement_tier",
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_id_user_id"),
        UniqueConstraint("id", "tenant_id", name="uq_memberships_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="ACTIVE")
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, server_default=text("'{LEARNER}'"))
    entitlement_tier: Mapped[str] = mapped_column(String(30), nullable=False, server_default="FREE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ParentalConsent(Base):
    """Consent of a parent, guardian or school contract for a learner under 16 (Law 91/2025/QH15, ND 13/2023)."""

    __tablename__ = "parental_consents"
    __table_args__ = (
        CheckConstraint(
            "guardian_relationship IN ('PARENT', 'LEGAL_GUARDIAN', 'SCHOOL_CONTRACT')",
            name="ck_parental_consents_guardian_relationship",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_parental_consents_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    learner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    guardian_email_blind_index: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    guardian_email_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    guardian_relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    consent_scope_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text('\'{"learning_history": true, "ai_tutor": true}\'')
    )
    verification_method: Mapped[str] = mapped_column(String(40), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
