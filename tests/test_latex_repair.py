import json

import pytest

from app.domain.latex import needs_repair, repair_latex


def test_repairs_the_real_broken_question():
    # Exactly what a JSON decoder made of "\textColor{black}{^\text{o}}\text{C}" (single backslashes).
    broken = "nhiệt độ $20\textColor{black}{^\text{o}}\text{C}$. Thêm $6$ ca"
    assert "\t" in broken
    assert repair_latex(broken) == r"nhiệt độ $20^\text{o}\text{C}$. Thêm $6$ ca"


@pytest.mark.parametrize(
    ("model_wrote", "expected"),
    [
        (r"$\frac{1}{2}$", r"$\frac{1}{2}$"),
        (r"$\beta = \theta$", r"$\beta = \theta$"),
        (r"$\vec{v}$", r"$\vec{v}$"),
        (r"$\left( \right)$", r"$\left( \right)$"),
        (r"$a \neq b$, $\nabla f$", r"$a \neq b$, $\nabla f$"),
        (r"$\times 10^5$", r"$\times 10^5$"),
        (r"$\color{red} x$", "$x$"),
    ],
)
def test_round_trip_through_lenient_json(model_wrote, expected):
    # Simulate the model emitting single backslashes inside a JSON string.
    raw = '{"s": "' + model_wrote.replace('"', '\\"') + '"}'
    try:
        decoded = json.loads(raw)["s"]
    except json.JSONDecodeError:  # e.g. "\c" or "\l" are invalid escapes; providers then keep them as-is
        decoded = model_wrote
    assert repair_latex(decoded) == expected


def test_real_line_breaks_and_clean_text_are_untouched():
    text = "Dòng 1\nDòng 2: $v = \\omega A$\nnhiệt độ tăng"
    assert repair_latex(text) == text
    assert not needs_repair(text)
    assert repair_latex(None) is None and repair_latex("") == ""
