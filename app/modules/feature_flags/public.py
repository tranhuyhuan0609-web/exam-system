"""Public API for app.modules.feature_flags (G01 Feature Flags & Config core)."""

from app.modules.feature_flags.internal.evaluator import (
    FeatureFlagRule,
    FlagEvaluationResult,
    FlagMutationAudit,
    apply_flag_update_with_audit,
    evaluate_feature_flag,
    resolve_pinned_versions,
    stable_rollout_bucket,
)

__all__ = [
    "FeatureFlagRule",
    "FlagEvaluationResult",
    "FlagMutationAudit",
    "apply_flag_update_with_audit",
    "evaluate_feature_flag",
    "resolve_pinned_versions",
    "stable_rollout_bucket",
]
