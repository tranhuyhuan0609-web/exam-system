"""Plain-text formulas converted to LaTeX without AI; Vietnamese prose is never touched."""

import pytest

from app.pipeline.domain.plain_math import normalize_plain_math
from app.pipeline.use_cases.silver import has_plain_math


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Một vật dao động theo phương trình x = 4cos(3πt + 0,5π) (cm).",
            r"Một vật dao động theo phương trình $x = 4\cos(3\pi t + 0{,}5\pi)$ (cm).",
        ),
        (
            "Lấy π$^{2}$ = 10. Gia tốc cực đại là 360 (cm/s$^{2}$)",
            r"Lấy $\pi^{2} = 10$. Gia tốc cực đại là 360 (cm/s$^{2}$)",
        ),
        ("T = 2π/ω = 1 s.", r"$T = 2\pi/\omega = 1$ s."),
        ("Cho hàm số f(x) = tan 2x - 1.", r"Cho hàm số $f(x) = \tan 2x - 1$."),
        ("Vật có khối lượng m = 100 g và A = 4 cm.", "Vật có khối lượng $m = 100$ g và $A = 4$ cm."),
        ("Chọn đáp án đúng về pha ban đầu φ.", r"Chọn đáp án đúng về pha ban đầu $\varphi$."),
        ("Khi x ≥ 0 thì √x xác định.", r"Khi $x \ge 0$ thì $\sqrt{x}$ xác định."),
        (
            r"Biết $\sin a = \frac{8}{17}$ và a, b là các góc nhọn.",
            r"Biết $\sin a = \frac{8}{17}$ và a, b là các góc nhọn.",
        ),
        ("Câu 1: Tìm phát biểu đúng.", "Câu 1: Tìm phát biểu đúng."),
        ("Độ lớn vận tốc cực đại là 12π m/s", r"Độ lớn vận tốc cực đại là $12\pi$ m/s"),
    ],
)
def test_normalize_plain_math(text, expected):
    out = normalize_plain_math(text)
    assert out == expected
    assert not has_plain_math(out)
