"""Public facade of the privacy module (F04/G02)."""

from app.modules.privacy.internal.audit_sanitizer import (
    DEFAULT_MAX_AUDIT_DEPTH,
    SENSITIVE_AUDIT_KEYS,
    sanitize_audit_json,
)
from app.modules.privacy.internal.blind_index import (
    MIN_PEPPER_BYTES,
    compute_email_blind_index,
    normalize_email_for_blind_index,
    verify_email_blind_index,
)
from app.modules.privacy.internal.scrub import (
    REDACTED_TEXT_MARKER,
    ScrubCounts,
    ScrubResult,
    scrub_free_text_pii_records,
)

__all__ = [
    "DEFAULT_MAX_AUDIT_DEPTH",
    "MIN_PEPPER_BYTES",
    "REDACTED_TEXT_MARKER",
    "SENSITIVE_AUDIT_KEYS",
    "ScrubCounts",
    "ScrubResult",
    "compute_email_blind_index",
    "normalize_email_for_blind_index",
    "sanitize_audit_json",
    "scrub_free_text_pii_records",
    "verify_email_blind_index",
]
