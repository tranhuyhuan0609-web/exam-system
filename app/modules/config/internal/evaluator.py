"""G01 feature flag evaluator: tenant whitelist, deterministic percentage rollout, version pins & audit diffs."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class FeatureFlagRule:
    """
    Pure representation of a `feature_flags` row aligned with G01 runtime rules.

    - `is_enabled`: global kill-switch; when True and `rollout_percent` is 100 (default), active for all.
    - `tenant_whitelist`: tenants opted in unconditionally (canary schools) even when `is_enabled=False`.
    - `tenant_blacklist`: explicit kill list overriding global rollout & whitelist (fail-safe rollback).
    - `rollout_percent`: 0..100 inclusive deterministic cohort share when `is_enabled=True`.
    - `pinned_parser_version` / `pinned_ai_model`: regression emergency pins per G01 spec.
    """

    key: str
    is_enabled: bool = False
    tenant_whitelist: frozenset[str] = field(default_factory=frozenset)
    tenant_blacklist: frozenset[str] = field(default_factory=frozenset)
    rollout_percent: int = 100
    pinned_parser_version: str | None = None
    pinned_ai_model: str | None = None
    config_json: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("FeatureFlagRule.key must be non-empty")
        if not isinstance(self.is_enabled, bool):
            raise TypeError("FeatureFlagRule.is_enabled must be a bool")
        if isinstance(self.rollout_percent, bool) or not isinstance(self.rollout_percent, int):
            raise TypeError("rollout_percent must be an int")
        if not 0 <= self.rollout_percent <= 100:
            raise ValueError(f"rollout_percent must be in 0..100, got {self.rollout_percent}")


@dataclass(frozen=True, slots=True)
class FlagEvaluationResult:
    flag_key: str
    enabled: bool
    reason: str
    pinned_parser_version: str | None
    pinned_ai_model: str | None
    config_json: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FlagMutationAudit:
    updated_rule: FeatureFlagRule
    before_json: dict[str, Any]
    after_json: dict[str, Any]


def stable_rollout_bucket(flag_key: str, subject_key: str) -> int:
    """
    Compute deterministic rollout bucket in `0..9999` (basis points = 0.01% precision).

    Stable across Python processes, Python hash randomization, and nodes.
    """
    digest = hashlib.sha256(f"{flag_key}:{subject_key}".encode()).digest()
    raw_int = int.from_bytes(digest[:8], "big")
    return raw_int % 10_000


def evaluate_feature_flag(
    rule: FeatureFlagRule,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> FlagEvaluationResult:
    """Evaluate a feature flag deterministically with explicit kill-switch & canary overrides."""
    pinned_parser = rule.pinned_parser_version
    pinned_model = rule.pinned_ai_model

    if tenant_id and tenant_id in rule.tenant_blacklist:
        return FlagEvaluationResult(
            flag_key=rule.key,
            enabled=False,
            reason="TENANT_BLACKLISTED",
            pinned_parser_version=pinned_parser,
            pinned_ai_model=pinned_model,
            config_json=rule.config_json,
        )

    if tenant_id and tenant_id in rule.tenant_whitelist:
        return FlagEvaluationResult(
            flag_key=rule.key,
            enabled=True,
            reason="TENANT_WHITELIST",
            pinned_parser_version=pinned_parser,
            pinned_ai_model=pinned_model,
            config_json=rule.config_json,
        )

    if not rule.is_enabled or rule.rollout_percent == 0:
        return FlagEvaluationResult(
            flag_key=rule.key,
            enabled=False,
            reason="DISABLED",
            pinned_parser_version=pinned_parser,
            pinned_ai_model=pinned_model,
            config_json=rule.config_json,
        )

    if rule.rollout_percent >= 100:
        return FlagEvaluationResult(
            flag_key=rule.key,
            enabled=True,
            reason="GLOBAL_ENABLED",
            pinned_parser_version=pinned_parser,
            pinned_ai_model=pinned_model,
            config_json=rule.config_json,
        )

    subject = user_id or tenant_id
    if not subject:
        return FlagEvaluationResult(
            flag_key=rule.key,
            enabled=False,
            reason="MISSING_ROLLOUT_SUBJECT",
            pinned_parser_version=pinned_parser,
            pinned_ai_model=pinned_model,
            config_json=rule.config_json,
        )

    threshold_bps = rule.rollout_percent * 100
    bucket = stable_rollout_bucket(rule.key, subject)
    in_rollout = bucket < threshold_bps
    return FlagEvaluationResult(
        flag_key=rule.key,
        enabled=in_rollout,
        reason="ROLLOUT_INCLUDED" if in_rollout else "ROLLOUT_EXCLUDED",
        pinned_parser_version=pinned_parser,
        pinned_ai_model=pinned_model,
        config_json=rule.config_json,
    )


def resolve_pinned_versions(
    rules: Mapping[str, FeatureFlagRule],
    *,
    flag_key: str,
    default_parser_version: str,
    default_ai_model: str,
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """
    Resolve active `(parser_version, ai_model)`, honoring G01 emergency version pins when enabled.
    """
    rule = rules.get(flag_key)
    if rule is None:
        return default_parser_version, default_ai_model
    eval_res = evaluate_feature_flag(rule, tenant_id=tenant_id)
    if not eval_res.enabled:
        return default_parser_version, default_ai_model
    return (
        eval_res.pinned_parser_version or default_parser_version,
        eval_res.pinned_ai_model or default_ai_model,
    )


def _rule_to_audit_dict(rule: FeatureFlagRule) -> dict[str, Any]:
    return {
        "key": rule.key,
        "is_enabled": rule.is_enabled,
        "tenant_whitelist": sorted(rule.tenant_whitelist),
        "tenant_blacklist": sorted(rule.tenant_blacklist),
        "rollout_percent": rule.rollout_percent,
        "pinned_parser_version": rule.pinned_parser_version,
        "pinned_ai_model": rule.pinned_ai_model,
        "config_json": dict(rule.config_json),
    }


def apply_flag_update_with_audit(
    current: FeatureFlagRule,
    *,
    is_enabled: bool | None = None,
    rollout_percent: int | None = None,
    tenant_whitelist: frozenset[str] | None = None,
    tenant_blacklist: frozenset[str] | None = None,
    pinned_parser_version: str | None = ...,  # type: ignore[assignment]
    pinned_ai_model: str | None = ...,  # type: ignore[assignment]
    config_json: Mapping[str, Any] | None = None,
) -> FlagMutationAudit:
    """
    Produce an updated `FeatureFlagRule` together with `before_json` / `after_json` for `audit_events`.
    """
    updated = replace(
        current,
        is_enabled=current.is_enabled if is_enabled is None else is_enabled,
        rollout_percent=current.rollout_percent if rollout_percent is None else rollout_percent,
        tenant_whitelist=current.tenant_whitelist if tenant_whitelist is None else tenant_whitelist,
        tenant_blacklist=current.tenant_blacklist if tenant_blacklist is None else tenant_blacklist,
        pinned_parser_version=(
            current.pinned_parser_version if pinned_parser_version is ... else pinned_parser_version
        ),
        pinned_ai_model=current.pinned_ai_model if pinned_ai_model is ... else pinned_ai_model,
        config_json=current.config_json if config_json is None else MappingProxyType(dict(config_json)),
    )
    return FlagMutationAudit(
        updated_rule=updated,
        before_json=_rule_to_audit_dict(current),
        after_json=_rule_to_audit_dict(updated),
    )
