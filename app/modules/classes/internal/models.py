"""SQLAlchemy ORM models for F01 Groups, Classes & Class Members Roster (spec chapter 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.platform.models import NOW, UUID_V7


class Group(Base):
    """Named cohort/group within a tenant (parent of one or more classes)."""

    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_groups_id_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class SchoolClass(Base):
    """Classroom owned by a teacher membership inside a tenant."""

    __tablename__ = "classes"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_classes_status"),
        UniqueConstraint("id", "tenant_id", name="uq_classes_id_tenant"),
        ForeignKeyConstraint(
            ["group_id", "tenant_id"],
            ["groups.id", "groups.tenant_id"],
            ondelete="SET NULL",
            name="fk_classes_group_tenant",
        ),
        ForeignKeyConstraint(
            ["teacher_membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_classes_teacher_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    teacher_membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    academic_year: Mapped[str | None] = mapped_column(String(30), nullable=True)
    invite_code: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'ACTIVE'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


class ClassMember(Base):
    """Class roster row using INACTIVE soft-delete so historical attempts/grades stay intact."""

    __tablename__ = "class_members"
    __table_args__ = (
        CheckConstraint(
            "role_in_class IN ('STUDENT', 'TEACHING_ASSISTANT')",
            name="ck_class_members_role_in_class",
        ),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_class_members_status"),
        UniqueConstraint("class_id", "membership_id", name="uq_class_members_class_membership"),
        UniqueConstraint("id", "tenant_id", name="uq_class_members_id_tenant"),
        ForeignKeyConstraint(
            ["class_id", "tenant_id"],
            ["classes.id", "classes.tenant_id"],
            ondelete="CASCADE",
            name="fk_class_members_class_tenant",
        ),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["memberships.id", "memberships.tenant_id"],
            ondelete="RESTRICT",
            name="fk_class_members_membership_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID_V7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role_in_class: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'STUDENT'"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ACTIVE'"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
