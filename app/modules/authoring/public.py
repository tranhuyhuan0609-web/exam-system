"""Public API for app.modules.authoring (B02 Block Studio: validators, drafts, block rows)."""

from app.modules.authoring.internal.catalog import load_asset_catalog
from app.modules.authoring.internal.service import (
    DraftForbiddenError,
    DraftNotFoundError,
    DraftRecord,
    ValidationOutcome,
    create_draft,
    materialize_version_blocks,
    update_draft,
    validate_draft,
)
from app.modules.authoring.internal.studio import (
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_NODES,
    collect_asset_references,
    publish_validated_content_version,
    validate_content_version,
    validate_katex_syntax,
)

__all__ = [
    "MAX_PAYLOAD_DEPTH",
    "MAX_PAYLOAD_NODES",
    "DraftForbiddenError",
    "DraftNotFoundError",
    "DraftRecord",
    "ValidationOutcome",
    "collect_asset_references",
    "create_draft",
    "load_asset_catalog",
    "materialize_version_blocks",
    "publish_validated_content_version",
    "update_draft",
    "validate_content_version",
    "validate_draft",
    "validate_katex_syntax",
]
