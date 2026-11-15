"""Public facade for the assembly module (C04)."""

from app.modules.assembly.internal.permutation import (
    AttemptPermutation,
    PermutationItemInput,
    generate_attempt_permutation,
)
from app.modules.assembly.internal.selection import (
    AssemblyShortageError,
    PoolQuestion,
    SectionRequirement,
    SelectedItem,
    select_items,
)
from app.modules.assembly.internal.service import (
    AssemblyShortageFailed,
    AssessmentConflictError,
    AssessmentItemRecord,
    AssessmentNotFoundError,
    AssessmentRecord,
    AssessmentVersionRecord,
    PublishGateBlockedError,
    PublishResult,
    assemble_assessment,
    create_assessment,
    get_assessment,
    publish_assessment,
)

__all__ = [
    "AssemblyShortageError",
    "AssemblyShortageFailed",
    "AssessmentConflictError",
    "AssessmentItemRecord",
    "AssessmentNotFoundError",
    "AssessmentRecord",
    "AssessmentVersionRecord",
    "AttemptPermutation",
    "PermutationItemInput",
    "PoolQuestion",
    "PublishGateBlockedError",
    "PublishResult",
    "SectionRequirement",
    "SelectedItem",
    "assemble_assessment",
    "create_assessment",
    "generate_attempt_permutation",
    "get_assessment",
    "publish_assessment",
    "select_items",
]
