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
_NL_COMMAND = re.compile(r"\n(?=(?:eq|abla|u|ot|eg|i|ewline|leq|geq|parallel|mid|subset|exists|ormalsize)(?![a-z]))")
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
    r"subseteq|supseteq|exists|cong|sim|leftarrow|rightarrow|Leftarrow|Rightarrow|leftrightarrow)(?![a-zA-Z]))"
)


def literal_newlines(text: str | None) -> str | None:
    return _LITERAL_NEWLINE.sub("\n", text) if text else text


def repair_latex(text: str | None) -> str | None:
    if not text:
        return text
    text = _CONTROL_RE.sub(lambda m: _CONTROL[m.group()], text)
    text = literal_newlines(text)
    return _MATH.sub(lambda m: _repair_math(m.group()), text)


def needs_repair(text: str | None) -> bool:
    return bool(text) and repair_latex(text) != text
