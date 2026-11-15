"""Role sets shared by the routers.

Platform tables without a tenant (assessment frameworks, IRT calibrations, vertical packs, locale bundles) are the same
for every tenant, so only the platform's own administrators may write them. A tenant administrator or a teacher is
powerful inside one tenant, never here.
"""

from __future__ import annotations

from collections.abc import Iterable

PLATFORM_ADMIN_ROLES = frozenset({"PLATFORM_ADMIN", "SUPER_ADMIN"})


def is_platform_admin(roles: Iterable[str]) -> bool:
    """True when one of `roles` is a platform administrator role (case-insensitive)."""
    return any(role.upper() in PLATFORM_ADMIN_ROLES for role in roles)
