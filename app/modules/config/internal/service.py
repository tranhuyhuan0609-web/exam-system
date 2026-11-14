"""G01 config service: feature flags and platform settings on the database, audited through the outbox.

No cache: every evaluation reads the row, so a change is effective on every API node as soon as its
transaction commits (spec DoD: < 5 s). Each mutation writes an audit event to the outbox in the same
transaction (`config.feature_flag.updated`, `config.platform_setting.updated`) carrying the
before/after JSON of `evaluator.apply_flag_update_with_audit`; G02 turns them into `audit_events`.

`feature_flags.config_json` holds the evaluator fields that have no column of their own:
`rollout_percent`, `tenant_blacklist`, `pinned_parser_version`, `pinned_ai_model`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.modules.config.internal.evaluator import (
    FeatureFlagRule,
    FlagEvaluationResult,
    apply_flag_update_with_audit,
    evaluate_feature_flag,
)
from app.modules.config.internal.models import FeatureFlag, PlatformSetting
from app.modules.outbox.public import enqueue

FLAG_UPDATED_EVENT = "config.feature_flag.updated"
SETTING_UPDATED_EVENT = "config.platform_setting.updated"
_RULE_KEYS_IN_CONFIG = ("rollout_percent", "tenant_blacklist", "pinned_parser_version", "pinned_ai_model")
_AGGREGATE_NAMESPACE = uuid.UUID("6f0f3c1e-7b3a-4f53-9a51-1d2c3e4f5a6b")


class ConfigNotFoundError(LookupError):
    """The flag or setting does not exist."""


@dataclass(frozen=True, slots=True)
class FeatureFlagRecord:
    key: str
    is_enabled: bool
    tenant_whitelist: tuple[uuid.UUID, ...]
    config_json: dict[str, Any]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlatformSettingRecord:
    key: str
    value_json: Any
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FlagUpdateResult:
    flag: FeatureFlagRecord
    created: bool
    before_json: dict[str, Any] | None
    after_json: dict[str, Any]
    audit_event_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SettingUpdateResult:
    setting: PlatformSettingRecord
    created: bool
    audit_event_id: uuid.UUID


def aggregate_id_for(kind: str, key: str) -> uuid.UUID:
    """Stable UUID of a flag or setting (their primary keys are strings, the outbox wants a UUID)."""
    return uuid.uuid5(_AGGREGATE_NAMESPACE, f"{kind}:{key}")


def _record(row: FeatureFlag) -> FeatureFlagRecord:
    return FeatureFlagRecord(
        key=row.key,
        is_enabled=row.is_enabled,
        tenant_whitelist=tuple(row.tenant_whitelist or ()),
        config_json=dict(row.config_json or {}),
        updated_at=row.updated_at,
    )


def rule_from_row(key: str, is_enabled: bool, tenant_whitelist: Any, config_json: Mapping[str, Any]) -> FeatureFlagRule:
    """The evaluator's view of a `feature_flags` row."""
    extra = {k: v for k, v in config_json.items() if k not in _RULE_KEYS_IN_CONFIG}
    rollout = config_json.get("rollout_percent", 100)
    return FeatureFlagRule(
        key=key,
        is_enabled=bool(is_enabled),
        tenant_whitelist=frozenset(str(t) for t in tenant_whitelist or ()),
        tenant_blacklist=frozenset(str(t) for t in config_json.get("tenant_blacklist") or ()),
        rollout_percent=rollout if isinstance(rollout, int) and not isinstance(rollout, bool) else 100,
        pinned_parser_version=config_json.get("pinned_parser_version"),
        pinned_ai_model=config_json.get("pinned_ai_model"),
        config_json=MappingProxyType(extra),
    )


def _row_values(rule: FeatureFlagRule) -> tuple[list[uuid.UUID], dict[str, Any]]:
    config = dict(rule.config_json)
    config["rollout_percent"] = rule.rollout_percent
    if rule.tenant_blacklist:
        config["tenant_blacklist"] = sorted(rule.tenant_blacklist)
    if rule.pinned_parser_version is not None:
        config["pinned_parser_version"] = rule.pinned_parser_version
    if rule.pinned_ai_model is not None:
        config["pinned_ai_model"] = rule.pinned_ai_model
    return sorted(uuid.UUID(t) for t in rule.tenant_whitelist), config


async def list_feature_flags(session: AsyncSession) -> list[FeatureFlagRecord]:
    rows = (await session.scalars(select(FeatureFlag).order_by(FeatureFlag.key))).all()
    return [_record(r) for r in rows]


async def get_feature_flag(session: AsyncSession, key: str) -> FeatureFlagRecord | None:
    row = await session.get(FeatureFlag, key)
    return None if row is None else _record(row)


