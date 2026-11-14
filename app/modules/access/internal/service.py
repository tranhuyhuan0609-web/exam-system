"""F05 access service: tenant roles and their permissions, resolved under the tenant's RLS.

Every function runs in a transaction bound to the caller's tenant (`tenant_transaction`), so a role
or grant of another tenant is invisible here: lookups by id return nothing instead of leaking that
the row exists (cross-tenant IDOR). Decisions themselves are made by `internal.evaluator`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.internal.evaluator import (
    AccessPrincipal,
    EntitlementTier,
    MembershipStatus,
)
from app.modules.access.internal.models import Permission, Role, RolePermission

ROLE_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{1,59}$"


class RoleConflictError(ValueError):
    """A role with this code already exists in the tenant."""


class UnknownPermissionError(ValueError):
    """One or more permission codes are not in the catalog."""

    def __init__(self, codes: Iterable[str]) -> None:
        self.codes = tuple(sorted(codes))
        super().__init__(f"unknown permission codes: {', '.join(self.codes)}")


class UnknownRoleError(ValueError):
    """One or more role codes are not defined in the tenant."""

    def __init__(self, codes: Iterable[str]) -> None:
        self.codes = tuple(sorted(codes))
        super().__init__(f"unknown role codes: {', '.join(self.codes)}")


@dataclass(frozen=True, slots=True)
class RoleRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    code: str
    name: str
    permission_codes: tuple[str, ...]
    created_at: datetime


def normalize_role_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """Upper-cased, de-duplicated, sorted role codes; raises ValueError for a malformed code."""
    out = sorted({c.strip().upper() for c in codes if c and c.strip()})
    bad = [c for c in out if not re.fullmatch(ROLE_CODE_PATTERN, c)]
    if bad:
        raise ValueError(f"role codes must match {ROLE_CODE_PATTERN}: {', '.join(bad)}")
    return tuple(out)


async def permission_catalog(session: AsyncSession) -> dict[str, str]:
    rows = await session.execute(select(Permission.code, Permission.description).order_by(Permission.code))
    return {code: description for code, description in rows}


async def create_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    name: str,
    permission_codes: Iterable[str] = (),
) -> RoleRecord:
    """Create a role of `tenant_id` with its permissions (all or nothing)."""
    (normalized,) = normalize_role_codes([code]) or ("",)
    if not normalized:
        raise ValueError("role code is required")
    wanted = sorted(set(permission_codes))
    known = set(await permission_catalog(session))
    if missing := set(wanted) - known:
        raise UnknownPermissionError(missing)
    try:
        async with session.begin_nested():
            role = (
                await session.execute(
                    insert(Role).values(tenant_id=tenant_id, code=normalized, name=name.strip()).returning(Role)
                )
            ).scalar_one()
            if wanted:
                await session.execute(
                    insert(RolePermission),
                    [{"role_id": role.id, "tenant_id": tenant_id, "permission_code": p} for p in wanted],
                )
    except IntegrityError as exc:
        if "uq_roles_tenant_id_code" in str(exc.orig):
            raise RoleConflictError(f"role {normalized} already exists") from exc
        raise
    return RoleRecord(role.id, role.tenant_id, role.code, role.name, tuple(wanted), role.created_at)


async def list_roles(session: AsyncSession) -> list[RoleRecord]:
    """Roles of the transaction's tenant with their permission codes."""
    roles = (await session.scalars(select(Role).order_by(Role.code))).all()
    grants = await session.execute(select(RolePermission.role_id, RolePermission.permission_code))
    by_role: dict[uuid.UUID, list[str]] = {}
    for role_id, permission in grants:
        by_role.setdefault(role_id, []).append(permission)
    return [
        RoleRecord(r.id, r.tenant_id, r.code, r.name, tuple(sorted(by_role.get(r.id, []))), r.created_at) for r in roles
    ]


async def existing_role_codes(session: AsyncSession, codes: Iterable[str]) -> frozenset[str]:
    """The subset of `codes` defined as roles in the transaction's tenant."""
    wanted = list(set(codes))
    if not wanted:
        return frozenset()
    return frozenset(await session.scalars(select(Role.code).where(Role.code.in_(wanted))))


async def require_role_codes(session: AsyncSession, codes: Iterable[str]) -> tuple[str, ...]:
    """Normalize `codes` and check that each one is a role of the tenant; raises UnknownRoleError."""
    normalized = normalize_role_codes(codes)
    missing = set(normalized) - await existing_role_codes(session, normalized)
    if missing:
        raise UnknownRoleError(missing)
    return normalized


async def permissions_for_roles(session: AsyncSession, role_codes: Iterable[str]) -> frozenset[str]:
    """Union of the permissions granted to `role_codes` in the transaction's tenant.

    A code that is not a role of this tenant grants nothing, even if another tenant defines it.
    """
    codes = list({c.strip().upper() for c in role_codes if c and c.strip()})
    if not codes:
        return frozenset()
    rows = await session.scalars(
        select(RolePermission.permission_code)
        .join(Role, (Role.id == RolePermission.role_id) & (Role.tenant_id == RolePermission.tenant_id))
        .where(Role.code.in_(codes))
        .distinct()
    )
    return frozenset(rows)


def principal_for(
    *,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    user_id: uuid.UUID,
    roles: Iterable[str],
    permissions: Iterable[str],
    entitlement_tier: str,
    membership_status: str = MembershipStatus.ACTIVE,
    is_under_16: bool = False,
    has_parental_consent: bool = False,
) -> AccessPrincipal:
    """The evaluator's principal; an unknown tier or status fails closed (FREE / SUSPENDED)."""
    try:
        tier = EntitlementTier(entitlement_tier)
    except ValueError:
        tier = EntitlementTier.FREE
    try:
        status = MembershipStatus(membership_status)
    except ValueError:
        status = MembershipStatus.SUSPENDED
    return AccessPrincipal(
        tenant_id=str(tenant_id),
        membership_id=str(membership_id),
        user_id=str(user_id),
        membership_status=status,
        roles=frozenset(r.upper() for r in roles),
        permissions=frozenset(permissions),
        entitlement_tier=tier,
        is_under_16=is_under_16,
        has_parental_consent=has_parental_consent,
    )
