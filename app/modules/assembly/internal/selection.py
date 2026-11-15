"""Deterministic, constraint-aware question selection for a paper (module C04). Pure: no I/O, no global PRNG.

Rules (spec C04), applied per section of the framework, in the framework's order:
* only the questions handed in are candidates: the caller has already filtered the pool to PUBLISHED, VERIFIED,
  rights-cleared questions with a frozen current version;
* never two questions of one `duplicate_cluster_id` in a paper (they are the same question in disguise);
* a stimulus cluster (questions sharing `stimulus_id`) stays whole: it is taken entirely or not at all;
* the same `random_seed` over the same pool gives the same paper, whatever order the pool arrives in.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PoolQuestion:
    """A candidate: the current frozen version of a question and what the rules need to know about it."""

    question_id: UUID
    question_version_id: UUID
    item_type: str
    content_hash: str
    stimulus_id: UUID | None = None
    duplicate_cluster_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SectionRequirement:
    section_code: str
    item_type: str
    question_count: int
    points_per_question: Decimal


@dataclass(frozen=True, slots=True)
class SelectedItem:
    question_id: UUID
    question_version_id: UUID
    section_code: str
    item_type: str
    stimulus_id: UUID | None
    points: Decimal
    content_hash: str
    sequence_order: int


class AssemblyShortageError(ValueError):
    """The pool cannot fill a section under the rules; says which one and by how much."""

    def __init__(self, section_code: str, item_type: str, required: int, selected: int, candidates: int) -> None:
        self.section_code = section_code
        self.item_type = item_type
        self.required = required
        self.selected = selected
        self.candidates = candidates
        super().__init__(
            f"section {section_code} ({item_type}) needs {required} questions: "
            f"{selected} could be chosen from {candidates} candidates (duplicate clusters and stimulus groups apply)"
        )


def _block_key(block: Sequence[PoolQuestion]) -> str:
    return min(str(q.question_version_id) for q in block)


def select_items(
    pool: Sequence[PoolQuestion],
    sections: Sequence[SectionRequirement],
    seed: int,
) -> tuple[SelectedItem, ...]:
    """Choose the questions of every section; `AssemblyShortageError` when a section cannot be filled."""
    rng = random.Random(seed)
    used_questions: set[UUID] = set()
    used_clusters: set[UUID] = set()
    selected: list[SelectedItem] = []

    for section in sections:
        candidates = [q for q in pool if q.item_type == section.item_type]
        blocks_by_key: dict[str, list[PoolQuestion]] = {}
        for q in sorted(candidates, key=lambda c: str(c.question_version_id)):
            key = str(q.stimulus_id) if q.stimulus_id is not None else f"single:{q.question_version_id}"
            blocks_by_key.setdefault(key, []).append(q)
        blocks = sorted(blocks_by_key.values(), key=_block_key)  # canonical order first: input order never matters
        rng.shuffle(blocks)

        taken = 0
        for block in blocks:
            if taken + len(block) > section.question_count:
                continue  # a stimulus cluster is never split
            clusters = [q.duplicate_cluster_id for q in block if q.duplicate_cluster_id is not None]
            if len(set(clusters)) != len(clusters) or any(c in used_clusters for c in clusters):
                continue
            if any(q.question_id in used_questions for q in block):
                continue
            for q in block:
                taken += 1
                used_questions.add(q.question_id)
                if q.duplicate_cluster_id is not None:
                    used_clusters.add(q.duplicate_cluster_id)
                selected.append(
                    SelectedItem(
                        question_id=q.question_id,
                        question_version_id=q.question_version_id,
                        section_code=section.section_code,
                        item_type=section.item_type,
                        stimulus_id=q.stimulus_id,
                        points=section.points_per_question,
                        content_hash=q.content_hash,
                        sequence_order=len(selected) + 1,
                    )
                )
            if taken == section.question_count:
                break
        if taken != section.question_count:
            raise AssemblyShortageError(
                section.section_code, section.item_type, section.question_count, taken, len(candidates)
            )
    return tuple(selected)
