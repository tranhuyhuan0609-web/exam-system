"""Database parity test verifying `fold_for_search` == PostgreSQL `lower(f_unaccent(...))` (P21)."""

from __future__ import annotations

import unicodedata

from sqlalchemy import text

from app.modules.search.public import fold_for_search

# Every Vietnamese accented vowel group + đ/Đ across upper & lower case + math symbols.
_VIETNAMESE_VOWEL_CHARS = (
    "aáàảãạăắằẳẵặâấầẩẫậ"
    "eéèẻẽẹêếềểễệ"
    "iíìỉĩị"
    "oóòỏõọôốồổỗộơớờởỡợ"
    "uúùủũụưứừửữự"
    "yýỳỷỹỵ"
    "đ"
    "AÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬ"
    "EÉÈẺẼẸÊẾỀỂỄỆ"
    "IÍÌỈĨỊ"
    "OÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢ"
    "UÚÙỦŨỤƯỨỪỬỮỰ"
    "YÝỲỶỸỴ"
    "Đ"
)

_MATH_PHRASES = (
    "x² + y² = r²",
    "√(a² + b²) ≥ 0",
    "f(x) ≠ g(x)",
    "a ± b × c ÷ d − e",
    "∫ f(x) dx ≈ 3.14",
    "α + β ≤ π − δ",
    "lim x→∞ (1 + 1/x)^x",
    "∀x ∈ ℝ, ∃n ∈ ℕ: x ≤ n",
    "Tập số nguyên ℤ và số hữu tỉ ℚ",
    "Đoạn thẳng – Góc — Tia",
    "Phân số ½ + ¼ = ¾",
)


def _build_300_plus_test_corpus() -> list[str]:
    samples: list[str] = []

    # 1. Every individual Vietnamese vowel character in BOTH NFC and NFD (2 * 134 = 268 cases)
    for ch in _VIETNAMESE_VOWEL_CHARS:
        samples.append(f"từ {ch} mẫu")
        samples.append(unicodedata.normalize("NFD", f"từ {ch} mẫu"))

    # 2. Combined realistic Vietnamese exam phrases in NFC and NFD + math symbols (> 60 cases)
    domain_phrases = [
        "Công thức Bayes và xác suất có điều kiện",
        "Dao động tắt dần, cưỡng bức và cộng hưởng",
        "Đường tròn lượng giác trên mặt phẳng tọa độ",
        "Hàm số mũ và hàm số lôgarit lớp 12",
        "Phương trình vi phân cấp 1 — Ứng dụng thực tế",
        "Khảo sát sự biến thiên của đồ thị hàm số",
        "Phản ứng oxi hóa – khử và điện phân dung dịch",
        "Cấu trúc di truyền quần thể và quy luật Menđen",
        "Đề kiểm tra giữa kỳ I năm học 2025–2026",
        "Hướng dẫn chấm chi tiết câu trả lời ngắn",
    ]
    for phrase in domain_phrases:
        samples.append(phrase)
        samples.append(unicodedata.normalize("NFD", phrase))

    for idx, math_p in enumerate(_MATH_PHRASES):
        samples.append(f"Bài {idx + 1}: {math_p}")
        samples.append(unicodedata.normalize("NFD", f"Bài {idx + 1}: {math_p}"))

    # 3. Whitespace variations
    samples.extend(
        [
            "  Công   thức   Bayes  ",
            "\tĐạo\nhàm\rcấp  hai  ",
            "x²  +  y²   ≤  √2",
        ]
    )
    assert len(samples) >= 300
    return samples


def test_fold_for_search_exact_parity_with_postgres_f_unaccent_over_300_strings(db):
    corpus = _build_300_plus_test_corpus()
    assert len(corpus) >= 300

    async def _fetch(s):
        res = await s.execute(
            text(
                "SELECT idx, btrim(regexp_replace(lower(f_unaccent(txt)), E'\\\\s+', ' ', 'g')) AS pg_folded "
                "FROM unnest(CAST(:texts AS text[])) WITH ORDINALITY AS t(txt, idx) ORDER BY idx"
            ),
            {"texts": corpus},
        )
        return res.all()

    rows = db(_fetch)

    assert len(rows) == len(corpus)
    mismatches: list[tuple[int, str, str, str]] = []
    for idx, pg_folded in rows:
        raw_input = corpus[idx - 1]
        py_folded = fold_for_search(raw_input)
        if py_folded != pg_folded:
            mismatches.append((idx - 1, raw_input, py_folded, pg_folded))

    assert not mismatches, f"Mismatches vs PostgreSQL f_unaccent + lower: {mismatches[:5]}"
