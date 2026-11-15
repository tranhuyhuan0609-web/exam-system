"""Public API of the generation module (E03)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.generation.internal.gap_filler import (
    ALLOWED_ANCHOR_RIGHTS,
    GeneratedCandidate,
    GenerationPolicyError,
    GenerationRequest,
    GenerationStatus,
    MatrixGapCell,
    SourceAnchor,
    approve_and_publish_ai_candidate,
    create_gap_fill_request,
    record_sympy_verification_and_queue_review,
    register_generated_candidate,
)

if TYPE_CHECKING:
    from app.modules.generation.internal.service import (
        ALLOWED_DIFFICULTIES,
        AnchorInput,
        GenerationNotFoundError,
        GenerationPolicyViolationError,
        GenerationPublishView,
        GenerationRequestView,
        GenerationValidationError,
        approve_generation_and_publish,
        create_ai_generation,
        get_generation_request,
        run_c02_verification_and_queue_review,
    )

_SERVICE_EXPORTS = frozenset(
    {
        "ALLOWED_DIFFICULTIES",
        "AnchorInput",
        "GenerationNotFoundError",
        "GenerationPolicyViolationError",
        "GenerationPublishView",
        "GenerationRequestView",
        "GenerationValidationError",
        "approve_generation_and_publish",
        "create_ai_generation",
        "get_generation_request",
        "run_c02_verification_and_queue_review",
    }
)


def __getattr__(name: str) -> Any:
    """Lazily load DB service exports so pure-core callers avoid importing ORM models."""
    if name in _SERVICE_EXPORTS:
        service_mod = importlib.import_module("app.modules.generation.internal.service")
        value = getattr(service_mod, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ALLOWED_ANCHOR_RIGHTS",
    "ALLOWED_DIFFICULTIES",
    "AnchorInput",
    "GeneratedCandidate",
    "GenerationNotFoundError",
    "GenerationPolicyError",
    "GenerationPolicyViolationError",
    "GenerationPublishView",
    "GenerationRequest",
    "GenerationRequestView",
    "GenerationStatus",
    "GenerationValidationError",
    "MatrixGapCell",
    "SourceAnchor",
    "approve_and_publish_ai_candidate",
    "approve_generation_and_publish",
    "create_ai_generation",
    "create_gap_fill_request",
    "get_generation_request",
    "record_sympy_verification_and_queue_review",
    "register_generated_candidate",
    "run_c02_verification_and_queue_review",
]