async def evaluate_flag(
    session: AsyncSession, key: str, *, tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None
) -> FlagEvaluationResult:
    """Evaluate `key` from the current row (an unknown flag is off)."""
    row = await session.get(FeatureFlag, key, populate_existing=True)
    rule = (
        FeatureFlagRule(key=key)
        if row is None
        else rule_from_row(row.key, row.is_enabled, row.tenant_whitelist, row.config_json or {})
    )
    return evaluate_feature_flag(
        rule,
        tenant_id=None if tenant_id is None else str(tenant_id),
        user_id=None if user_id is None else str(user_id),
    )


async def _audit(
    session: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    key: str,
    actor_tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    correlation_id: str | None,
    payload: dict[str, Any],
) -> uuid.UUID:
    envelope = EventEnvelopeContract(
        event_type=event_type,
        tenant_id=actor_tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id_for(aggregate_type, key),
        correlation_id=correlation_id,
        payload={**payload, "key": key, "actor_user_id": None if actor_user_id is None else str(actor_user_id)},
    )
    await enqueue(session, envelope)
    return envelope.event_id


async def update_feature_flag(
    session: AsyncSession,
    key: str,
    *,
    actor_tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    is_enabled: bool | None = None,
    tenant_whitelist: list[uuid.UUID] | None = None,
    tenant_blacklist: list[uuid.UUID] | None = None,
    rollout_percent: int | None = None,
    pinned_parser_version: str | None | Any = ...,
    pinned_ai_model: str | None | Any = ...,
    config_json: Mapping[str, Any] | None = None,
) -> FlagUpdateResult:
    """Create or change a flag (row locked for the transaction) and audit the change in the outbox.

    Raises ValueError (from the evaluator) for an invalid rollout percentage.
    """
    row = await session.scalar(select(FeatureFlag).where(FeatureFlag.key == key).with_for_update())
    created = row is None
    current = (
        FeatureFlagRule(key=key)
        if row is None
        else rule_from_row(row.key, row.is_enabled, row.tenant_whitelist, row.config_json or {})
    )
    if config_json is not None and any(k in config_json for k in _RULE_KEYS_IN_CONFIG):
        raise ValueError(f"set {', '.join(_RULE_KEYS_IN_CONFIG)} through their own fields, not config_json")
    mutation = apply_flag_update_with_audit(
        current,
        is_enabled=is_enabled,
        rollout_percent=rollout_percent,
        tenant_whitelist=None if tenant_whitelist is None else frozenset(str(t) for t in tenant_whitelist),
        tenant_blacklist=None if tenant_blacklist is None else frozenset(str(t) for t in tenant_blacklist),
        pinned_parser_version=pinned_parser_version,
        pinned_ai_model=pinned_ai_model,
        config_json=config_json,
    )
    whitelist, config = _row_values(mutation.updated_rule)
    stored = (
        await session.execute(
            pg_insert(FeatureFlag)
            .values(
                key=key,
                is_enabled=mutation.updated_rule.is_enabled,
                tenant_whitelist=whitelist,
                config_json=config,
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=[FeatureFlag.key],
                set_={
                    "is_enabled": mutation.updated_rule.is_enabled,
                    "tenant_whitelist": whitelist,
                    "config_json": config,
                    "updated_at": func.now(),
                },
            )
            .returning(FeatureFlag)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    before = None if created else mutation.before_json
    event_id = await _audit(
        session,
        event_type=FLAG_UPDATED_EVENT,
        aggregate_type="feature_flag",
        key=key,
        actor_tenant_id=actor_tenant_id,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
        payload={"created": created, "before_json": before, "after_json": mutation.after_json},
    )
    return FlagUpdateResult(
        flag=_record(stored),
        created=created,
        before_json=before,
        after_json=mutation.after_json,
        audit_event_id=event_id,
    )


async def get_platform_setting(session: AsyncSession, key: str) -> PlatformSettingRecord | None:
    row = await session.get(PlatformSetting, key, populate_existing=True)
    return None if row is None else PlatformSettingRecord(row.key, row.value_json, row.updated_at)


async def update_platform_setting(
    session: AsyncSession,
    key: str,
    value_json: Any,
    *,
    actor_tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
) -> SettingUpdateResult:
    """Create or replace a platform setting and audit the change in the outbox."""
    before_row = await session.scalar(select(PlatformSetting).where(PlatformSetting.key == key).with_for_update())
    created = before_row is None
    before = None if before_row is None else before_row.value_json
    stored = (
        await session.execute(
            pg_insert(PlatformSetting)
            .values(key=key, value_json=value_json, updated_at=func.now())
            .on_conflict_do_update(
                index_elements=[PlatformSetting.key], set_={"value_json": value_json, "updated_at": func.now()}
            )
            .returning(PlatformSetting)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    event_id = await _audit(
        session,
        event_type=SETTING_UPDATED_EVENT,
        aggregate_type="platform_setting",
        key=key,
        actor_tenant_id=actor_tenant_id,
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
        payload={"created": created, "before_json": before, "after_json": value_json},
    )
    return SettingUpdateResult(
        setting=PlatformSettingRecord(stored.key, stored.value_json, stored.updated_at),
        created=created,
        audit_event_id=event_id,
    )
