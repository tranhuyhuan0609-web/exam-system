"""Strict unpadded base64url helpers shared by the signed-token and signed-cursor code."""

from __future__ import annotations

import base64
import binascii
import re

_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")


class Base64UrlError(ValueError):
    """Raised when a segment is not canonical, unpadded base64url."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode_strict(segment: object, *, max_length: int = 8192) -> bytes:
    """Decode canonical unpadded base64url; reject padding, whitespace, non-ASCII and junk."""
    if not isinstance(segment, str) or not segment or len(segment) > max_length:
        raise Base64UrlError("invalid base64url segment")
    if _B64URL_RE.fullmatch(segment) is None or len(segment) % 4 == 1:
        raise Base64UrlError("invalid base64url segment")
    standard = segment.replace("-", "+").replace("_", "/") + "=" * (-len(segment) % 4)
    try:
        raw = base64.b64decode(standard, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Base64UrlError("invalid base64url segment") from exc
    if b64url_encode(raw) != segment:
        raise Base64UrlError("non-canonical base64url segment")
    return raw
