"""Public facade for the taxonomy module (B04).

Other modules import only from here, never from `app.modules.taxonomy.internal`.
"""

from app.modules.taxonomy.internal.service import (
    TaxonomyNodeRecord,
    TaxonomyNodeVersionRecord,
    add_taxonomy_alias,
    create_node_version,
    create_taxonomy_node,
    get_node_by_code,
    list_node_versions,
    list_subtree,
    normalize_alias,
    normalize_ltree_label,
)

__all__ = [
    "TaxonomyNodeRecord",
    "TaxonomyNodeVersionRecord",
    "add_taxonomy_alias",
    "create_node_version",
    "create_taxonomy_node",
    "get_node_by_code",
    "list_node_versions",
    "list_subtree",
    "normalize_alias",
    "normalize_ltree_label",
]

