"""F04 login: e-mail + password to a tenant membership, for the JWT the API trusts.

The e-mail is never compared in clear: `f04_find_user_id` (SECURITY DEFINER) maps its blind index to a user id, the
user is then read under their own row policy (`bind_user`). `memberships` is strict tenant RLS, so the membership is
read through the SECURITY DEFINER function `f04_login_memberships`: one query, the first ACTIVE membership in an
ACTIVE tenant (optionally narrowed by slug). A wrong e-mail, a wrong password and a user that is not ACTIVE look
the same.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.modules.privacy.internal.blind_index import compute_email_blind_index
from app.modules.privacy.internal.crypto_shredding import blind_index_pepper
from app.platform.tenancy import DbRole, set_role
from app.security import DUMMY_HASH, verify_password


@dataclass(frozen=True, slots=True)
class LoginPrincipal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str
    membership_id: uuid.UUID
    roles: tuple[str, ...]
    entitlement_tier: str


async def _verified_user_id(email: str, password: str) -> uuid.UUID | None:
    settings = get_settings()
    pepper = blind_index_pepper(settings.secret_key, settings.blind_index_pepper)
    blind = compute_email_blind_index(email, pepper)
    async with SessionLocal() as session, session.begin():
        await set_role(session, DbRole.APP_RW)
        user_id = (await session.execute(text("SELECT f04_find_user_id(:b)"), {"b": blind})).scalar()
        row = None
        if user_id is not None:
            await session.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(user_id)})
            row = (
                await session.execute(text("SELECT password_hash, status FROM users WHERE id = :u"), {"u": user_id})
            ).one_or_none()
    stored = row.password_hash if row is not None and row.password_hash else DUMMY_HASH
    matches = await asyncio.to_thread(verify_password, password, stored)  # bcrypt is slow: off the event loop
    if row is None or not row.password_hash or not matches or row.status != "ACTIVE":
        return None
    return uuid.UUID(str(user_id))


async def authenticate_login(email: str, password: str, tenant_slug: str | None = None) -> LoginPrincipal | None:
    """The principal to sign a token for, or None when the credentials or the membership do not check out."""
    user_id = await _verified_user_id(email, password)
    if user_id is None:
        return None
    async with SessionLocal() as session, session.begin():
        await set_role(session, DbRole.APP_RW)
        row = (
            await session.execute(
                text(
                    "SELECT r_tenant_id, r_tenant_slug, r_membership_id, r_roles, r_entitlement_tier "
                    "FROM f04_login_memberships(:u, CAST(:slug AS varchar))"
                ),
                {"u": user_id, "slug": tenant_slug},
            )
        ).one_or_none()
    if row is None:
        return None
    return LoginPrincipal(
        user_id=user_id,
        tenant_id=row.r_tenant_id,
        tenant_slug=row.r_tenant_slug,
        membership_id=row.r_membership_id,
        roles=tuple(row.r_roles),
        entitlement_tier=row.r_entitlement_tier,
    )
