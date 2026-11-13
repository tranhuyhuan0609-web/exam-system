"""Public facade for the assembly module (C04)."""

from app.modules.assembly.internal.permutation import (
    AttemptPermutation,
    PermutationItemInput,
    generate_attempt_permutation,
)

__all__ = [
    "AttemptPermutation",
    "PermutationItemInput",
    "generate_attempt_permutation",
]
