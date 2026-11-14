"""Pure unit tests for G01 Feature Flags, Rollout Cohorts & Version Pinning (P27)."""

from __future__ import annotations

import unicodedata

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
    assert stable_rollout_bucket("ocr.hybrid_v3", "user-0042") == stable_rollout_bucket("ocr.hybrid_v3", "user-0042")

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


def test_rule_validation_rejects_blank_keys_non_bool_flags_and_bad_percentages() -> None:
    for key in ("", "   "):
        with pytest.raises(ValueError):
            FeatureFlagRule(key=key)
    for pct in (-1, 101, 1000):
        with pytest.raises(ValueError):
            FeatureFlagRule(key="k", rollout_percent=pct)
    for pct in (True, 50.0, "50", None):
        with pytest.raises(TypeError):
            FeatureFlagRule(key="k", rollout_percent=pct)  # type: ignore[arg-type]
    for enabled in ("false", 1, None):
        with pytest.raises(TypeError):
            FeatureFlagRule(key="k", is_enabled=enabled)  # type: ignore[arg-type]
    FeatureFlagRule(key="k", rollout_percent=0)
    FeatureFlagRule(key="k", rollout_percent=100)


def test_rollout_edges_zero_full_and_missing_subject() -> None:
    zero = FeatureFlagRule(key="k", is_enabled=True, rollout_percent=0)
    assert evaluate_feature_flag(zero, user_id="u").reason == "DISABLED"
    full = FeatureFlagRule(key="k", is_enabled=True, rollout_percent=100)
    assert evaluate_feature_flag(full).reason == "GLOBAL_ENABLED"
    partial = FeatureFlagRule(key="k", is_enabled=True, rollout_percent=50)
    for subject in (None, ""):
        result = evaluate_feature_flag(partial, user_id=subject, tenant_id=None)
        assert result.enabled is False and result.reason == "MISSING_ROLLOUT_SUBJECT"
    off = FeatureFlagRule(key="k", is_enabled=False, rollout_percent=100)
    assert evaluate_feature_flag(off, user_id="u").enabled is False


def test_blank_tenant_never_matches_white_or_blacklists() -> None:
    rule = FeatureFlagRule(
        key="k", tenant_whitelist=frozenset({""}), tenant_blacklist=frozenset({""}), is_enabled=False
    )
    result = evaluate_feature_flag(rule, tenant_id="")
    assert result.enabled is False and result.reason == "DISABLED"


def test_tenant_ids_are_matched_exactly() -> None:
    rule = FeatureFlagRule(key="k", tenant_whitelist=frozenset({"School-A"}))
    assert evaluate_feature_flag(rule, tenant_id="School-A").enabled is True
    for other in ("school-a", "School-A ", "School-B"):
        assert evaluate_feature_flag(rule, tenant_id=other).enabled is False


def test_bucket_is_in_range_stable_and_separates_flags() -> None:
    buckets = {stable_rollout_bucket("flag", f"s{i}") for i in range(200)}
    assert all(0 <= b < 10_000 for b in buckets) and len(buckets) > 150
    for subject in ("Nguyễn", unicodedata.normalize("NFD", "Nguyễn")):
        assert 0 <= stable_rollout_bucket("flag", subject) < 10_000
    # NFC and NFD spellings are different byte strings, hence deliberately different subjects
    assert isinstance(stable_rollout_bucket("flag", ""), int)


def test_rollout_cohort_is_independent_per_flag() -> None:
    subjects = [f"user-{i}" for i in range(500)]
    a = {
        s for s in subjects if evaluate_feature_flag(FeatureFlagRule("a", True, rollout_percent=30), user_id=s).enabled
    }
    b = {
        s for s in subjects if evaluate_feature_flag(FeatureFlagRule("b", True, rollout_percent=30), user_id=s).enabled
    }
    assert a != b


def test_resolve_pinned_versions_defaults_for_missing_disabled_or_blacklisted() -> None:
    rule = FeatureFlagRule(
        key="pin",
        is_enabled=True,
        tenant_blacklist=frozenset({"t-black"}),
        pinned_parser_version="p1",
        pinned_ai_model=None,
    )
    rules = {"pin": rule}
    defaults = {"default_parser_version": "p2", "default_ai_model": "m2"}
    assert resolve_pinned_versions({}, flag_key="pin", **defaults) == ("p2", "m2")
    assert resolve_pinned_versions(rules, flag_key="other", **defaults) == ("p2", "m2")
    assert resolve_pinned_versions(rules, flag_key="pin", tenant_id="t-black", **defaults) == ("p2", "m2")
    # partial pins fall back per field
    assert resolve_pinned_versions(rules, flag_key="pin", tenant_id="t", **defaults) == ("p1", "m2")


def test_audit_update_leaves_original_untouched_and_validates_new_values() -> None:
    rule = FeatureFlagRule(key="k", tenant_whitelist=frozenset({"b", "a"}), config_json={"x": 1})
    audit = apply_flag_update_with_audit(rule, rollout_percent=10, config_json={"y": 2})
    assert rule.rollout_percent == 100 and dict(rule.config_json) == {"x": 1}
    assert audit.updated_rule.rollout_percent == 10
    assert audit.before_json["tenant_whitelist"] == ["a", "b"]
    assert audit.after_json["config_json"] == {"y": 2}
    with pytest.raises(ValueError):
        apply_flag_update_with_audit(rule, rollout_percent=101)
    with pytest.raises(TypeError):
        apply_flag_update_with_audit(rule, is_enabled="yes")  # type: ignore[arg-type]
    unchanged = apply_flag_update_with_audit(rule)
    assert unchanged.before_json == unchanged.after_json
    cleared = apply_flag_update_with_audit(
        FeatureFlagRule(key="k", pinned_parser_version="v1"), pinned_parser_version=None
    )
    assert cleared.updated_rule.pinned_parser_version is None
    # mutating the caller's config dict after the call does not leak into the rule
    cfg = {"z": 1}
    updated = apply_flag_update_with_audit(rule, config_json=cfg).updated_rule
    cfg["z"] = 2
    assert dict(updated.config_json) == {"z": 1}
