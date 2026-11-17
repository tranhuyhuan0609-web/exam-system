"""Public API for app.modules.review (A09 Rights/Review Gate core)."""

import importlib
from typing import TYPE_CHECKING, Any

from app.modules.review.internal.publish_gate import (
    BRANDING_ALLOWED_RIGHTS_STATUSES,
    PUBLISHABLE_RIGHTS_STATUSES,
    GovernancePolicyError,
    PublicationStatus,
    PublishGateDecision,
    RightsStatus,
    VerificationStatus,
    assert_publish_allowed,
    evaluate_publish_gate,
)
from app.modules.review.internal.review_workflow import (
    RecordedReviewDecision,
    ReviewDecisionType,
    ReviewEntityType,
    ReviewSeverity,
    ReviewTaskState,
    ReviewTaskStatus,
    cancel_review_task,
    claim_review_task,
    submit_review_decision,
)

if TYPE_CHECKING:
    from app.modules.review.internal.service import (
        ReviewConflictError,
        ReviewNotFoundError,
        create_review_task,
        decide_review_task,
    )

# The DB services are loaded on first use: the service imports audit, which imports this module's pure core.
_SERVICE_EXPORTS = frozenset({"ReviewConflictError", "ReviewNotFoundError", "create_review_task", "decide_review_task"})


def __getattr__(name: str) -> Any:
    if name in _SERVICE_EXPORTS:
        value = getattr(importlib.import_module("app.modules.review.internal.service"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BRANDING_ALLOWED_RIGHTS_STATUSES",
    "PUBLISHABLE_RIGHTS_STATUSES",
    "GovernancePolicyError",
    "PublicationStatus",
    "PublishGateDecision",
    "RecordedReviewDecision",
    "ReviewConflictError",
    "ReviewDecisionType",
    "ReviewEntityType",
    "ReviewNotFoundError",
    "ReviewSeverity",
    "ReviewTaskState",
    "ReviewTaskStatus",
    "RightsStatus",
    "VerificationStatus",
    "assert_publish_allowed",
    "cancel_review_task",
    "claim_review_task",
    "create_review_task",
    "decide_review_task",
    "evaluate_publish_gate",
    "submit_review_decision",
]
