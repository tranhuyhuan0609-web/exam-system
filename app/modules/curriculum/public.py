"""Public facade for the curriculum module (B06).

Other modules import only from here, never from `app.modules.curriculum.internal`.
"""

from app.modules.curriculum.internal.seed import SeedKnttResult, seed_kntt_math_physics
from app.modules.curriculum.internal.service import (
    CurriculumNodeRecord,
    CurriculumVersionRecord,
    TaxonomyMappingRecord,
    create_curriculum_node,
    create_curriculum_version,
    get_curriculum_version_by_code,
    list_curriculum_nodes,
    list_mappings_for_curriculum_node,
    list_mappings_for_taxonomy_node,
    map_curriculum_to_taxonomy,
)

__all__ = [
    "CurriculumNodeRecord",
    "CurriculumVersionRecord",
    "SeedKnttResult",
    "TaxonomyMappingRecord",
    "create_curriculum_node",
    "create_curriculum_version",
    "get_curriculum_version_by_code",
    "list_curriculum_nodes",
    "list_mappings_for_curriculum_node",
    "list_mappings_for_taxonomy_node",
    "map_curriculum_to_taxonomy",
    "seed_kntt_math_physics",
]
