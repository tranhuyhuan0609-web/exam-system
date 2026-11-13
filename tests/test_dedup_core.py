"""Unit tests for A08 deduplication and content/structural hashing (pure, no conftest)."""

from __future__ import annotations

import itertools
import unicodedata

import pytest

from app.modules.dedup.public import (
    compute_question_hashes,
    content_hash,
    normalize_text,
    structural_hash,
    structural_normalize,
)


def test_chemistry_formulas_distinguished_in_structural_hash() -> None:
    h_sulfuric = structural_hash("Cho dung dịch H2SO4 loãng tác dụng với Fe.")
    h_sulfurous = structural_hash("Cho dung dịch H2SO3 loãng tác dụng với Fe.")
    assert h_sulfuric != h_sulfurous


def test_numeric_variants_collapse_to_same_structural_hash() -> None:
    h1 = structural_hash("Cho m = 10g kim loại tác dụng với H2O dư.", ["10g", "20g", "30g", "40g"])
    h2 = structural_hash("Cho m = 20g kim loại tác dụng với H2O dư.", ["20g", "40g", "60g", "80g"])
    assert h1 == h2


def test_unicode_nfc_and_nfd_produce_identical_hashes() -> None:
    stem_nfc = unicodedata.normalize("NFC", "Tính khối lượng kết tủa thu được khi cho Fe3O4 vào HNO3.")
    stem_nfd = unicodedata.normalize("NFD", stem_nfc)
    assert stem_nfc != stem_nfd

    opts_nfc = [unicodedata.normalize("NFC", o) for o in ("Đáp án A", "Đáp án B", "Đáp án C", "Đáp án D")]
    opts_nfd = [unicodedata.normalize("NFD", o) for o in opts_nfc]

    assert content_hash(stem_nfc, opts_nfc) == content_hash(stem_nfd, opts_nfd)
    assert structural_hash(stem_nfc, opts_nfc) == structural_hash(stem_nfd, opts_nfd)


def test_content_hash_invariant_to_option_permutations() -> None:
    stem = "Công thức phân tử của etanol là gì?"
    options = ["C2H5OH", "CH3OH", "C2H4", "CH3COOH"]
    baseline = content_hash(stem, options)

    for perm in itertools.permutations(options):
        assert content_hash(stem, list(perm)) == baseline

    modified = ["C2H5OH", "CH3OH", "C2H4", "HCOOH"]
    assert content_hash(stem, modified) != baseline


def test_compute_question_hashes_matches_individual_functions() -> None:
    stem = "Hòa tan 11,2g Fe vào dung dịch H2SO4 20%."
    opts = ["0,2 mol", "0,4 mol", "0,6 mol", "0,8 mol"]
    pair = compute_question_hashes(stem, opts)
    assert pair.content_hash == content_hash(stem, opts)
    assert pair.structural_hash == structural_hash(stem, opts)


