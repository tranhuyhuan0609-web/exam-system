r"""Repair LaTeX that was damaged by JSON string escaping.

Models sometimes write a single backslash inside JSON strings, e.g. "\text{C}". A JSON decoder then reads
"\t" as TAB, "\f" as form feed, "\b" as backspace, "\r" as carriage return and "\n" as a newline, so
"\textcolor" becomes TAB + "extcolor" and "\frac" becomes FF + "rac". This module restores them, and
removes colour wrappers that only add noise (\textcolor{black}{X} -> X).
"""

import re

# Control characters that only appear in our text when a LaTeX command lost its backslash.
_CONTROL = {"\t": r"\t", "\x0c": r"\f", "\x08": r"\b", "\x0b": r"\v"}
_CONTROL_RE = re.compile("[\t\x0c\x08\x0b]")
# "\r" and "\n" are also legitimate line breaks, so only repair them when a LaTeX command follows.
_CR_COMMAND = re.compile(r"\r(?=(?:ho|ight|angle|m|ceil|floor|brace|brack|Rightarrow|ightarrow)\b|ight)")
_NL_COMMAND = re.compile(
    r"\n(?=(?:eq|abla|u|ot|eg|i|ewline|leq|geq|parallel|mid|subset|exists|ormalsize|earrow|warrow)(?![a-z]))"
)
# Arrows of a variation table (\nearrow, \nwarrow) are often outside $...$, and "earrow" starts no word
_NL_ARROW = re.compile(r"\n(?=[ew]arrow\b)")
_MATH = re.compile(r"\$\$.*?\$\$|\$[^$]*\$", re.S)
_COLOR_WRAPPER = re.compile(r"\\(?:textcolor|textColor|color)\{[^{}]*\}\{((?:[^{}]|\{[^{}]*\})*)\}")
_COLOR_SWITCH = re.compile(r"\\color\{[^{}]*\}\s*")


def _repair_math(segment: str) -> str:
    segment = _CR_COMMAND.sub(r"\\r", segment)
    segment = _NL_COMMAND.sub(r"\\n", segment)
    segment = _COLOR_WRAPPER.sub(r"\1", segment)
    return _COLOR_SWITCH.sub("", segment)


# A line break written as the two characters "\n" (JSON escaped twice by a model): "…cm$.\nĐộ lệch pha …".
# LaTeX commands starting with "n" are kept: \ne, \neq, \nu, \nabla, \neg, \ni, \notin, \newline, …
_LITERAL_NEWLINE = re.compile(
    r"\\n(?!(?:e|eq|u|abla|eg|i|mid|leq|geq|less|gtr|ot|otin|ewline|olimits|onumber|parallel|"
    r"subseteq|supseteq|exists|cong|sim|leftarrow|rightarrow|Leftarrow|Rightarrow|leftrightarrow|"
    r"earrow|warrow)(?![a-zA-Z]))"
)


def literal_newlines(text: str | None) -> str | None:
    return _LITERAL_NEWLINE.sub("\n", text) if text else text


def repair_latex(text: str | None) -> str | None:
    if not text:
        return text
    text = _CONTROL_RE.sub(lambda m: _CONTROL[m.group()], text)
    text = _NL_ARROW.sub(r"\\n", text)
    text = literal_newlines(text)
    return _MATH.sub(lambda m: _repair_math(m.group()), text)


_CENTER = re.compile(r"\\(?:begin|end)\{center\}")
_TABULAR = re.compile(r"\\begin\{tabular\}(?:\{(?:[^{}]|\{[^{}]*\})*\})?(.*?)\\end\{tabular\}", re.S)
_MATHY = re.compile(r"\\[a-zA-Z]+|[\^_]|'|\b[a-z]\(")


def _cell(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return " "  # an empty cell keeps its column (a line may start with one)
    if text.strip("|") == "":
        return "‖"  # the double bar of a variation table (not defined there): not a column separator
    if "$" in text or not _MATHY.search(text):
        return text
    return f"${text}$"  # -\infty, f'(x), \nearrow: typeset by KaTeX inside the table cell


def latex_tables(text: str | None, drop: bool = False) -> str | None:
    """\\begin{tabular} written by a model (a variation table, a data table) as "a | b" lines, which the site and
    the printed papers draw as tables; `drop` removes it (the table is already in the question's figure)."""
    if not text or "tabular" not in text:
        return text

    def rows(m: re.Match) -> str:
        if drop:
            return "\n"
        lines = []
        for row in re.split(r"\\\\", m.group(1)):
            row = re.sub(r"\\(?:hline|cline\{[^}]*\})", "", row)
            cells = [_cell(c) for c in re.split(r"(?<!\\)&", row)]
            if any(c.strip() for c in cells):
                lines.append(" | ".join(cells))
        return "\n" + "\n".join(lines) + "\n"

    text = _TABULAR.sub(rows, _CENTER.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]*\n[ \t]*", "\n", text)).strip()


def needs_repair(text: str | None) -> bool:
    return bool(text) and repair_latex(text) != text
