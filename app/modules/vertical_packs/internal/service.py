"""Database service layer for G06 Non-Invasive Vertical Packs & Localization Bundles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.vertical_packs.internal.models import LocaleBundleRecord, VerticalPackRecord
from app.modules.vertical_packs.internal.verticals import (
    VerticalPackConfig,
    VerticalPackError,
    VerticalPackRegistry,
    resolve_localized_label,
)


class VerticalPackNotFoundError(LookupError):
    """Raised when a referenced vertical pack code is not registered."""


@dataclass(frozen=True, slots=True)
class VerticalPackView:
    code: str
    name: str
    taxonomy_root_prefix: str
    assessment_framework_code: str
    scoring_policy_code: str
    default_locale: str
    is_core_k12: bool
    is_enabled: bool
    manifest_json: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LocaleBundleView:
    locale_code: str
    namespace: str
    messages_json: dict[str, str]


@dataclass(frozen=True, slots=True)
class LocalizedLabelView:
    key: str
    namespace: str
    requested_locale: str
    resolved_label: str


def _row_to_config(row: VerticalPackRecord) -> VerticalPackConfig:
    m = dict(row.manifest_json or {})
    return VerticalPackConfig(
        code=row.code,
        display_name=row.name,
        taxonomy_root_prefix=str(m.get("taxonomy_root_prefix") or row.code),
        assessment_framework_code=str(m.get("assessment_framework_code") or f"FW_{row.code}"),
        scoring_policy_code=str(m.get("scoring_policy_code") or f"SCORE_{row.code}"),
        default_locale=str(m.get("default_locale") or "vi-VN"),
        is_core_k12=bool(m.get("is_core_k12", False)),
        active=bool(row.is_enabled),
    )


def _row_to_view(row: VerticalPackRecord) -> VerticalPackView:
    cfg = _row_to_config(row)
    return VerticalPackView(
        code=cfg.code,
        name=cfg.display_name,
        taxonomy_root_prefix=cfg.taxonomy_root_prefix,
        assessment_framework_code=cfg.assessment_framework_code,
        scoring_policy_code=cfg.scoring_policy_code,
        default_locale=cfg.default_locale,
        is_core_k12=cfg.is_core_k12,
        is_enabled=row.is_enabled,
        manifest_json=dict(row.manifest_json or {}),
    )


async def _load_registry_and_rows(
    session: AsyncSession,
) -> tuple[VerticalPackRegistry, dict[str, VerticalPackRecord]]:
    rows = list(
        (await session.execute(select(VerticalPackRecord).order_by(VerticalPackRecord.code.asc()))).scalars()
    )
    registry = VerticalPackRegistry()
    by_code: dict[str, VerticalPackRecord] = {}
    for row in rows:
        cfg = _row_to_config(row)
        registry.register(cfg)
        by_code[cfg.code] = row
    return registry, by_code


async def list_vertical_packs(
    session: AsyncSession,
    *,
    active_only: bool = False,
) -> tuple[VerticalPackView, ...]:
    """Return registered vertical packs (optionally filtered to enabled packs)."""
    stmt = select(VerticalPackRecord).order_by(VerticalPackRecord.code.asc())
    if active_only:
        stmt = stmt.where(VerticalPackRecord.is_enabled.is_(True))
    rows = (await session.execute(stmt)).scalars()
    return tuple(_row_to_view(r) for r in rows)


async def register_vertical_pack(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    taxonomy_root_prefix: str,
    assessment_framework_code: str,
    scoring_policy_code: str,
    default_locale: str = "vi-VN",
    is_core_k12: bool = False,
    is_enabled: bool = False,
    extra_manifest: dict[str, Any] | None = None,
) -> VerticalPackView:
    """Register a new non-invasive vertical pack after verifying prefix isolation."""
    if not name or not name.strip():
        raise VerticalPackError("pack name is required")

    registry, _ = await _load_registry_and_rows(session)
    cfg = VerticalPackConfig(
        code=code,
        display_name=name.strip(),
        taxonomy_root_prefix=taxonomy_root_prefix,
        assessment_framework_code=assessment_framework_code.strip(),
        scoring_policy_code=scoring_policy_code.strip(),
        default_locale=default_locale.strip() or "vi-VN",
        is_core_k12=is_core_k12,
        active=is_enabled,
    )
    normalized = registry.register(cfg)
    manifest: dict[str, Any] = dict(extra_manifest or {})
    manifest.update(
        {
            "taxonomy_root_prefix": normalized.taxonomy_root_prefix,
            "assessment_framework_code": normalized.assessment_framework_code,
            "scoring_policy_code": normalized.scoring_policy_code,
            "default_locale": normalized.default_locale,
            "is_core_k12": normalized.is_core_k12,
        }
    )

    row = VerticalPackRecord(
        code=normalized.code,
        name=normalized.display_name,
        manifest_json=manifest,
        is_enabled=is_enabled,
    )
    session.add(row)
    await session.flush()
    return _row_to_view(row)


async def activate_vertical_pack(
    session: AsyncSession,
    *,
    code: str,
) -> VerticalPackView:
    """Enable a registered vertical pack non-invasively without altering core DDL."""
    normalized_code = code.strip().upper()
    registry, by_code = await _load_registry_and_rows(session)
    if normalized_code not in by_code:
        raise VerticalPackNotFoundError(f"vertical pack {code!r} was not found")
    updated_cfg = registry.activate(normalized_code)
    row = by_code[normalized_code]
    row.is_enabled = updated_cfg.active
    await session.flush()
    return _row_to_view(row)


async def deactivate_vertical_pack(
    session: AsyncSession,
    *,
    code: str,
) -> VerticalPackView:
    """Disable an expansion vertical pack while protecting the core K-12 pack."""
    normalized_code = code.strip().upper()
    registry, by_code = await _load_registry_and_rows(session)
    if normalized_code not in by_code:
        raise VerticalPackNotFoundError(f"vertical pack {code!r} was not found")
    updated_cfg = registry.deactivate(normalized_code)
    row = by_code[normalized_code]
    row.is_enabled = updated_cfg.active
    await session.flush()
    return _row_to_view(row)


async def upsert_locale_bundle(
    session: AsyncSession,
    *,
    locale_code: str,
    namespace: str,
    messages: dict[str, str],
) -> LocaleBundleView:
    """Insert or merge UI localization messages for `(locale_code, namespace)`."""
    clean_locale = locale_code.strip()
    clean_ns = namespace.strip()
    if not clean_locale or not clean_ns:
        raise VerticalPackError("locale_code and namespace are required")

    clean_messages = {str(k): str(v) for k, v in messages.items()}
    stmt = (
        pg_insert(LocaleBundleRecord)
        .values(
            locale_code=clean_locale,
            namespace=clean_ns,
            messages_json=clean_messages,
        )
        .on_conflict_do_update(
            index_elements=["locale_code", "namespace"],
            set_={"messages_json": clean_messages},
        )
    )
    await session.execute(stmt)
    await session.flush()
    return LocaleBundleView(
        locale_code=clean_locale,
        namespace=clean_ns,
        messages_json=clean_messages,
    )


async def resolve_localized_ui_label(
    session: AsyncSession,
    *,
    key: str,
    requested_locale: str,
    namespace: str = "common",
    default_locale: str = "vi-VN",
) -> LocalizedLabelView:
    """Resolve UI/vertical label via DB `locale_bundles` with exact -> base -> default fallback."""
    rows = (
        await session.execute(
            select(LocaleBundleRecord).where(LocaleBundleRecord.namespace == namespace.strip())
        )
    ).scalars()
    bundles: dict[str, dict[str, str]] = {
        row.locale_code: {str(k): str(v) for k, v in (row.messages_json or {}).items()}
        for row in rows
    }
    label = resolve_localized_label(
        bundles=bundles,
        key=key,
        requested_locale=requested_locale,
        default_locale=default_locale,
    )
    return LocalizedLabelView(
        key=key,
        namespace=namespace.strip(),
        requested_locale=requested_locale,
        resolved_label=label,
    )
