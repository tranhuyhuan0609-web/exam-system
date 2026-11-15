"""F04 login: e-mail + password to a tenant membership, for the JWT the API trusts.

The e-mail is never compared in clear: `f04_find_user_id` (SECURITY DEFINER) maps its blind index to a user id, the
user is then read under their own row policy (`bind_user`). `memberships` is strict tenant RLS, so a person's
memberships are looked up one tenant at a time, never across tenants at once, and the tenants are the ACTIVE ones
(optionally narrowed by slug). A wrong e-mail, a wrong password and a user that is not ACTIVE look the same.
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
from app.platform.tenancy import DbRole, set_role, tenant_transaction
from app.security import DUMMY_HASH, verify_password

MAX_TENANTS_PER_LOGIN = 200


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
        tenants = (
            await session.execute(
                text(
                    "SELECT id, slug FROM tenants "
                    "WHERE status = 'ACTIVE' AND (CAST(:slug AS text) IS NULL OR slug = :slug) "
                    "ORDER BY created_at, id LIMIT :n"
                ),
                {"slug": tenant_slug, "n": MAX_TENANTS_PER_LOGIN},
            )
        ).all()
    for tenant in tenants:
        async with tenant_transaction(tenant.id, role=DbRole.APP_RW) as session:
            membership = (
                await session.execute(
                    text(
                        "SELECT id, roles, entitlement_tier FROM memberships "
                        "WHERE user_id = :u AND status = 'ACTIVE' AND left_at IS NULL"
                    ),
                    {"u": user_id},
                )
            ).one_or_none()
        if membership is not None:
            return LoginPrincipal(
                user_id=user_id,
                tenant_id=tenant.id,
                tenant_slug=tenant.slug,
                membership_id=membership.id,
                roles=tuple(membership.roles),
                entitlement_tier=membership.entitlement_tier,
            )
    return None
