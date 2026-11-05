import re
import unicodedata


def slugify(value: str, fallback: str = "mon-hoc") -> str:
    """ASCII slug that handles Vietnamese ("Vật lí 11" → "vat-li-11")."""
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:180] or fallback


def like_pattern(term: str) -> str:
    """Escape LIKE wildcards so user input is matched literally (use with escape='\\')."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
