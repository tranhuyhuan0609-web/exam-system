"""Pure free-text PII erasure helpers for right-to-erasure workflows (modules F04/G02)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

REDACTED_TEXT_MARKER = "[REDACTED_BY_ERASURE]"


@dataclass(frozen=True, slots=True)
class ScrubCounts:
    """Count of scrubbed records per table category."""

    tutor_messages: int
    appeals: int
    comment_threads: int

    @property
    def total(self) -> int:
        return self.tutor_messages + self.appeals + self.comment_threads


@dataclass(frozen=True, slots=True)
class ScrubResult:
    """Immutable result containing freshly scrubbed record copies and per-type counts."""

    tutor_messages: tuple[dict[str, Any], ...]
    appeals: tuple[dict[str, Any], ...]
    comment_threads: tuple[dict[str, Any], ...]
    counts: ScrubCounts


def scrub_free_text_pii_records(
    tutor_messages: Sequence[Mapping[str, Any]],
    appeals: Sequence[Mapping[str, Any]],
    comment_threads: Sequence[Mapping[str, Any]],
) -> ScrubResult:
    """Return new scrubbed copies of free-text PII records along with per-type counts."""
    scrubbed_msgs: list[dict[str, Any]] = []
    for msg in tutor_messages:
        copied = deepcopy(dict(msg))
        copied["message_md"] = REDACTED_TEXT_MARKER
        copied["citations_json"] = []
        scrubbed_msgs.append(copied)

    scrubbed_appeals: list[dict[str, Any]] = []
    for appeal in appeals:
        copied = deepcopy(dict(appeal))
        copied["reason_text"] = REDACTED_TEXT_MARKER
        copied["resolution_note"] = None
        scrubbed_appeals.append(copied)

    scrubbed_comments: list[dict[str, Any]] = []
    for comment in comment_threads:
        copied = deepcopy(dict(comment))
        copied["body_text"] = REDACTED_TEXT_MARKER
        scrubbed_comments.append(copied)

    return ScrubResult(
        tutor_messages=tuple(scrubbed_msgs),
        appeals=tuple(scrubbed_appeals),
        comment_threads=tuple(scrubbed_comments),
        counts=ScrubCounts(
            tutor_messages=len(scrubbed_msgs),
            appeals=len(scrubbed_appeals),
            comment_threads=len(scrubbed_comments),
        ),
    )