@pytest.mark.parametrize(
    ("text_a", "text_b", "same_structural"),
    [
        # 1-4: Comma and dot decimals
        ("Đốt cháy 11,2 lít C2H4", "Đốt cháy 22,4 lít C2H4", True),
        ("Nồng độ 0,25M HCl", "Nồng độ 0,5M HCl", True),
        ("Giá trị x = 3.14", "Giá trị x = 6.28", True),
        ("Khối lượng 1,5kg và 2,5kg", "Khối lượng 3,0kg và 4,0kg", True),
        # 5-8: Exponents
        ("Tính x^2 + 3x + 1 = 0", "Tính x^2 + 5x + 7 = 0", True),
        ("Số lượng 10^5 hạt", "Số lượng 10^6 hạt", True),
        ("Giá trị 2^10 bằng bao nhiêu", "Giá trị 2^8 bằng bao nhiêu", True),
        ("Biểu thức e^-2 + 1", "Biểu thức e^-3 + 5", True),
        # 9-12: Percentages
        ("Dung dịch H2SO4 20%", "Dung dịch H2SO4 98%", True),
        ("Hiệu suất đạt 85,5%", "Hiệu suất đạt 72,5%", True),
        ("Tăng 15% rồi giảm 10%", "Tăng 25% rồi giảm 30%", True),
        ("Dung dịch H2SO4 20%", "Dung dịch HNO3 20%", False),
        # 13-18: Chemical formulas with parentheses & brackets
        ("Cho Ca(OH)2 dư vào CO2", "Cho Ca(OH)2 dư vào CO2", True),
        ("Phản ứng giữa Ca(OH)2 và HCl", "Phản ứng giữa Ba(OH)2 và HCl", False),
        ("Muối Al2(SO4)3 khan", "Muối Fe2(SO4)3 khan", False),
        ("Nhiệt phân (NH4)2CO3", "Nhiệt phân (NH4)2SO4", False),
        ("Phức chất [Co(NH3)6]Cl3", "Phức chất [Co(NH3)5Cl]Cl2", False),
        ("Cho 10g Ca(OH)2 tác dụng", "Cho 25g Ca(OH)2 tác dụng", True),
        # 19-24: Ions
        ("Dung dịch chứa ion Fe3+ và Cl-", "Dung dịch chứa ion Fe2+ và Cl-", False),
        ("Nồng độ ion SO4 2- là 0,1M", "Nồng độ ion SO4 2- là 0,5M", True),
        ("Ion SO4^2- trong dung dịch", "Ion SO3^2- trong dung dịch", False),
        ("Khử Cr2O7^2- trong môi trường axit", "Khử MnO4^- trong môi trường axit", False),
        ("Ion NH4+ tác dụng với OH-", "Ion NH4+ tác dụng với OH-", True),
        ("Nồng độ Fe3+ bằng 0,2M", "Nồng độ Fe3+ bằng 0,8M", True),
        # 25-30: Units with exponents / digits attached
        ("Gia tốc 9,8 m/s2", "Gia tốc 10 m/s2", True),
        ("Thể tích 250 cm3", "Thể tích 500 cm3", True),
        ("Vận tốc 20m/s và gia tốc 2m/s2", "Vận tốc 35m/s và gia tốc 5m/s2", True),
        ("Thể tích 1,5 dm3 khí", "Thể tích 3,0 dm3 khí", True),
        ("Cho 10g vào 200ml nước", "Cho 50g vào 500ml nước", True),
        ("Khối lượng riêng 2,7 g/cm3", "Khối lượng riêng 7,8 g/cm3", True),
        # 31-35: Unicode subscripts, math subscripts, dashes, whitespace
        ("Nước H₂O và sắt từ Fe₃O₄", "Nước H2O và sắt từ Fe3O4", True),
        ("Axit H₂SO₄ đặc nóng", "Axit H2SO3 đặc nóng", False),
        ("Cấp số cộng có u_1 = 2 và u_2 = 5", "Cấp số cộng có u_1 = 10 và u_2 = 20", True),
        ("Cấp số cộng có u_1 = 2", "Cấp số cộng có u_3 = 2", False),
        ("Tính −5 + 3 – 2", "Tính -10 + 7 - 4", True),
    ],
)
def test_structural_normalize_35_cases(text_a: str, text_b: str, same_structural: bool) -> None:
    norm_a = structural_normalize(text_a)
    norm_b = structural_normalize(text_b)
    if same_structural:
        assert norm_a == norm_b, f"Expected equal structural form: {norm_a!r} vs {norm_b!r}"
        assert structural_hash(text_a) == structural_hash(text_b)
    else:
        assert norm_a != norm_b, f"Expected distinct structural form: {norm_a!r} vs {norm_b!r}"
        assert structural_hash(text_a) != structural_hash(text_b)


def test_normalize_text_collapses_whitespace_and_case() -> None:
    assert normalize_text("  Cho   H₂O   và\n\tFe₃O₄  ") == "cho h2o và fe3o4"

