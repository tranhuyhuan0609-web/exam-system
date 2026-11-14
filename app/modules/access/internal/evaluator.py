"""Pure RBAC/ABAC, cross-tenant isolation, entitlement tier, and under-16 consent policy evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MembershipStatus(StrEnum):
    """Exact DDL CHECK values for `memberships.status`."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"


class EntitlementTier(StrEnum):
    """Exact DDL CHECK values for `memberships.entitlement_tier`."""

    FREE = "FREE"
    STUDENT_PRO = "STUDENT_PRO"
    TEACHER_PRO = "TEACHER_PRO"
    SCHOOL_ENTERPRISE = "SCHOOL_ENTERPRISE"


class VisibilityScope(StrEnum):
    """Exact DDL CHECK values for `questions.visibility_scope` / `content_items.visibility_scope`."""

    SYSTEM_PUBLIC = "SYSTEM_PUBLIC"
    TENANT_PRIVATE = "TENANT_PRIVATE"
    SHARED_PARTNER = "SHARED_PARTNER"


_TIER_RANK: dict[EntitlementTier, int] = {
    EntitlementTier.FREE: 0,
    EntitlementTier.STUDENT_PRO: 1,
    EntitlementTier.TEACHER_PRO: 2,
    EntitlementTier.SCHOOL_ENTERPRISE: 3,
}


