"""Presentation MathML -> LaTeX (the subset textbook sites use: fractions, scripts, roots, over/under marks).

Unknown elements fall back to their children, so the worst case is a plain-text formula, never a crash.
"""

from lxml import etree

# Characters that need a LaTeX command (KaTeX renders the Greek letters directly too, but commands keep
# the source readable and searchable).
SYMBOLS = {
    "π": r"\pi",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "Δ": r"\Delta",
    "ε": r"\varepsilon",
    "ϵ": r"\epsilon",
    "θ": r"\theta",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "φ": r"\varphi",
    "ϕ": r"\phi",
    "ω": r"\omega",
    "Ω": r"\Omega",
    "Φ": r"\Phi",
    "η": r"\eta",
    "ξ": r"\xi",
    "ζ": r"\zeta",
    "χ": r"\chi",
    "ψ": r"\psi",
    "Σ": r"\Sigma",
    "Π": r"\Pi",
    "Λ": r"\Lambda",
    "Γ": r"\Gamma",
    "Θ": r"\Theta",
    "×": r"\times",
    "·": r"\cdot",
    "⋅": r"\cdot",
    "÷": r"\div",
    "±": r"\pm",
    "∓": r"\mp",
    "≤": r"\le",
    "≥": r"\ge",
    "≠": r"\ne",
    "≈": r"\approx",
    "≡": r"\equiv",
    "∼": r"\sim",
    "∝": r"\propto",
    "→": r"\to",
    "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "∞": r"\infty",
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "∂": r"\partial",
    "∇": r"\nabla",
    "√": r"\sqrt{}",
    "°": r"^{\circ}",
    "∘": r"\circ",
    "′": "'",
    "″": "''",
    "…": r"\ldots",
    "⋯": r"\cdots",
    "∈": r"\in",
    "∉": r"\notin",
    "⊂": r"\subset",
    "∪": r"\cup",
    "∩": r"\cap",
    "∅": r"\varnothing",
    "∀": r"\forall",
    "∃": r"\exists",
    "⊥": r"\perp",
    "∥": r"\parallel",
    "∠": r"\angle",
    "△": r"\triangle",
    " ": " ",
    "⁡": "",
    "⁢": "",
    "⁣": "",
    " ": r"\,",
    "−": "-",
    "%": r"\%",
    "#": r"\#",
    "&": r"\&",
    "$": r"\$",
}
ACCENTS = {
    "→": r"\vec",
    "¯": r"\overline",
    "‾": r"\overline",
    "^": r"\hat",
    "ˆ": r"\hat",
    "~": r"\tilde",
    "˙": r"\dot",
    "⏜": r"\overset{\frown}",
    "⌢": r"\overset{\frown}",
}
FUNCTIONS = {"sin", "cos", "tan", "cot", "log", "ln", "exp", "lim", "max", "min", "arcsin", "arccos", "arctan"}


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _symbol(ch: str) -> str:
    cmd = SYMBOLS.get(ch, ch)
    # "\omega" followed by "A" must not become "\omegaA": end letter commands with a space.
    return cmd + " " if cmd.startswith("\\") and cmd[-1].isalpha() else cmd


def _text(value: str) -> str:
    return "".join(_symbol(ch) for ch in value)


def _group(latex: str) -> str:
    latex = latex.strip()
    return latex if len(latex) == 1 else "{" + latex + "}"


def _children(el) -> list:
    return [c for c in el if isinstance(c.tag, str)]


def _convert(el) -> str:
    name = _local(el.tag)
    kids = _children(el)
    k = [_convert(c) for c in kids]
    text = (el.text or "").strip("\n")

    if name in ("math", "mrow", "mstyle", "semantics", "mpadded", "mphantom", "menclose", "merror"):
        return "".join(k)
    if name == "annotation" or name == "annotation-xml":
        return ""
    if name == "mi":
        word = text.strip()
        if word in FUNCTIONS:
            return "\\" + word + " "
        if len(word) > 1 and word.isalpha():
            return r"\mathrm{" + word + "}"
        return _text(word)
    if name == "mn":
        return text.strip().replace(",", "{,}")  # Vietnamese decimal comma without extra spacing
    if name == "mo":
        op = text.strip()
        if op in FUNCTIONS:
            return "\\" + op + " "
        if op in ("(", ")", "[", "]", "|"):
            return op
        if op == "{":
            return r"\{"
        if op == "}":
            return r"\}"
        return _text(op)
    if name == "mtext":
        value = (el.text or "").replace(" ", " ")
        return r"\text{" + value + "}" if value.strip() else " "
    if name == "mspace":
        return r"\,"
    if name == "mfrac":
        if len(k) == 2:
            return r"\dfrac" + "{" + k[0] + "}{" + k[1] + "}"
    if name == "msqrt":
        return r"\sqrt{" + "".join(k) + "}"
    if name == "mroot" and len(k) == 2:
        return r"\sqrt[" + k[1] + "]{" + k[0] + "}"
    if name == "msup" and len(k) == 2:
        return _group(k[0]) + "^" + _group(k[1])
    if name == "msub" and len(k) == 2:
        return _group(k[0]) + "_" + _group(k[1])
    if name == "msubsup" and len(k) == 3:
        return _group(k[0]) + "_" + _group(k[1]) + "^" + _group(k[2])
    if name == "mover" and len(k) == 2:
        mark = "".join(kids[1].itertext()).strip()
        if mark in ACCENTS:
            return ACCENTS[mark] + "{" + k[0] + "}"
        return r"\overset{" + k[1] + "}{" + k[0] + "}"
    if name == "munder" and len(k) == 2:
        return r"\underset{" + k[1] + "}{" + k[0] + "}"
    if name == "munderover" and len(k) == 3:
        return _group(k[0]) + "_" + _group(k[1]) + "^" + _group(k[2])
    if name == "mfenced":
        open_, close = el.get("open", "("), el.get("close", ")")
        sep = el.get("separators", ",")
        inner = (sep[:1] or ",").join(k)
        return r"\left" + (open_ or ".") + inner + r"\right" + (close or ".")
    if name == "mtable":
        rows = [" & ".join(_convert(cell) for cell in _children(row)) for row in kids]
        return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
    if name in ("mtr", "mlabeledtr", "mtd"):
        return " & ".join(k) if name != "mtd" else "".join(k)
    return _text(text) + "".join(k)


def mathml_to_latex(markup: str | bytes) -> str:
    """Convert one <math> element (string) to LaTeX without delimiters."""
    if isinstance(markup, str):
        markup = markup.encode("utf-8")
    root = etree.fromstring(markup, parser=etree.XMLParser(recover=True, resolve_entities=False))
    if root is None:
        return ""
    latex = _convert(root)
    return " ".join(latex.split())
