"""Database service layer for F01 Groups, Classes & Soft-Delete Roster."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.classes.internal.models import ClassMember, Group, SchoolClass
from app.modules.classes.internal.roster import (
    ClassMemberState,
    ClassMemberStatus,
    ClassRole,
    ClassroomPolicyError,
    filter_active_assignment_recipients,
    rejoin_class,
    soft_leave_class,
)


class ClassValidationError(ValueError):
    """Raised when group/class/member inputs violate F01 validation rules."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ClassNotFoundError(LookupError):
    """Raised when a group, class, or class member cannot be found in tenant scope."""


class ClassConflictError(RuntimeError):
    """Raised when invite codes or unique constraints conflict."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GroupView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClassView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    group_id: uuid.UUID | None
    teacher_membership_id: uuid.UUID
    name: str
    academic_year: str | None
    invite_code: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClassSummaryView:
    """A class plus its count of ACTIVE students, for list screens."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    group_id: uuid.UUID | None
    teacher_membership_id: uuid.UUID
    name: str
    academic_year: str | None
    invite_code: str | None
    status: str
    created_at: datetime
    active_student_count: int


@dataclass(frozen=True, slots=True)
class ClassMemberView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    class_id: uuid.UUID
    membership_id: uuid.UUID
    role_in_class: str
    status: str
    joined_at: datetime
    left_at: datetime | None


def _clean_name(value: str, field: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ClassValidationError("EMPTY_NAME", f"{field} must not be blank")
    if len(cleaned) > 255:
        raise ClassValidationError("NAME_TOO_LONG", f"{field} exceeds 255 characters")
    return cleaned


def _normalize_role(role: ClassRole | str) -> ClassRole:
    if isinstance(role, ClassRole):
        return role
    try:
        return ClassRole(role.strip().upper())
    except ValueError as exc:
        raise ClassValidationError("INVALID_CLASS_ROLE", f"unsupported class role: {role!r}") from exc


def _group_view(row: Group) -> GroupView:
    return GroupView(id=row.id, tenant_id=row.tenant_id, name=row.name, created_at=row.created_at)


def _class_view(row: SchoolClass) -> ClassView:
    return ClassView(
        id=row.id,
        tenant_id=row.tenant_id,
        group_id=row.group_id,
        teacher_membership_id=row.teacher_membership_id,
        name=row.name,
        academic_year=row.academic_year,
        invite_code=row.invite_code,
        status=row.status,
        created_at=row.created_at,
    )


def _member_view(row: ClassMember) -> ClassMemberView:
    return ClassMemberView(
        id=row.id,
        tenant_id=row.tenant_id,
        class_id=row.class_id,
        membership_id=row.membership_id,
        role_in_class=row.role_in_class,
        status=row.status,
        joined_at=row.joined_at,
        left_at=row.left_at,
    )


def to_class_member_state(view: ClassMemberView) -> ClassMemberState:
    """Convert a persisted `ClassMemberView` into the pure-core `ClassMemberState`."""
    return ClassMemberState(
        class_id=str(view.class_id),
        tenant_id=str(view.tenant_id),
        membership_id=str(view.membership_id),
        role_in_class=ClassRole(view.role_in_class),
        status=ClassMemberStatus(view.status),
        left_at=view.left_at,
    )


async def _membership_exists_in_tenant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM memberships WHERE tenant_id = :tid AND id = :mid"),
            {"tid": tenant_id, "mid": membership_id},
        )
    ).first()
    return row is not None


async def create_group(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
) -> GroupView:
    """Create a named group/cohort inside the tenant."""
    cleaned = _clean_name(name, "group.name")
    row = Group(tenant_id=tenant_id, name=cleaned)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _group_view(row)


