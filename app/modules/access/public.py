"""Public API for app.modules.access (F05 RBAC/ABAC & Entitlement core)."""

from app.modules.access.internal.evaluator import (
    AccessControlError,
    AccessPrincipal,
    AssetAccessDecision,
    EntitlementTier,
    MembershipStatus,
    ResourceContext,
    VisibilityScope,
    assert_active_membership,
    assert_tenant_scope,
    authorize_action,
    entitlement_satisfies,
    evaluate_asset_access,
)

__all__ = [
    "AccessControlError",
    "AccessPrincipal",
    "AssetAccessDecision",
    "EntitlementTier",
    "MembershipStatus",
    "ResourceContext",
    "VisibilityScope",
    "assert_active_membership",
    "assert_tenant_scope",
    "authorize_action",
    "entitlement_satisfies",
    "evaluate_asset_access",
]
