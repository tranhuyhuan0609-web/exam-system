"""HMAC-SHA256 email blind-index helpers (modules F04/G02). Standard library only."""

from __future__ import annotations

import hashlib
import hmac
import unicodedata

MIN_PEPPER_BYTES = 32


def validate_pepper(cluster_pepper: bytes) -> bytes:
    """Validate that the HMAC pepper is non-empty bytes of at least 32 bytes."""
    if not isinstance(cluster_pepper, (bytes, bytearray)) or len(cluster_pepper) < MIN_PEPPER_BYTES:
        raise ValueError(f"cluster_pepper must be at least {MIN_PEPPER_BYTES} bytes")
    return bytes(cluster_pepper)


def normalize_email_for_blind_index(email: str) -> str:
    """Normalize email via NFKC, remove all whitespace, and lowercase."""
    if not isinstance(email, str):
        raise TypeError("email must be a str")
    nfkc = unicodedata.normalize("NFKC", email)
    no_whitespace = "".join(nfkc.split())
    return no_whitespace.lower()


def compute_email_blind_index(email: str, cluster_pepper: bytes) -> str:
    """Compute a keyed HMAC-SHA256 hex digest over the normalized email address."""
    key = validate_pepper(cluster_pepper)
    normalized = normalize_email_for_blind_index(email)
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_email_blind_index(email: str, expected_digest: str, cluster_pepper: bytes) -> bool:
    """Constant-time verification of an email against a stored blind index digest."""
    computed = compute_email_blind_index(email, cluster_pepper)
    return hmac.compare_digest(computed, expected_digest)
