"""Public facade for the taxonomy module (B04).

Other modules import only from here, never from `app.modules.taxonomy.internal`.
"""

from app.modules.taxonomy.internal.service import (
    TaxonomyNodeRecord,
    TaxonomyNodeVersionRecord,
    TaxonomyNodeViewRecord,
    add_taxonomy_alias,
    create_node_version,
    create_taxonomy_node,
    get_node_by_code,
    get_node_by_id,
    list_node_versions,
    list_subtree,
    list_taxonomy_nodes,
    normalize_alias,
    normalize_ltree_label,
    validate_ltree_path,
)

__all__ = [
    "TaxonomyNodeRecord",
    "TaxonomyNodeVersionRecord",
    "TaxonomyNodeViewRecord",
    "add_taxonomy_alias",
    "create_node_version",
    "create_taxonomy_node",
    "get_node_by_code",
    "get_node_by_id",
    "list_node_versions",
    "list_subtree",
    "list_taxonomy_nodes",
    "normalize_alias",
    "normalize_ltree_label",
    "validate_ltree_path",
]
