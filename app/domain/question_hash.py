"""Content fingerprint used to detect exact duplicates across manual entry, file import, AI and crawlers.

Two questions get the same hash when they have the same type, text and options after normalisation
(Unicode NFC, case, whitespace, trailing punctuation). The answer key and explanation are ignored, so a
re-typed copy of an existing question is still caught. Semantic (near-duplicate) matching with
pgvector comes later.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable

_SPACES = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").replace("\u00a0", " ").casefold()
    text = _SPACES.sub("", text)  # spacing inside LaTeX and between words is irrelevant
    return text.rstrip(".:;,?!")


def content_hash(question_type: str, content: str, choices: Iterable[str] = ()) -> str:
    parts = [str(question_type), normalize_text(content), *(normalize_text(c) for c in choices)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def hash_of_choices(question_type: str, content: str, choices: Iterable[dict]) -> str:
    """Same as content_hash, for stored JSONB choices ({"content": ...})."""
    return content_hash(question_type, content, (c.get("content", "") for c in choices))
