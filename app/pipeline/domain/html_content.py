"""HTML fragment -> question text in the app's format: plain text lines with $LaTeX$ for formulas.

MathML becomes LaTeX, <sup>/<sub> become ^{}/_{}, images become [[img:N]] placeholders (the caller downloads
figures, or sends formula images to the AI), ads and scripts are dropped.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from lxml import etree, html

from app.pipeline.domain.mathml import mathml_to_latex

BLOCK_TAGS = {
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "tr",
    "table",
    "tbody",
    "section",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "center",
}
SKIP_TAGS = {"script", "style", "ins", "noscript", "iframe", "button", "svg", "form", "input"}
SKIP_CLASSES = ("ads_", "adsbygoogle", "ads-", "advert")
_SPACES = re.compile(r"[ \t\r\f\v ]+")


@dataclass
class ImageRef:
    src: str
    alt: str = ""
    width: int | None = None
    height: int | None = None


@dataclass
class Content:
    text: str
    images: list[ImageRef] = field(default_factory=list)


def _skip(el) -> bool:
    if not isinstance(el.tag, str):
        return True
    tag = etree.QName(el.tag).localname.lower()
    if tag in SKIP_TAGS:
        return True
    cls = el.get("class") or ""
    return any(marker in cls for marker in SKIP_CLASSES)


def _int(value) -> int | None:
    try:
        return int(str(value).rstrip("px"))
    except (TypeError, ValueError):
        return None


class _Writer:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.parts: list[str] = []
        self.images: list[ImageRef] = []

    def text(self, value: str | None) -> None:
        if value:
            self.parts.append(_SPACES.sub(" ", value.replace("\n", " ")))

    def newline(self) -> None:
        self.parts.append("\n")

    def walk(self, el) -> None:
        if _skip(el):
            self.text(el.tail)
            return
        tag = etree.QName(el.tag).localname.lower()
        if tag == "math":
            latex = mathml_to_latex(etree.tostring(el, encoding="unicode", with_tail=False))
            if latex:
                display = el.get("display") == "block"
                self.parts.append(f" $${latex}$$ " if display else f"${latex}$")
            self.text(el.tail)
            return
        if tag in ("sup", "sub"):
            inner = " ".join("".join(el.itertext()).split())
            if inner:
                script = _latex_escape(inner)
                if re.search(r"[^\x00-\x7f]", inner) and not re.search(r"[α-ωΑ-Ω]", inner):
                    script = r"\text{" + script + "}"  # "W_{tỏa}": Vietnamese words stay upright text
                self.parts.append(("$^{" if tag == "sup" else "$_{") + script + "}$")
            self.text(el.tail)
            return
        if tag == "img":
            src = el.get("data-src") or el.get("src") or ""
            latex = latex_alt(el.get("alt") or "")
            if latex:  # formula image whose alt text is its LaTeX source (MathType exports): no AI needed
                self.parts.append(f"${latex}$")
            elif src and not src.startswith("data:"):
                self.images.append(
                    ImageRef(
                        urljoin(self.base_url, src), el.get("alt") or "", _int(el.get("width")), _int(el.get("height"))
                    )
                )
                self.parts.append(f"[[img:{len(self.images) - 1}]]")
            self.text(el.tail)
            return
        if tag == "br":
            self.newline()
            self.text(el.tail)
            return
        block = tag in BLOCK_TAGS
        if block:
            self.newline()
        self.text(el.text)
        for i, child in enumerate(el):
            if tag == "tr" and i:
                self.parts.append(" | ")
            self.walk(child)
        if block:
            self.newline()
        self.text(el.tail)

    def result(self) -> Content:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.split("\n")]
        text = "\n".join(line for line in lines if line)
        return Content(text, self.images)


_LATEX_HINT = re.compile(r"\\[a-zA-Z]+|[\^_]\{|[\^_][0-9a-zA-Z]")


def latex_alt(alt: str) -> str | None:
    """The alt text when it is LaTeX (e.g. "_{1}^{2}\textrm{D} + n"), not a caption like "hình 1"."""
    alt = " ".join(alt.split())
    if not alt or len(alt) > 400 or not _LATEX_HINT.search(alt):
        return None
    if re.search(r"[À-ỹ]{2,}", alt) and r"\text" not in alt:  # Vietnamese words: a caption, not a formula
        return None
    return alt.strip("$ ")


def _latex_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("%", r"\%").replace("&", r"\&")


def to_content(nodes, base_url: str = "") -> Content:
    """Convert lxml elements (or HTML strings) to question text; children only, the wrappers are not output."""
    writer = _Writer(base_url)
    for node in nodes if isinstance(nodes, list | tuple) else [nodes]:
        if isinstance(node, str):
            node = html.fragment_fromstring(node, create_parent="div")
        writer.walk(node)
    return writer.result()


def element(fragment: str):
    return html.fragment_fromstring(fragment, create_parent="div")
