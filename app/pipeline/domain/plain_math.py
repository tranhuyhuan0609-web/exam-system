"""Plain-text formulas -> $LaTeX$ without AI ("x = 4cos(3πt + 0,5π) (cm)" -> "$x = 4\\cos(3\\pi t + 0{,}5\\pi)$ (cm)").

Conservative on purpose: only tokens made of ASCII letters, digits, Greek letters and math signs can be part of
a formula, so Vietnamese words (which carry diacritics) are never touched, and a run becomes a formula only
when it has a clear math sign (=, ^, Greek letter, root, comparison, function call...). Units at the end of a run
stay outside it. What this cannot handle is left as it is and still goes to the AI.
"""

import re

GREEK = {
    "π": r"\pi",
    "ω": r"\omega",
    "λ": r"\lambda",
    "φ": r"\varphi",
    "ϕ": r"\varphi",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "Δ": r"\Delta",
    "θ": r"\theta",
    "μ": r"\mu",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "ε": r"\varepsilon",
    "Ω": r"\Omega",
    "τ": r"\tau",
    "η": r"\eta",
    "ν": r"\nu",
    "Φ": r"\Phi",
    "ξ": r"\xi",
}
SIGNS = {
    "≤": r"\le ",
    "≥": r"\ge ",
    "≠": r"\ne ",
    "≈": r"\approx ",
    "±": r"\pm ",
    "×": r"\times ",
    "·": r"\cdot ",
    "→": r"\to ",
    "⇒": r"\Rightarrow ",
    "∞": r"\infty ",
    "°": r"^{\circ}",
    "−": "-",
    "–": "-",
}
FUNCS = ("arcsin", "arccos", "arctan", "sin", "cos", "tan", "cot", "log", "ln", "lim", "max", "min")
UNITS = {
    "cm",
    "mm",
    "m",
    "km",
    "s",
    "ms",
    "h",
    "kg",
    "g",
    "N",
    "J",
    "W",
    "V",
    "A",
    "Hz",
    "kHz",
    "MHz",
    "rad",
    "rad/s",
    "m/s",
    "cm/s",
    "km/h",
    "m/s2",
    "cm/s2",
    "Pa",
    "K",
    "C",
    "eV",
    "MeV",
    "kJ",
    "kW",
    "mA",
    "mV",
    "Ω",
    "μF",
    "F",
    "T",
    "Wb",
    "mol",
    "l",
    "L",
    "dm",
    "dB",
}
_MATH_CHARS = re.compile(r"^[A-Za-z0-9§" + "".join(GREEK) + r"√()\[\]{}+\-−–*/=^_.,;:<>≤≥≠≈±×·→⇒∞°'|!]+$")
_HOLE = re.compile(r"§(\d+)§")  # an existing $...$ formula, set aside while its plain-text neighbours are read
_INLINE = re.compile(r"(?<!\$)\$(?!\$)([^$]+?)\$(?!\$)")
_STRONG = re.compile(r"[=^√≤≥≠≈±×→⇒" + "".join(GREEK) + r"]|\b(?:" + "|".join(FUNCS) + r")\s*\(|\d[a-zA-Z]|[a-zA-Z]\d")
_SEGMENT = re.compile(r"(\$\$.*?\$\$|\\\(.*?\\\)|\\\[.*?\\\])", re.S)  # display / \( \) formulas stay as they are


def _unit(token: str) -> bool:
    bare = re.sub(r"\^\{?(\d)\}?", r"\1", token.strip("()[].,;:"))  # "cm/s^{2}" -> "cm/s2"
    return bare in UNITS


def _word(token: str) -> bool:
    """An ASCII word such as "Khi", "cho", "theo": prose, not a variable (those are 1-2 letters)."""
    if "[[img:" in token:  # image placeholders are never part of a formula
        return True
    bare = token.strip("()[].,;:!?")
    return bare.isalpha() and len(bare) > 2 and bare.lower() not in FUNCS and bare not in UNITS


