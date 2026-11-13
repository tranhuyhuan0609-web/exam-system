"""Public facade of the exam delivery module (C05)."""

from app.modules.delivery.internal.sanitizer import sanitize_exam_paper

__all__ = ["sanitize_exam_paper"]