class AccessControlError(PermissionError):
    """Raised when an authorization check fails with a deterministic reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AccessPrincipal:
    """Verified actor context extracted from signed JWT claims + active membership."""

    tenant_id: str
    membership_id: str
    user_id: str
    membership_status: MembershipStatus
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    entitlement_tier: EntitlementTier = EntitlementTier.FREE
    is_under_16: bool = False
    has_parental_consent: bool = False  # fail closed: consent must be asserted explicitly
    is_super_admin: bool = False


@dataclass(frozen=True, slots=True)
class ResourceContext:
    """Target resource ownership & policy metadata."""

    tenant_id: str
    visibility_scope: VisibilityScope = VisibilityScope.TENANT_PRIVATE
    required_tier: EntitlementTier = EntitlementTier.FREE
    partner_shared_tenant_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class AssetAccessDecision:
    """B03/F05 asset download decision distinguishing full access vs 2-page preview."""

    allowed: bool
    access_mode: str  # FULL_ASSET | PREVIEW_PDF | DENIED
    reason_code: str


def entitlement_satisfies(current_tier: EntitlementTier, required_tier: EntitlementTier) -> bool:
    """Check monotonic plan hierarchy (`FREE < STUDENT_PRO < TEACHER_PRO < SCHOOL_ENTERPRISE`)."""
    current_rank = _TIER_RANK.get(current_tier)
    required_rank = _TIER_RANK.get(required_tier)
    if current_rank is None or required_rank is None:
        return False  # unknown tier names never satisfy anything
    return current_rank >= required_rank


def _higher_tier(first: EntitlementTier, second: EntitlementTier | None) -> EntitlementTier:
    """Return the stricter of two required tiers; an unknown tier is treated as the strictest."""
    if second is None:
        return first
    first_rank = _TIER_RANK.get(first)
    second_rank = _TIER_RANK.get(second)
    if first_rank is None:
        return first
    if second_rank is None or second_rank > first_rank:
        return second
    return first


def _has_tenant(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def assert_active_membership(principal: AccessPrincipal) -> None:
    """Fail closed if membership is INACTIVE or SUSPENDED."""
    if principal.membership_status != MembershipStatus.ACTIVE:
        raise AccessControlError(
            "MEMBERSHIP_NOT_ACTIVE",
            f"Membership status {str(principal.membership_status)[:32]!r} cannot perform active operations",
        )


def assert_tenant_scope(
    principal: AccessPrincipal,
    resource: ResourceContext,
    *,
    is_write: bool,
) -> None:
    """Enforce strict multi-tenant isolation against Cross-Tenant IDOR."""
    if not _has_tenant(principal.tenant_id) or not _has_tenant(resource.tenant_id):
        raise AccessControlError("TENANT_ID_REQUIRED", "Principal and resource must both carry a tenant id")
    if principal.tenant_id == resource.tenant_id:
        return

    if is_write:
        raise AccessControlError(
            "CROSS_TENANT_FORBIDDEN",
            "Cross-tenant write operation is strictly forbidden",
        )

    if resource.visibility_scope == VisibilityScope.SYSTEM_PUBLIC:
        return

    if (
        resource.visibility_scope == VisibilityScope.SHARED_PARTNER
        and principal.tenant_id in resource.partner_shared_tenant_ids
    ):
        return

    raise AccessControlError(
        "CROSS_TENANT_FORBIDDEN",
        "Cross-tenant resource access denied",
    )


def _assert_permission(principal: AccessPrincipal, permission_code: str) -> None:
    if not isinstance(permission_code, str) or not permission_code.strip():
        raise AccessControlError("PERMISSION_DENIED", "A non-empty permission code is required")
    if not principal.is_super_admin and permission_code not in principal.permissions:
        raise AccessControlError(
            "PERMISSION_DENIED",
            f"Missing RBAC permission {permission_code!r}",
        )


def authorize_action(
    principal: AccessPrincipal,
    *,
    permission_code: str,
    resource: ResourceContext,
    is_write: bool = False,
    required_entitlement: EntitlementTier | None = None,
    requires_sensitive_learner_profile: bool = False,
) -> None:
    """
    Evaluate F05 RBAC/ABAC independently from EntitlementTier, tenant scope, and K-12 consent.

    Paying for a higher `entitlement_tier` never implicitly grants RBAC permissions,
    and having an RBAC role never bypasses tenant isolation or commercial tier gates.
    """
    assert_active_membership(principal)
    assert_tenant_scope(principal, resource, is_write=is_write)

    if requires_sensitive_learner_profile and principal.is_under_16 and not principal.has_parental_consent:
        raise AccessControlError(
            "PARENTAL_CONSENT_REQUIRED",
            "Learners under 16 require verified parental or school consent before profile processing",
        )

    _assert_permission(principal, permission_code)

    effective_tier = _higher_tier(resource.required_tier, required_entitlement)
    if not entitlement_satisfies(principal.entitlement_tier, effective_tier):
        raise AccessControlError(
            "ENTITLEMENT_UPGRADE_REQUIRED",
            f"Requires tier >= {effective_tier!s}, current tier is {principal.entitlement_tier!s}",
        )


def evaluate_asset_access(
    principal: AccessPrincipal,
    resource: ResourceContext,
    *,
    permission_code: str,
    has_preview_pdf: bool,
) -> AssetAccessDecision:
    """
    Evaluate B03 + F05 media asset visibility.

    - Cross-tenant private access -> DENIED (`CROSS_TENANT_FORBIDDEN`)
    - Inactive membership -> DENIED (`MEMBERSHIP_NOT_ACTIVE`)
    - Missing RBAC permission (`permission_code`) -> DENIED (`PERMISSION_DENIED`)
    - Authorized tenant but insufficient `entitlement_tier`:
      - If `has_preview_pdf`: returns `PREVIEW_PDF` (e.g. 2-page sample for FREE users)
      - Else: `DENIED` (`ENTITLEMENT_UPGRADE_REQUIRED`)
    """
    try:
        assert_active_membership(principal)
        assert_tenant_scope(principal, resource, is_write=False)
        _assert_permission(principal, permission_code)
    except AccessControlError as exc:
        return AssetAccessDecision(allowed=False, access_mode="DENIED", reason_code=exc.code)

    if entitlement_satisfies(principal.entitlement_tier, resource.required_tier):
        return AssetAccessDecision(allowed=True, access_mode="FULL_ASSET", reason_code="OK")

    if has_preview_pdf:
        return AssetAccessDecision(
            allowed=True,
            access_mode="PREVIEW_PDF",
            reason_code="ENTITLEMENT_PREVIEW_ONLY",
        )

    return AssetAccessDecision(
        allowed=False,
        access_mode="DENIED",
        reason_code="ENTITLEMENT_UPGRADE_REQUIRED",
    )