def _latex(run: str) -> str:
    out = run
    for f in FUNCS:  # "4cos(" -> "4\cos(", not inside a longer word (arcsin handled first)
        out = re.sub(rf"(?<![\\a-zA-Z]){f}(?=\s*[\(a-zA-Z0-9" + "".join(GREEK) + "])", "\\\\" + f + " ", out)
    out = re.sub(r"√\(([^()]*)\)", r"\\sqrt{\1}", out)
    out = re.sub(r"√([A-Za-z0-9" + "".join(GREEK) + r"]+)", r"\\sqrt{\1}", out)
    out = re.sub(r"\^\(([^()]*)\)", r"^{\1}", out)
    out = re.sub(r"(\d),(\d)", r"\1{,}\2", out)  # Vietnamese decimal comma
    out = "".join(GREEK.get(ch, SIGNS.get(ch, ch)) + (" " if ch in GREEK else "") for ch in out)
    out = re.sub(r"\\([a-zA-Z]+) (?=[^a-zA-Z])", r"\\\1", out)  # drop the guard space where it is not needed
    return " ".join(out.split())


def _fold_scripts(text: str) -> str:
    """ "π$^{2}$" (from <sup>) -> "π^{2}" so the run detector sees one token."""
    return re.sub(r"(?<=\S)\$([\^_])\{([^{}$]*)\}\$", r"\1{\2}", text)


def _unfold(text: str) -> str:
    """Scripts folded for detection but left outside a formula go back to "$^{2}$"."""
    return re.sub(r"([\^_])\{([^{}$]*)\}", r"$\1{\2}$", text)


def _convert_plain(text: str, holes: list[str] | None = None) -> str:
    """`holes` are the LaTeX bodies of $...$ formulas replaced by §n§ in `text`: a run such as "P = sin(§0§)"
    becomes ONE formula "$P = \\sin(\\alpha)$" (two adjacent $...$ would read as display math $$)."""
    holes = holes or []
    tokens = re.split(r"(\s+)", text)
    out: list[str] = []
    run: list[str] = []

    def restore(value: str) -> str:
        return _HOLE.sub(lambda m: "$" + holes[int(m.group(1))] + "$", value)

    def flush():
        if not run:
            return
        words = [t for t in run if not t.isspace()]
        # keep trailing units / punctuation outside the formula
        tail: list[str] = []
        while words and (_unit(words[-1]) or words[-1] in ".,;:") and len(words) > 1:
            tail.insert(0, words.pop())
        body = " ".join(words)
        trail = ""
        m = re.search(r"[.,;:]+$", body)  # sentence punctuation stays outside
        if m:
            body, trail = body[: m.start()], m.group(0)
        plain = _HOLE.sub("", body)
        merge = bool(_HOLE.search(body)) and bool(re.search(r"[A-Za-z0-9" + "".join(GREEK) + r"]", plain))
        if words and ((_STRONG.search(plain) and (len(plain) > 1 or plain in GREEK)) or merge):
            latex = _HOLE.sub(lambda h: " " + holes[int(h.group(1))] + " ", _latex(body))
            out.append(
                "$" + " ".join(latex.split()) + "$" + trail + (" " + restore(_unfold(" ".join(tail))) if tail else "")
            )
        else:
            out.append(restore(_unfold("".join(run))))
        run.clear()

    for token in tokens:
        if token.isspace():
            if run:
                run.append(token)
            else:
                out.append(token)
            continue
        if _MATH_CHARS.match(token) and not _word(token):
            run.append(token)
        else:
            if run and run[-1].isspace():
                space = run.pop()
                flush()
                out.append(space)
            else:
                flush()
            out.append(restore(token))
    if run and run[-1].isspace():
        space = run.pop()
        flush()
        out.append(space)
    flush()
    return "".join(out)


def normalize_plain_math(text: str) -> str:
    """Wrap and convert plain-text formulas outside existing $...$ / \\(...\\) segments."""
    if not text:
        return text
    lines = []
    for line in text.split("\n"):  # a formula never spans lines
        parts = _SEGMENT.split(_fold_scripts(line))
        done = []
        for i, part in enumerate(parts):
            if i % 2:  # $$...$$ and \(...\) stay as they are
                done.append(part)
                continue
            holes: list[str] = []

            def hole(m, holes=holes):
                holes.append(m.group(1))
                return f"§{len(holes) - 1}§"

            done.append(_convert_plain(_INLINE.sub(hole, part), holes))
        lines.append("".join(done))
    return "\n".join(lines)
