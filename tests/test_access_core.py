"""Pure unit tests for F05 RBAC/ABAC, Tenant Isolation & Entitlements (P26)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.access.public import (
    AccessControlError,
    AccessPrincipal,
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
    assert (
        evaluate_asset_access(free_user, pro_doc, permission_code="question:read", has_preview_pdf=True).access_mode
        == "PREVIEW_PDF"
    )
    assert (
        evaluate_asset_access(free_user, pro_doc, permission_code="question:read", has_preview_pdf=False).access_mode
        == "DENIED"
    )
    assert (
        evaluate_asset_access(pro_user, pro_doc, permission_code="question:read", has_preview_pdf=True).access_mode
        == "FULL_ASSET"
    )

    private_other_tenant = ResourceContext(
        tenant_id="tenant-b",
        visibility_scope=VisibilityScope.TENANT_PRIVATE,
        required_tier=EntitlementTier.FREE,
    )
    denied = evaluate_asset_access(
        pro_user, private_other_tenant, permission_code="question:read", has_preview_pdf=True
    )
    assert denied.allowed is False
    assert denied.reason_code == "CROSS_TENANT_FORBIDDEN"


def test_caller_cannot_lower_resource_tier_by_passing_free_entitlement() -> None:
    free_user = _principal(permissions=frozenset({"doc:read"}), tier=EntitlementTier.FREE)
    pro_res = ResourceContext(tenant_id="tenant-a", required_tier=EntitlementTier.TEACHER_PRO)
    with pytest.raises(AccessControlError) as exc:
        authorize_action(
            free_user,
            permission_code="doc:read",
            resource=pro_res,
            required_entitlement=EntitlementTier.FREE,
        )
    assert exc.value.code == "ENTITLEMENT_UPGRADE_REQUIRED"


def test_caller_can_raise_the_required_tier_above_the_resource_tier() -> None:
    pro_user = _principal(permissions=frozenset({"doc:read"}), tier=EntitlementTier.STUDENT_PRO)
    free_res = ResourceContext(tenant_id="tenant-a", required_tier=EntitlementTier.FREE)
    authorize_action(pro_user, permission_code="doc:read", resource=free_res)
    with pytest.raises(AccessControlError) as exc:
        authorize_action(
            pro_user,
            permission_code="doc:read",
            resource=free_res,
            required_entitlement=EntitlementTier.SCHOOL_ENTERPRISE,
        )
    assert exc.value.code == "ENTITLEMENT_UPGRADE_REQUIRED"


def test_entitlement_satisfies_is_monotonic_and_rejects_unknown_tiers() -> None:
    tiers = list(EntitlementTier)
    for i, current in enumerate(tiers):
        for j, required in enumerate(tiers):
            assert entitlement_satisfies(current, required) is (i >= j)
    assert entitlement_satisfies("PLATINUM", EntitlementTier.FREE) is False  # type: ignore[arg-type]
    assert entitlement_satisfies(EntitlementTier.SCHOOL_ENTERPRISE, "PLATINUM") is False  # type: ignore[arg-type]
    assert entitlement_satisfies("student_pro", EntitlementTier.FREE) is False  # type: ignore[arg-type]


def test_unknown_required_tier_on_resource_fails_closed() -> None:
    top = _principal(permissions=frozenset({"doc:read"}), tier=EntitlementTier.SCHOOL_ENTERPRISE)
    weird = ResourceContext(tenant_id="tenant-a", required_tier="PLATINUM")  # type: ignore[arg-type]
    with pytest.raises(AccessControlError) as exc:
        authorize_action(top, permission_code="doc:read", resource=weird)
    assert exc.value.code == "ENTITLEMENT_UPGRADE_REQUIRED"


def test_empty_or_blank_tenant_ids_are_rejected_on_both_sides() -> None:
    for blank in ("", "   ", "\t"):
        user = _principal(tenant_id=blank, permissions=frozenset({"doc:read"}))
        with pytest.raises(AccessControlError) as exc:
            authorize_action(user, permission_code="doc:read", resource=ResourceContext(tenant_id=blank))
        assert exc.value.code == "TENANT_ID_REQUIRED"
        with pytest.raises(AccessControlError) as exc2:
            assert_tenant_scope(user, ResourceContext(tenant_id="tenant-a"), is_write=False)
        assert exc2.value.code == "TENANT_ID_REQUIRED"
    user_a = _principal(permissions=frozenset({"doc:read"}))
    with pytest.raises(AccessControlError) as exc3:
        assert_tenant_scope(user_a, ResourceContext(tenant_id=""), is_write=False)
    assert exc3.value.code == "TENANT_ID_REQUIRED"
    decision = evaluate_asset_access(
        _principal(tenant_id=""), ResourceContext(tenant_id=""), permission_code="question:read", has_preview_pdf=True
    )
    assert decision.allowed is False and decision.reason_code == "TENANT_ID_REQUIRED"


def test_tenant_ids_compare_exactly_case_and_whitespace_sensitive() -> None:
    user = _principal(tenant_id="tenant-a")
    for other in ("Tenant-A", "tenant-a ", "tenant-b"):
        with pytest.raises(AccessControlError) as exc:
            assert_tenant_scope(user, ResourceContext(tenant_id=other), is_write=False)
        assert exc.value.code == "CROSS_TENANT_FORBIDDEN"


def test_parental_consent_defaults_to_false_so_minors_fail_closed() -> None:
    minor = AccessPrincipal(
        tenant_id="tenant-a",
        membership_id="m",
        user_id="u",
        membership_status=MembershipStatus.ACTIVE,
        permissions=frozenset({"profile:write"}),
        is_under_16=True,
    )
    assert minor.has_parental_consent is False
    with pytest.raises(AccessControlError) as exc:
        authorize_action(
            minor,
            permission_code="profile:write",
            resource=ResourceContext(tenant_id="tenant-a"),
            requires_sensitive_learner_profile=True,
        )
    assert exc.value.code == "PARENTAL_CONSENT_REQUIRED"
    consenting = replace(minor, has_parental_consent=True)
    authorize_action(
        consenting,
        permission_code="profile:write",
        resource=ResourceContext(tenant_id="tenant-a"),
        requires_sensitive_learner_profile=True,
    )
    # adults and non-sensitive operations do not need consent
    adult = replace(minor, is_under_16=False, has_parental_consent=False)
    authorize_action(
        adult,
        permission_code="profile:write",
        resource=ResourceContext(tenant_id="tenant-a"),
        requires_sensitive_learner_profile=True,
    )
    authorize_action(minor, permission_code="profile:write", resource=ResourceContext(tenant_id="tenant-a"))


def test_asset_access_requires_rbac_permission() -> None:
    no_perm = _principal(permissions=frozenset(), tier=EntitlementTier.SCHOOL_ENTERPRISE)
    res = ResourceContext(tenant_id="tenant-a")
    decision = evaluate_asset_access(no_perm, res, permission_code="asset:download", has_preview_pdf=True)
    assert decision.allowed is False
    assert decision.access_mode == "DENIED"
    assert decision.reason_code == "PERMISSION_DENIED"

    with_perm = _principal(permissions=frozenset({"asset:download"}))
    assert (
        evaluate_asset_access(with_perm, res, permission_code="asset:download", has_preview_pdf=False).access_mode
        == "FULL_ASSET"
    )
    # permission codes are exact: wrong case or empty never match
    for code in ("ASSET:DOWNLOAD", "", "  "):
        denied = evaluate_asset_access(with_perm, res, permission_code=code, has_preview_pdf=True)
        assert denied.allowed is False and denied.reason_code == "PERMISSION_DENIED"
    admin = replace(no_perm, is_super_admin=True)
    assert evaluate_asset_access(admin, res, permission_code="asset:download", has_preview_pdf=False).allowed


def test_empty_permission_code_is_denied_even_if_the_empty_string_was_granted() -> None:
    user = _principal(permissions=frozenset({""}))
    with pytest.raises(AccessControlError) as exc:
        authorize_action(user, permission_code="", resource=ResourceContext(tenant_id="tenant-a"))
    assert exc.value.code == "PERMISSION_DENIED"


def test_membership_status_is_compared_strictly() -> None:
    for status in ("active", "ACTIVE ", "", "DELETED"):
        user = _principal(status=status, permissions=frozenset({"doc:read"}))  # type: ignore[arg-type]
        with pytest.raises(AccessControlError) as exc:
            assert_active_membership(user)
        assert exc.value.code == "MEMBERSHIP_NOT_ACTIVE"
    assert_active_membership(_principal(status=MembershipStatus.ACTIVE))


def test_shared_partner_visibility_needs_explicit_tenant_listing() -> None:
    user = _principal(tenant_id="tenant-a", permissions=frozenset({"doc:read"}))
    shared = ResourceContext(
        tenant_id="tenant-b",
        visibility_scope=VisibilityScope.SHARED_PARTNER,
        partner_shared_tenant_ids=frozenset({"tenant-a"}),
    )
    authorize_action(user, permission_code="doc:read", resource=shared)
    not_listed = replace(shared, partner_shared_tenant_ids=frozenset({"tenant-c"}))
    with pytest.raises(AccessControlError):
        authorize_action(user, permission_code="doc:read", resource=not_listed)
    with pytest.raises(AccessControlError) as write:
        authorize_action(user, permission_code="doc:read", resource=shared, is_write=True)
    assert write.value.code == "CROSS_TENANT_FORBIDDEN"
    unknown_scope = replace(shared, visibility_scope="WORLD")  # type: ignore[arg-type]
    with pytest.raises(AccessControlError):
        assert_tenant_scope(user, unknown_scope, is_write=False)


def test_super_admin_still_bound_by_tenant_membership_and_tier() -> None:
    admin = AccessPrincipal(
        tenant_id="tenant-a",
        membership_id="m",
        user_id="u",
        membership_status=MembershipStatus.ACTIVE,
        is_super_admin=True,
    )
    with pytest.raises(AccessControlError) as tenant_exc:
        authorize_action(admin, permission_code="x:y", resource=ResourceContext(tenant_id="tenant-b"))
    assert tenant_exc.value.code == "CROSS_TENANT_FORBIDDEN"
    pro_res = ResourceContext(tenant_id="tenant-a", required_tier=EntitlementTier.STUDENT_PRO)
    with pytest.raises(AccessControlError) as tier_exc:
        authorize_action(admin, permission_code="x:y", resource=pro_res)
    assert tier_exc.value.code == "ENTITLEMENT_UPGRADE_REQUIRED"
    inactive = replace(admin, membership_status=MembershipStatus.SUSPENDED)
    with pytest.raises(AccessControlError) as inactive_exc:
        authorize_action(inactive, permission_code="x:y", resource=ResourceContext(tenant_id="tenant-a"))
    assert inactive_exc.value.code == "MEMBERSHIP_NOT_ACTIVE"
    authorize_action(admin, permission_code="x:y", resource=ResourceContext(tenant_id="tenant-a"))


def test_error_messages_do_not_leak_tenant_ids() -> None:
    user = _principal(tenant_id="tenant-secret-a")
    with pytest.raises(AccessControlError) as exc:
        assert_tenant_scope(user, ResourceContext(tenant_id="tenant-secret-b"), is_write=True)
    assert "tenant-secret" not in str(exc.value)
