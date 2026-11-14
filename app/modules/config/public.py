"""Public API for app.modules.config (G01 feature flags and platform configuration)."""

from app.modules.config.internal.evaluator import (
    FeatureFlagRule,
    FlagEvaluationResult,
    FlagMutationAudit,
    apply_flag_update_with_audit,
    evaluate_feature_flag,
    resolve_pinned_versions,
    stable_rollout_bucket,
)
from app.modules.config.internal.service import (
    FLAG_UPDATED_EVENT,
    SETTING_UPDATED_EVENT,
    FeatureFlagRecord,
    FlagUpdateResult,
    PlatformSettingRecord,
    SettingUpdateResult,
    evaluate_flag,
    get_feature_flag,
    get_platform_setting,
    list_feature_flags,
    update_feature_flag,
    update_platform_setting,
)

__all__ = [
    "FLAG_UPDATED_EVENT",
    "SETTING_UPDATED_EVENT",
    "FeatureFlagRecord",
    "FeatureFlagRule",
    "FlagEvaluationResult",
    "FlagMutationAudit",
    "FlagUpdateResult",
    "PlatformSettingRecord",
    "SettingUpdateResult",
    "apply_flag_update_with_audit",
    "evaluate_feature_flag",
    "evaluate_flag",
    "get_feature_flag",
    "get_platform_setting",
    "list_feature_flags",
    "resolve_pinned_versions",
    "stable_rollout_bucket",
    "update_feature_flag",
    "update_platform_setting",
]
