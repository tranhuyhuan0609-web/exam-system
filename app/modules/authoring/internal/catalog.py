"""Asset catalog for B02 validation: which referenced assets the tenant may embed.

The lookup runs under the caller's row-level security (`assets`: the tenant's own rows plus the system
tenant's non-bronze ones), so an asset of another tenant is simply not found and reported as missing, never
confirmed to exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.public import AssetNotFoundError, get_asset_by_id
from app.modules.content.public import AssetCatalogEntry
from app.platform.models import SYSTEM_PUBLIC_TENANT_ID


async def load_asset_catalog(session: AsyncSession, asset_ids: Iterable[str]) -> dict[str, AssetCatalogEntry]:
    """Catalog entries for the ids that resolve to a visible asset; malformed ids are left out (reported missing)."""
    catalog: dict[str, AssetCatalogEntry] = {}
    for raw in dict.fromkeys(asset_ids):
        try:
            asset_id = uuid.UUID(raw)
        except (ValueError, AttributeError):
            continue
        try:
            asset = await get_asset_by_id(session, asset_id=asset_id)
        except AssetNotFoundError:
            continue
        scope = "SYSTEM_PUBLIC" if asset.tenant_id == SYSTEM_PUBLIC_TENANT_ID else "TENANT_PRIVATE"
        catalog[raw] = AssetCatalogEntry(asset_id=raw, tenant_id=str(asset.tenant_id), visibility_scope=scope)
    return catalog
