"""SQLAlchemy ORM models for F05 access (spec chapter 4: `roles`, `permissions`, `role_permissions`).

`roles` and `role_permissions` are tenant-scoped (strict RLS); `permissions` is the global catalog of
permission codes (app.platform.rls.GLOBAL_TABLES), seeded by migrations.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class Role(Base):
    """A tenant-defined role; `code` is what memberships.roles and the JWT `roles` claim carry."""

    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_roles_tenant_id_code"),
        UniqueConstraint("id", "tenant_id", name="uq_roles_id_tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class Permission(Base):
    """A business permission such as `question:approve` or `exam:publish` (RBAC, never an entitlement)."""

    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)


class RolePermission(Base):
    """Grant of one permission to one role; the composite FK keeps both in the same tenant."""

    __tablename__ = "role_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            ondelete="CASCADE",
            name="fk_role_permissions_role_tenant",
        ),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    permission_code: Mapped[str] = mapped_column(
        String(80), ForeignKey("permissions.code", ondelete="CASCADE"), primary_key=True
    )
