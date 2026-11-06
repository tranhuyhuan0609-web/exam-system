"""Parser registry and helpers shared by site parsers.

A parser turns one Bronze HTML document into a ParsedPage. Parsers are pure (no I/O) so Bronze pages can
be re-parsed at any time when a parser improves.
"""

import re
from collections.abc import Callable

from lxml import html as lxml_html

from app.pipeline.domain.html_content import to_content
from app.pipeline.domain.models import ParsedPage

Parser = Callable[[str, str], ParsedPage]  # (html, url) -> page
PARSERS: dict[str, Parser] = {}

_IMG = re.compile(r"\[\[img:(\d+)\]\]")


def register(name: str):
    def wrap(fn: Parser) -> Parser:
        PARSERS[name] = fn
        return fn

    return wrap


def get_parser(name: str) -> Parser:
    if name not in PARSERS:
        raise KeyError(f"Unknown parser {name!r}; known: {sorted(PARSERS)}")
    return PARSERS[name]


def document(html: str):
    return lxml_html.fromstring(html)


def text_of(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


class Images:
    """Collects the images of one question across stem, options and solution; placeholders stay unique."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.urls: list[str] = []

    def convert(self, nodes) -> str:
        content = to_content(nodes, self.base_url)
        offset = len(self.urls)
        self.urls += [img.src for img in content.images]
        return _IMG.sub(lambda m: f"[[img:{int(m.group(1)) + offset}]]", content.text)


def letter_choices(texts: list[str], correct: str | None) -> list[dict]:
    letters = "ABCDEFGH"
    return [
        {"id": letters[i].lower(), "content": t, "is_correct": correct is not None and letters[i] == correct}
        for i, t in enumerate(texts)
    ]


def tf_choices(statements: list[str], truth: list[bool | None]) -> list[dict]:
    return [
        {"id": "abcd"[i], "content": s, "is_correct": bool(truth[i]) if i < len(truth) else False}
        for i, s in enumerate(statements)
    ]


def strip_prefix(text: str, pattern: str) -> str:
    return re.sub(pattern, "", text, count=1).strip()


def page_title(doc) -> str:
    titles = doc.xpath("//title/text()")
    return " ".join(titles[0].split()) if titles else ""


def all_parsers() -> dict[str, Parser]:
    from app.pipeline.parsers import (  # noqa: F401  (registers them)
        hoc247,
        loigiaihay,
        opentrivia,
        tuyensinh247,
        vietjack,
    )

    return PARSERS
