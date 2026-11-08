"""Cross-Curriculum Auto-Mapper:
Maps topics, chapters, and lessons from other textbooks (Cánh Diều, Chân Trời Sáng Tạo, GDPT 2006)
into the single canonical SGK Kết nối tri thức (KNTT) curriculum tree.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

# Standard topic normalization rules for matching between textbooks
TOPIC_SYNONYMS: list[tuple[re.Pattern, str]] = [
    # Math
    (re.compile(r"hàm số lượng giác|phương trình lượng giác|lượng giác", re.I), "luong_giac"),
    (re.compile(r"cấp số cộng|cấp số nhân|dãy số", re.I), "day_so_cap_so"),
    (re.compile(r"mẫu số liệu ghép nhóm|thống kê|đo xu thế trung tâm", re.I), "thong_ke_ghep_nhom"),
    (re.compile(r"đạo hàm|tiếp tuyến", re.I), "dao_ham"),
    (re.compile(r"nguyên hàm|tích phân", re.I), "nguyen_ham_tich_phan"),
    (re.compile(r"tọa độ không gian|hình giải tích|vectơ trong không gian|o xyz", re.I), "toa_do_khong_gian"),
    (re.compile(r"số có nhiều chữ số", re.I), "so_co_nhieu_chu_so"),
    (re.compile(r"phép cộng và phép trừ|phép cộng|phép trừ", re.I), "cong_tru"),
    (re.compile(r"phép nhân và phép chia|phép nhân|phép chia", re.I), "nhan_chia"),
    (re.compile(r"đơn vị đo đại lượng|đại lượng", re.I), "don_vi_do"),
    (re.compile(r"đường vuông góc|đường thẳng song song|hình học", re.I), "duong_vuong_goc_song_song"),
    (re.compile(r"phân số", re.I), "phan_so"),
    (re.compile(r"xác suất|thống kê", re.I), "xac_suat"),
    (re.compile(r"phương trình bậc nhất|hàm số bậc nhất", re.I), "pt_bac_nhat"),
    (re.compile(r"phân thức đại số|biểu thức đại số|đa thức", re.I), "phan_thuc_dai_so"),
    (re.compile(r"tứ giác|định lí thales|tam giác đồng dạng|hình khối", re.I), "hinh_hoc_thcs"),
    # Physics
    (re.compile(r"mô tả chuyển động|động học|chuyển động thẳng|chuyển động biến đổi", re.I), "dong_hoc"),
    (re.compile(r"lực và chuyển động|động lực học|định luật newton|moment lực|cân bằng", re.I), "dong_luc_hoc"),
    (re.compile(r"năng lượng|công|công suất|cơ năng", re.I), "nang_luong"),
    (re.compile(r"động lượng|xung lượng|va chạm", re.I), "dong_luong"),
    (re.compile(r"chuyển động tròn|chuyển động tròn đều|lực hướng tâm", re.I), "chuyen_dong_tron"),
    (re.compile(r"biến dạng|vật rắn|áp suất chất lỏng", re.I), "bien_dang_chat_long"),
    (re.compile(r"dao động|con lắc lò xo|con lắc đơn", re.I), "dao_dong"),
    (re.compile(r"sóng cơ|sóng âm|giao thoa sóng", re.I), "song_co"),
    (re.compile(r"nhiệt|nhiệt độ|nhiệt dung|chuyển thể|khí lí tưởng", re.I), "vat_li_nhiet"),
    (re.compile(r"từ trường|cảm ứng từ|từ thông", re.I), "tu_truong"),
    (re.compile(r"hạt nhân|phóng xạ", re.I), "vat_ly_hat_nhan"),
    # Chemistry
    (re.compile(r"cấu tạo nguyên tử|nguyên tử", re.I), "nguyen_tu"),
    (re.compile(r"bảng tuần hoàn|nguyên tố hóa học", re.I), "bang_tuan_hoan"),
    (re.compile(r"liên kết hóa học", re.I), "lien_ket_hoa_hoc"),
    (re.compile(r"năng lượng hóa học|nhiệt phản ứng", re.I), "nang_luong_hoa_hoc"),
    (re.compile(r"tốc độ phản ứng|cân bằng hóa học", re.I), "toc_do_phan_ung"),
    (re.compile(r"nhóm halogen|nguyên tố nhóm viia", re.I), "halogen"),
    (re.compile(r"este|lipid|chất béo", re.I), "este_lipid"),
    (re.compile(r"cacbohidrat|glucozo|saccarozo|tinh bột|xenlulozo", re.I), "cacbohidrat"),
    (re.compile(r"amin|amino axit|peptit|protein", re.I), "amin_amino_axit"),
    (re.compile(r"polime|vật liệu polime", re.I), "polime"),
    (re.compile(r"kim loại|đại cương về kim loại", re.I), "dai_cuong_kim_loai"),
    (re.compile(r"kim loại kiềm|kiềm thổ|nhôm", re.I), "kiem_tho_nhom"),
    (re.compile(r"crom|sắt|đồng", re.I), "crom_sat_dong"),
]


def clean_title(title: str) -> str:
    """Strip prefixes like 'Chương 1:', 'Chủ đề 2.', 'Bài 3:' and whitespace."""
    t = unicodedata.normalize("NFC", title or "").strip()
    t = re.sub(r"^(?:chương|chủ đề|bài|phần|tiết)\s*[\dIVXLCDMivxlcdm]+[\s:.-]*", "", t, flags=re.I).strip()
    return t


def extract_topic_key(title: str) -> str:
    """Extract standard semantic topic key from title or content."""
    norm = unicodedata.normalize("NFC", title or "").strip().lower()
    for pattern, key in TOPIC_SYNONYMS:
        if pattern.search(norm):
            return key
    return clean_title(title).lower()


def match_similarity(a: str, b: str) -> float:
    """SequenceMatcher similarity on cleaned titles."""
    ca = clean_title(a).lower()
    cb = clean_title(b).lower()
    if ca == cb:
        return 1.0
    if ca in cb or cb in ca:
        return 0.85
    return SequenceMatcher(None, ca, cb).ratio()


def map_other_book_to_kntt(
    source_title: str,
    kntt_candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the best matching KNTT chapter/lesson dictionary for a title from another textbook."""
    if not kntt_candidates or not source_title:
        return None

    s_key = extract_topic_key(source_title)

    # 1. Exact semantic topic key match
    for cand in kntt_candidates:
        c_title = cand.get("name") or cand.get("title") or ""
        if extract_topic_key(c_title) == s_key:
            return cand

    # 2. High SequenceMatcher similarity (> 0.6)
    best_cand = None
    best_score = 0.0
    for cand in kntt_candidates:
        c_title = cand.get("name") or cand.get("title") or ""
        score = match_similarity(source_title, c_title)
        if score > best_score:
            best_score = score
            best_cand = cand

    if best_score >= 0.55:
        return best_cand

    return None

