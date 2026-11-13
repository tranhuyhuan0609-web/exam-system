"""Pure unit tests for G01 Feature Flags, Rollout Cohorts & Version Pinning (P27)."""

from __future__ import annotations

import pytest

from app.modules.config.public import (
    FeatureFlagRule,
    apply_flag_update_with_audit,
    evaluate_feature_flag,
    resolve_pinned_versions,
    stable_rollout_bucket,
)


def test_whitelist_enables_canary_tenant_and_blacklist_overrides_everything() -> None:
    rule = FeatureFlagRule(
        key="exam.sympy_v2",
        is_enabled=False,
        tenant_whitelist=frozenset({"school-canary"}),
        tenant_blacklist=frozenset({"school-blocked"}),
    )

    canary = evaluate_feature_flag(rule, tenant_id="school-canary")
    assert canary.enabled is True
    assert canary.reason == "TENANT_WHITELIST"

    normal = evaluate_feature_flag(rule, tenant_id="school-normal")
    assert normal.enabled is False
    assert normal.reason == "DISABLED"

    # Even if globally enabled AND whitelisted, blacklist wins fail-safe
    mixed = FeatureFlagRule(
        key="exam.sympy_v2",
        is_enabled=True,
        tenant_whitelist=frozenset({"school-blocked"}),
        tenant_blacklist=frozenset({"school-blocked"}),
    )
    blocked = evaluate_feature_flag(mixed, tenant_id="school-blocked")
    assert blocked.enabled is False
    assert blocked.reason == "TENANT_BLACKLISTED"


def test_deterministic_percentage_rollout_is_monotonic_and_accurate() -> None:
    rule_20 = FeatureFlagRule(key="ocr.hybrid_v3", is_enabled=True, rollout_percent=20)
    rule_50 = FeatureFlagRule(key="ocr.hybrid_v3", is_enabled=True, rollout_percent=50)

    subjects = [f"user-{i:04d}" for i in range(1_000)]
    in_20 = {s for s in subjects if evaluate_feature_flag(rule_20, user_id=s).enabled}
    in_50 = {s for s in subjects if evaluate_feature_flag(rule_50, user_id=s).enabled}

    # Empirical share closely tracks configured rollout percentage
    assert 150 <= len(in_20) <= 250
    assert 440 <= len(in_50) <= 560
    # Increasing percentage never ejects previously enrolled cohorts (monotonic)
    assert in_20.issubset(in_50)
    # Hashing stability
    assert stable_rollout_bucket("ocr.hybrid_v3", "user-0042") == stable_rollout_bucket(
        "ocr.hybrid_v3", "user-0042"
    )

    with pytest.raises(ValueError):
        FeatureFlagRule(key="bad.pct", rollout_percent=101)


def test_emergency_version_pin_fallback_and_audit_diff() -> None:
    pin_rule = FeatureFlagRule(
        key="ops.pin_extraction_stack",
        is_enabled=False,
        tenant_whitelist=frozenset({"tenant-affected"}),
        pinned_parser_version="pdf-v1.8.2",
        pinned_ai_model="gemini-2.5-flash-stable",
    )
    rules = {pin_rule.key: pin_rule}

    # Unaffected tenant gets latest defaults
    assert resolve_pinned_versions(
        rules,
        flag_key="ops.pin_extraction_stack",
        default_parser_version="pdf-v2.0.0",
        default_ai_model="gemini-2.5-pro",
        tenant_id="tenant-other",
    ) == ("pdf-v2.0.0", "gemini-2.5-pro")

    # Affected tenant uses emergency pinned versions without code redeploy
    assert resolve_pinned_versions(
        rules,
        flag_key="ops.pin_extraction_stack",
        default_parser_version="pdf-v2.0.0",
        default_ai_model="gemini-2.5-pro",
        tenant_id="tenant-affected",
    ) == ("pdf-v1.8.2", "gemini-2.5-flash-stable")

    # Mutation emits immutable before/after audit snapshots
    audit = apply_flag_update_with_audit(
        pin_rule,
        is_enabled=True,
        rollout_percent=100,
        pinned_ai_model=None,
    )
    assert audit.before_json["is_enabled"] is False
    assert audit.after_json["is_enabled"] is True
    assert audit.before_json["pinned_ai_model"] == "gemini-2.5-flash-stable"
    assert audit.after_json["pinned_ai_model"] is None
