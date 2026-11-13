"""Assessment framework and blueprint contracts (module C03).

Only stdlib, pydantic, and ``app.contracts.enums`` may be imported here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enums import QuestionItemType

SectionCode = Literal["PART_I_MCQ", "PART_II_TF4", "PART_III_SHORT", "PART_IV_ESSAY"]
CognitiveLevel = Literal["NB", "TH", "VD", "VDC"]


class SectionSpecContract(BaseModel):
    """Specification of a single exam section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    section_code: SectionCode
    item_type: QuestionItemType
    question_count: int
    points_per_question: Decimal

    @property
    def section_points(self) -> Decimal:
        return (Decimal(self.question_count) * self.points_per_question).quantize(Decimal("0.01"))


class FrameworkSpecContract(BaseModel):
    """Canonical assessment framework specification (e.g. THPTQG 2025+ Math or Physics)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    framework_code: str = Field(min_length=1)
    subject_code: str = Field(min_length=1)
    duration_seconds: int = Field(gt=0)
    total_questions: int = Field(gt=0)
    total_points: Decimal = Decimal("10.00")
    sections: tuple[SectionSpecContract, ...]


class BlueprintMatrixCell(BaseModel):
    """One cell in a blueprint requirement matrix (topic x cognitive level x item type)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy_node_code: str = Field(min_length=1)
    cognitive_level: CognitiveLevel
    item_type: QuestionItemType
    required_count: int = Field(ge=0)


class BlueprintContract(BaseModel):
    """Candidate exam blueprint to be validated against a framework and checked for shortages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    framework_code: str = "THPTQG_2025"
    subject_code: str
    duration_seconds: int
    total_points: Decimal = Decimal("10.00")
    sections: tuple[SectionSpecContract, ...]
    matrix_cells: tuple[BlueprintMatrixCell, ...] = ()


class BlueprintValidationError(BaseModel):
    """Structured validation error returned by ``validate_blueprint``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    field: str | None = None


class ShortageCell(BaseModel):
    """Shortage count for a single matrix cell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy_node_code: str
    cognitive_level: CognitiveLevel
    item_type: QuestionItemType
    required_count: int
    available_count: int
    shortage_count: int


class ShortageReport(BaseModel):
    """Aggregated question bank shortage report for a blueprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_required: int
    total_available_matched: int
    total_shortage: int
    has_shortage: bool
    shortages: tuple[ShortageCell, ...]
