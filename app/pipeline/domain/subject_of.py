"""Subject, grade and textbook series of a page, read from its URL or title ("trac-nghiem-toan-8-…-chan-troi-sang-tao",
"Trắc nghiệm Bài 35 … Hóa 12").

A crawl that starts from one subject's seed may follow links into other subjects; the table-of-contents context it
carries then names the seed's subject. When the page itself says otherwise, the page wins.
"""

import re
import unicodedata

# URL slug -> our subject name (roots are "<subject> <grade>", e.g. "Hóa học 10")
_SLUGS = {
    "toan": "Toán",
    "vat-li": "Vật lí",
    "vat-ly": "Vật lí",
    "li": "Vật lí",
    "ly": "Vật lí",
    "hoa": "Hóa học",
    "hoa-hoc": "Hóa học",
    "sinh": "Sinh học",
    "sinh-hoc": "Sinh học",
    "khtn": "Khoa học tự nhiên",
    "tieng-viet": "Tiếng Việt",
    "tieng-anh": "Tiếng Anh",
    "su": "Lịch sử",
    "lich-su": "Lịch sử",
    "dia": "Địa lí",
    "dia-li": "Địa lí",
    "van": "Ngữ văn",
    "ngu-van": "Ngữ văn",
    "tin": "Tin học",
    "tin-hoc": "Tin học",
    "cong-nghe": "Công nghệ",
    "gdcd": "GDCD",
}
_URL = re.compile(r"(?:^|[/-])(" + "|".join(sorted(_SLUGS, key=len, reverse=True)) + r")-(\d{1,2})(?=-|\.|$)")
# Words in titles (accents removed, lower case) -> subject
_WORDS = {
    "toan": "Toán",
    "vat li": "Vật lí",
    "vat ly": "Vật lí",
    "li": "Vật lí",
    "ly": "Vật lí",
    "hoa hoc": "Hóa học",
    "hoa": "Hóa học",
    "sinh hoc": "Sinh học",
    "sinh": "Sinh học",
    "khtn": "Khoa học tự nhiên",
    "khoa hoc tu nhien": "Khoa học tự nhiên",
    "tieng viet": "Tiếng Việt",
    "tieng anh": "Tiếng Anh",
    "lich su": "Lịch sử",
    "su": "Lịch sử",
    "dia li": "Địa lí",
    "dia ly": "Địa lí",
    "dia": "Địa lí",
    "ngu van": "Ngữ văn",
    "van": "Ngữ văn",
    "tin hoc": "Tin học",
    "cong nghe": "Công nghệ",
}
_TITLE = re.compile(r"\b(" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")\s+(\d{1,2})\b")
_SERIES = {
    "ket-noi-tri-thuc": "Kết nối tri thức",
    "ket noi tri thuc": "Kết nối tri thức",
    "chan-troi-sang-tao": "Chân trời sáng tạo",
    "chan troi sang tao": "Chân trời sáng tạo",
    "canh-dieu": "Cánh diều",
    "canh dieu": "Cánh diều",
}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def subject_of(url: str, title: str = "") -> tuple[str, str] | None:
    """("Hóa học 12", series or "") when the URL or the title names a subject and grade (1-12)."""
    found = None
    if m := _URL.search(url.lower()):
        found = (_SLUGS[m.group(1)], int(m.group(2)))
    elif m := _TITLE.search(_plain(title)):
        found = (_WORDS[m.group(1)], int(m.group(2)))
    if not found or not 1 <= found[1] <= 12:
        return None
    haystack = url.lower() + " " + _plain(title)
    series = next((name for key, name in _SERIES.items() if key in haystack), "")
    return f"{found[0]} {found[1]}", series


def _same_root(a: str, b: str) -> bool:
    return _plain(a).replace("hoa hoc", "hoa").replace("sinh hoc", "sinh") == _plain(b).replace(
        "hoa hoc", "hoa"
    ).replace("sinh hoc", "sinh")


def corrected_context(context: dict | None, url: str, title: str = "") -> dict:
    """The context, with root (and series) taken from the page when it names another subject or grade."""
    context = dict(context or {})
    found = subject_of(url, title)
    if not found or not context.get("root") or _same_root(context["root"], found[0]):
        return context
    root, series = found
    context["root"] = root
    context["series"] = series  # the seed's series belonged to the seed's subject
    return context