async def list_groups(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> tuple[GroupView, ...]:
    """List groups visible to the current tenant."""
    rows = (
        await session.execute(
            select(Group).where(Group.tenant_id == tenant_id).order_by(Group.created_at.asc(), Group.id.asc())
        )
    ).scalars().all()
    return tuple(_group_view(r) for r in rows)


async def create_class(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    teacher_membership_id: uuid.UUID,
    name: str,
    group_id: uuid.UUID | None = None,
    academic_year: str | None = None,
    invite_code: str | None = None,
) -> ClassView:
    """Create a classroom after verifying teacher membership (and optional group) in tenant scope."""
    cleaned_name = _clean_name(name, "class.name")
    cleaned_year = academic_year.strip() if academic_year and academic_year.strip() else None
    cleaned_code = invite_code.strip() if invite_code and invite_code.strip() else None
    if cleaned_year is not None and len(cleaned_year) > 30:
        raise ClassValidationError("INVALID_ACADEMIC_YEAR", "academic_year exceeds 30 characters")
    if cleaned_code is not None and len(cleaned_code) > 30:
        raise ClassValidationError("INVALID_INVITE_CODE", "invite_code exceeds 30 characters")

    if not await _membership_exists_in_tenant(session, tenant_id=tenant_id, membership_id=teacher_membership_id):
        raise ClassNotFoundError(f"teacher membership {teacher_membership_id} not found in tenant")

    if group_id is not None:
        grp = (
            await session.execute(select(Group.id).where(Group.tenant_id == tenant_id, Group.id == group_id))
        ).scalar_one_or_none()
        if grp is None:
            raise ClassNotFoundError(f"group {group_id} not found in tenant")

    row = SchoolClass(
        tenant_id=tenant_id,
        group_id=group_id,
        teacher_membership_id=teacher_membership_id,
        name=cleaned_name,
        academic_year=cleaned_year,
        invite_code=cleaned_code,
        status="ACTIVE",
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ClassConflictError("INVITE_CODE_CONFLICT", "invite_code is already in use") from exc
    await session.refresh(row)
    return _class_view(row)


async def get_class(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
) -> ClassView:
    """Fetch one class scoped to the current tenant."""
    row = (
        await session.execute(
            select(SchoolClass).where(SchoolClass.tenant_id == tenant_id, SchoolClass.id == class_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ClassNotFoundError(f"class {class_id} not found in tenant")
    return _class_view(row)


async def add_class_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    membership_id: uuid.UUID,
    role_in_class: ClassRole | str = ClassRole.STUDENT,
) -> ClassMemberView:
    """Enroll a tenant membership into a class or rejoin a previously INACTIVE member."""
    await get_class(session, tenant_id=tenant_id, class_id=class_id)
    role = _normalize_role(role_in_class)
    if not await _membership_exists_in_tenant(session, tenant_id=tenant_id, membership_id=membership_id):
        raise ClassNotFoundError(f"membership {membership_id} not found in tenant")

    existing = (
        await session.execute(
            select(ClassMember).where(
                ClassMember.tenant_id == tenant_id,
                ClassMember.class_id == class_id,
                ClassMember.membership_id == membership_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        state = ClassMemberState(
            class_id=str(existing.class_id),
            tenant_id=str(existing.tenant_id),
            membership_id=str(existing.membership_id),
            role_in_class=ClassRole(existing.role_in_class),
            status=ClassMemberStatus(existing.status),
            left_at=existing.left_at,
        )
        restored = rejoin_class(state)
        existing.status = restored.status.value
        existing.left_at = restored.left_at
        existing.role_in_class = role.value
        await session.flush()
        await session.refresh(existing)
        return _member_view(existing)

    row = ClassMember(
        tenant_id=tenant_id,
        class_id=class_id,
        membership_id=membership_id,
        role_in_class=role.value,
        status=ClassMemberStatus.ACTIVE.value,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _member_view(row)


async def remove_class_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    member_or_membership_id: uuid.UUID,
    left_at: datetime | None = None,
) -> ClassMemberView:
    """
    Soft-delete a class member (`status='INACTIVE'`, `left_at=NOW()`) so historical
    attempts and grades remain queryable. Never physically deletes the `class_members` row.
    """
    await get_class(session, tenant_id=tenant_id, class_id=class_id)
    target = (
        await session.execute(
            select(ClassMember).where(
                ClassMember.tenant_id == tenant_id,
                ClassMember.class_id == class_id,
                or_(
                    ClassMember.id == member_or_membership_id,
                    ClassMember.membership_id == member_or_membership_id,
                ),
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise ClassNotFoundError(f"member {member_or_membership_id} not found in class {class_id}")

    stamp = datetime.now(UTC) if left_at is None else left_at
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ClassroomPolicyError("NAIVE_TIMESTAMP", "left_at must be a timezone-aware datetime")

    current_state = ClassMemberState(
        class_id=str(target.class_id),
        tenant_id=str(target.tenant_id),
        membership_id=str(target.membership_id),
        role_in_class=ClassRole(target.role_in_class),
        status=ClassMemberStatus(target.status),
        left_at=target.left_at,
    )
    updated_state = soft_leave_class(current_state, left_at=stamp)
    target.status = updated_state.status.value
    target.left_at = updated_state.left_at
    await session.flush()
    await session.refresh(target)
    return _member_view(target)


async def get_class_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> ClassMemberView | None:
    """Return one class member row (whether ACTIVE or INACTIVE), or `None` if never enrolled."""
    row = (
        await session.execute(
            select(ClassMember).where(
                ClassMember.tenant_id == tenant_id,
                ClassMember.class_id == class_id,
                ClassMember.membership_id == membership_id,
            )
        )
    ).scalar_one_or_none()
    return None if row is None else _member_view(row)


async def get_class_roster(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    include_inactive: bool = True,
) -> tuple[ClassMemberView, ...]:
    """List roster entries for a class inside the tenant."""
    await get_class(session, tenant_id=tenant_id, class_id=class_id)
    stmt = select(ClassMember).where(ClassMember.tenant_id == tenant_id, ClassMember.class_id == class_id)
    if not include_inactive:
        stmt = stmt.where(ClassMember.status == ClassMemberStatus.ACTIVE.value)
    rows = (await session.execute(stmt.order_by(ClassMember.joined_at.asc(), ClassMember.id.asc()))).scalars().all()
    return tuple(_member_view(r) for r in rows)


async def get_active_class_recipients(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
) -> tuple[ClassMemberState, ...]:
    """Return only ACTIVE STUDENT members in the class who should receive new assignments."""
    roster = await get_class_roster(session, tenant_id=tenant_id, class_id=class_id, include_inactive=True)
    states = tuple(to_class_member_state(m) for m in roster)
    return filter_active_assignment_recipients(states, tenant_id=str(tenant_id), class_id=str(class_id))


async def list_classes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> tuple[ClassSummaryView, ...]:
    """Classes of the tenant, newest first.

    With `membership_id`, only the classes that membership teaches or is an ACTIVE member of (a learner sees their
    own classes, a teacher theirs); without it, every class of the tenant (tenant administrators).
    """
    student_count = (
        select(func.count())
        .select_from(ClassMember)
        .where(
            ClassMember.tenant_id == SchoolClass.tenant_id,
            ClassMember.class_id == SchoolClass.id,
            ClassMember.status == ClassMemberStatus.ACTIVE.value,
            ClassMember.role_in_class == ClassRole.STUDENT.value,
        )
        .correlate(SchoolClass)
        .scalar_subquery()
    )
    stmt = select(SchoolClass, student_count.label("active_student_count")).where(SchoolClass.tenant_id == tenant_id)
    if not include_archived:
        stmt = stmt.where(SchoolClass.status == "ACTIVE")
    if membership_id is not None:
        member_of = (
            select(ClassMember.id)
            .where(
                ClassMember.tenant_id == SchoolClass.tenant_id,
                ClassMember.class_id == SchoolClass.id,
                ClassMember.membership_id == membership_id,
                ClassMember.status == ClassMemberStatus.ACTIVE.value,
            )
            .correlate(SchoolClass)
            .exists()
        )
        stmt = stmt.where(or_(SchoolClass.teacher_membership_id == membership_id, member_of))
    rows = (await session.execute(stmt.order_by(SchoolClass.created_at.desc(), SchoolClass.id.desc()))).all()
    return tuple(
        ClassSummaryView(
            id=row.id,
            tenant_id=row.tenant_id,
            group_id=row.group_id,
            teacher_membership_id=row.teacher_membership_id,
            name=row.name,
            academic_year=row.academic_year,
            invite_code=row.invite_code,
            status=row.status,
            created_at=row.created_at,
            active_student_count=int(count or 0),
        )
        for row, count in rows
    )


async def is_class_participant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    class_id: uuid.UUID,
    membership_id: uuid.UUID,
) -> bool:
    """True when the membership teaches the class or is an ACTIVE member of it (a left member is not a participant)."""
    teaches = (
        select(SchoolClass.id)
        .where(
            SchoolClass.tenant_id == tenant_id,
            SchoolClass.id == class_id,
            SchoolClass.teacher_membership_id == membership_id,
        )
        .exists()
    )
    member = (
        select(ClassMember.id)
        .where(
            ClassMember.tenant_id == tenant_id,
            ClassMember.class_id == class_id,
            ClassMember.membership_id == membership_id,
            ClassMember.status == ClassMemberStatus.ACTIVE.value,
        )
        .exists()
    )
    return bool(await session.scalar(select(or_(teaches, member))))
