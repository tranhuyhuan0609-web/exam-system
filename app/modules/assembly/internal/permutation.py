"""Deterministic question and option permutation for an exam attempt (C04).

Uses an isolated `random.Random(attempt_seed)` instance (never global PRNG state) and sorts
inputs by stable canonical keys prior to shuffling so outputs are independent of input order.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from app.contracts.enums import QuestionItemType

_BASE_OPTION_KEYS: tuple[str, ...] = ("A", "B", "C", "D")

_SECTION_RANK: dict[str, int] = {
    "PART_I": 0,
    "PART_I_MCQ": 0,
    "PART_II": 1,
    "PART_II_TF4": 1,
    "PART_III": 2,
    "PART_III_SHORT": 2,
    "PART_IV": 3,
    "PART_IV_ESSAY": 3,
}


@dataclass(frozen=True, slots=True)
class PermutationItemInput:
    """Immutable item metadata required to build a deterministic attempt permutation."""

    question_version_id: UUID
    section_code: str
    stimulus_id: UUID | None = None
    item_type: str = QuestionItemType.SINGLE_CHOICE
    position: int = 0


@dataclass(frozen=True, slots=True)
class AttemptPermutation:
    """Deterministic question display order and per-question option mapping.

    For each SINGLE_CHOICE question `q`, `option_permutations[str(q)][display_idx]` equals
    the canonical original option key (`'A'..'D'`).
    """

    ordered_question_ids: tuple[UUID, ...]
    option_permutations: Mapping[str, tuple[str, ...]]


def _section_sort_key(section_code: str) -> tuple[int, str]:
    return (_SECTION_RANK.get(section_code, len(_SECTION_RANK)), section_code)


def _item_sort_key(item: PermutationItemInput) -> tuple[int, str, str, int, str]:
    sec_rank, sec_code = _section_sort_key(item.section_code)
    stim_key = str(item.stimulus_id) if item.stimulus_id is not None else ""
    return (sec_rank, sec_code, stim_key, item.position, str(item.question_version_id))


def generate_attempt_permutation(
    items: Sequence[PermutationItemInput],
    attempt_seed: int,
) -> AttemptPermutation:
    """Generate a deterministic question order and SINGLE_CHOICE option permutation."""
    rng = random.Random(attempt_seed)

    # Sort items stably so list order never perturbs stimulus block membership or RNG draws.
    canonical_items = sorted(items, key=_item_sort_key)

    sections: dict[str, dict[str, list[PermutationItemInput]]] = {}
    for item in canonical_items:
        block_key = str(item.stimulus_id) if item.stimulus_id is not None else str(item.question_version_id)
        sections.setdefault(item.section_code, {}).setdefault(block_key, []).append(item)

    ordered_ids: list[UUID] = []
    option_permutations: dict[str, tuple[str, ...]] = {}

    for sec_code in sorted(sections.keys(), key=_section_sort_key):
        block_map = sections[sec_code]
        blocks = [block_map[k] for k in sorted(block_map.keys())]
        rng.shuffle(blocks)

        for block in blocks:
            for q in block:
                ordered_ids.append(q.question_version_id)
                if str(q.item_type) == QuestionItemType.SINGLE_CHOICE:
                    opts = list(_BASE_OPTION_KEYS)
                    rng.shuffle(opts)
                    option_permutations[str(q.question_version_id)] = tuple(opts)

    return AttemptPermutation(
        ordered_question_ids=tuple(ordered_ids),
        option_permutations=MappingProxyType(option_permutations),
    )
