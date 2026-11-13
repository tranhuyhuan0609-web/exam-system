"""Shared Vietnamese text-folding primitives (std-lib only leaf contract)."""

from __future__ import annotations

import re
import unicodedata

_SEPARATORS_RE = re.compile(r"[^0-9a-z]+")


def strip_vietnamese_diacritics(raw: str) -> str:
    """Fold đ/Đ -> d/D and strip Mn combining marks cleanly without altering composed symbols like ≠."""
    text = (raw or "").replace("đ", "d").replace("Đ", "D")
    out: list[str] = []
    for ch in text:
        if unicodedata.category(ch).startswith("L"):
            out.append("".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn"))
        elif unicodedata.category(ch) != "Mn":
            out.append(ch)
    return "".join(out)


def fold_vietnamese_ascii(raw: str) -> str:
    """Fold NFC/NFD Vietnamese text and separators into space-delimited ASCII tokens."""
    stripped = strip_vietnamese_diacritics(raw)
    lowered = stripped.casefold()
    return _SEPARATORS_RE.sub(" ", lowered).strip()
