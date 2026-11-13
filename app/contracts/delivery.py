"""Public Pydantic allowlist contracts for sanitized exam delivery (module C05)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import QuestionItemType


class NumericParsePolicyPublicContract(BaseModel):
    """Client-safe numeric input formatting constraints (never contains grading tolerance or key)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal_sep: str = "COMMA_OR_DOT"
    allow_thousand_sep: bool = False
    max_chars: int = Field(default=4, ge=1)


class SanitizedOptionContract(BaseModel):
    """Display-only option contract using reassigned label (`A..D` / `a..d`), zero origin IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_label: str
    content_json: dict[str, Any] = Field(default_factory=dict)


class SanitizedQuestionContract(BaseModel):
    """Strictly allowlisted question payload delivered to an active exam session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_version_id: UUID
    section_code: str
    item_type: QuestionItemType
    display_position: int = Field(ge=1)
    stimulus_id: UUID | None = None
    stimulus_json: dict[str, Any] | None = None
    stem_json: dict[str, Any] = Field(default_factory=dict)
    options: tuple[SanitizedOptionContract, ...] = ()
    numeric_parse_policy: NumericParsePolicyPublicContract | None = None


class SanitizedExamPaperContract(BaseModel):
    """Strictly allowlisted exam paper payload safe to serialize to exam clients."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: UUID
    assessment_version_id: UUID
    deadline_at: datetime
    questions: tuple[SanitizedQuestionContract, ...]
