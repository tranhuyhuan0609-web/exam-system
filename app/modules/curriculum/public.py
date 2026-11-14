"""Public facade for the curriculum module (B06).

Other modules import only from here, never from `app.modules.curriculum.internal`.
"""

from app.modules.curriculum.internal.seed import SeedKnttResult, seed_kntt_math_physics
from app.modules.curriculum.internal.service import (
    CognitiveCounts,
    CoverageNodeRecord,
    CoverageReportRecord,
    CurriculumNodeInputSpec,
    CurriculumNodeRecord,
    CurriculumVersionRecord,
    DuplicateMappingError,
    TaxonomyMappingRecord,
    create_curriculum_node,
    create_curriculum_version,
    create_curriculum_with_nodes,
    fork_curriculum_version,
    get_curriculum_coverage,
    get_curriculum_version_by_code,
    link_curriculum_to_taxonomy,
    list_curriculum_nodes,
    list_mappings_for_curriculum_node,
    list_mappings_for_taxonomy_node,
    map_curriculum_to_taxonomy,
)

__all__ = [
    "CognitiveCounts",
    "CoverageNodeRecord",
    "CoverageReportRecord",
    "CurriculumNodeInputSpec",
    "CurriculumNodeRecord",
    "CurriculumVersionRecord",
    "DuplicateMappingError",
    "SeedKnttResult",
    "TaxonomyMappingRecord",
    "create_curriculum_node",
    "create_curriculum_version",
    "create_curriculum_with_nodes",
    "fork_curriculum_version",
    "get_curriculum_coverage",
    "get_curriculum_version_by_code",
    "link_curriculum_to_taxonomy",
    "list_curriculum_nodes",
    "list_mappings_for_curriculum_node",
    "list_mappings_for_taxonomy_node",
    "map_curriculum_to_taxonomy",
    "seed_kntt_math_physics",
]
