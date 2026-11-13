"""Pure unit tests for F05 RBAC/ABAC, Tenant Isolation & Entitlements (P26)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.access_control.public import (
    AccessControlError,
    AccessPrincipal,
    EntitlementTier,
    MembershipStatus,
    ResourceContext,
    VisibilityScope,
    authorize_action,
    evaluate_asset_access,
)


def _principal(
    *,
    tenant_id: str = "tenant-a",
    status: MembershipStatus = MembershipStatus.ACTIVE,
    permissions: frozenset[str] = frozenset({"question:read"}),
    tier: EntitlementTier = EntitlementTier.FREE,
    is_under_16: bool = False,
    has_parental_consent: bool = True,
) -> AccessPrincipal:
    return AccessPrincipal(
        tenant_id=tenant_id,
        membership_id="mem-1",
        user_id="user-1",
        membership_status=status,
        permissions=permissions,
        entitlement_tier=tier,
        is_under_16=is_under_16,
        has_parental_consent=has_parental_consent,
    )


def test_enums_match_ddl_check_constraints() -> None:
    ddl = Path("docs/architecture/chapters/04_postgresql_ddl_rls_grants.sql").read_text(encoding="utf-8")
    for enum_cls in (MembershipStatus, EntitlementTier, VisibilityScope):
        for member in enum_cls:
            assert f"'{member.value}'" in ddl


def test_cross_tenant_idor_blocked_on_reads_and_writes() -> None:
    user_a = _principal(
        tenant_id="tenant-a",
        permissions=frozenset({"question:read", "question:edit"}),
        tier=EntitlementTier.SCHOOL_ENTERPRISE,
    )
    private_b = ResourceContext(tenant_id="tenant-b", visibility_scope=VisibilityScope.TENANT_PRIVATE)

    with pytest.raises(AccessControlError) as read_exc:
        authorize_action(user_a, permission_code="question:read", resource=private_b, is_write=False)
    assert read_exc.value.code == "CROSS_TENANT_FORBIDDEN"

    # Even SYSTEM_PUBLIC forbids cross-tenant writes
    public_b = ResourceContext(tenant_id="tenant-b", visibility_scope=VisibilityScope.SYSTEM_PUBLIC)
    authorize_action(user_a, permission_code="question:read", resource=public_b, is_write=False)
    with pytest.raises(AccessControlError) as write_exc:
        authorize_action(user_a, permission_code="question:edit", resource=public_b, is_write=True)
    assert write_exc.value.code == "CROSS_TENANT_FORBIDDEN"


def test_rbac_and_entitlement_tiers_are_strictly_decoupled() -> None:
    # School enterprise tier alone NEVER grants question:approve permission
    enterprise_buyer = _principal(
        permissions=frozenset({"question:read"}),
        tier=EntitlementTier.SCHOOL_ENTERPRISE,
    )
    res = ResourceContext(tenant_id="tenant-a", required_tier=EntitlementTier.FREE)
    with pytest.raises(AccessControlError) as rbac_exc:
        authorize_action(enterprise_buyer, permission_code="question:approve", resource=res, is_write=True)
    assert rbac_exc.value.code == "PERMISSION_DENIED"

    # Full reviewer permission on FREE tier still cannot consume TEACHER_PRO resource
    free_reviewer = _principal(
        permissions=frozenset({"question:approve", "analytics:export"}),
        tier=EntitlementTier.FREE,
    )
    pro_res = ResourceContext(tenant_id="tenant-a", required_tier=EntitlementTier.TEACHER_PRO)
    with pytest.raises(AccessControlError) as tier_exc:
        authorize_action(free_reviewer, permission_code="analytics:export", resource=pro_res)
    assert tier_exc.value.code == "ENTITLEMENT_UPGRADE_REQUIRED"


def test_inactive_membership_and_missing_under_16_parental_consent_fail_closed() -> None:
    for status in (MembershipStatus.INACTIVE, MembershipStatus.SUSPENDED):
        p = _principal(status=status, permissions=frozenset({"attempt:start"}))
        with pytest.raises(AccessControlError) as exc:
            authorize_action(p, permission_code="attempt:start", resource=ResourceContext(tenant_id="tenant-a"))
        assert exc.value.code == "MEMBERSHIP_NOT_ACTIVE"

    minor_no_consent = _principal(
        permissions=frozenset({"profile:write"}),
        is_under_16=True,
        has_parental_consent=False,
    )
    with pytest.raises(AccessControlError) as consent_exc:
        authorize_action(
            minor_no_consent,
            permission_code="profile:write",
            resource=ResourceContext(tenant_id="tenant-a"),
            is_write=True,
            requires_sensitive_learner_profile=True,
        )
    assert consent_exc.value.code == "PARENTAL_CONSENT_REQUIRED"


def test_asset_access_grants_preview_only_for_free_and_blocks_cross_tenant() -> None:
    free_user = _principal(tenant_id="tenant-a", tier=EntitlementTier.FREE)
    pro_user = _principal(tenant_id="tenant-a", tier=EntitlementTier.STUDENT_PRO)

    pro_doc = ResourceContext(
        tenant_id="tenant-b",
        visibility_scope=VisibilityScope.SYSTEM_PUBLIC,
        required_tier=EntitlementTier.STUDENT_PRO,
    )
    assert evaluate_asset_access(free_user, pro_doc, has_preview_pdf=True).access_mode == "PREVIEW_PDF"
    assert evaluate_asset_access(free_user, pro_doc, has_preview_pdf=False).access_mode == "DENIED"
    assert evaluate_asset_access(pro_user, pro_doc, has_preview_pdf=True).access_mode == "FULL_ASSET"

    private_other_tenant = ResourceContext(
        tenant_id="tenant-b",
        visibility_scope=VisibilityScope.TENANT_PRIVATE,
        required_tier=EntitlementTier.FREE,
    )
    denied = evaluate_asset_access(pro_user, private_other_tenant, has_preview_pdf=True)
    assert denied.allowed is False
    assert denied.reason_code == "CROSS_TENANT_FORBIDDEN"
