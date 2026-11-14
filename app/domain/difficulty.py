"""Cognitive difficulty level classification for exam questions (THPT / Vietnamese standards).

Levels:
- RECOGNITION (Nhận biết): Nhớ lại khái niệm, định nghĩa, công thức, ký hiệu, đọc dữ kiện trực tiếp.
- UNDERSTANDING (Thông hiểu): Giải thích nguyên nhân, so sánh, phân tích mối quan hệ, áp dụng 1 bước.
- APPLICATION (Vận dụng): Tính toán nhiều bước, giải bài toán định lượng, phân tích số liệu biểu đồ.
- ADVANCED_APPLICATION (Vận dụng cao): Cực trị, bài toán tổng hợp liên chuyên đề, bài toán phân hóa 9-10 điểm.
"""

import re
import unicodedata

_HSG_KEYWORDS = re.compile(
    r"\b(học sinh giỏi|olympic|chuyên|quốc gia|bất đẳng thức jensen|bất đẳng thức karamata|"
    r"phương trình hàm|hệ vật cơ học|vật rắn quay|lưỡng chất cầu|vi phân|tích phân đường|"
    r"cơ học lượng tử|tương đối tính|cực trị hình học không gian|số học olympic)\b",
    re.I,
)

_ADVANCED_KEYWORDS = re.compile(
    r"\b(cực trị|giá trị lớn nhất|giá trị nhỏ nhất|gtln|gtnn|đạt cực đại|đạt cực tiểu|"
    r"tối ưu|tối đa|tối thiểu|bất đẳng thức|biện luận số nghiệm|tham số m để|"
    r"nguyên của tham số|tổng quát|đánh giá toàn diện|đề xuất chiến lược|"
    r"hộp đen|đồ thị biến thiên|hai dao động cùng phương|khoảng cách lớn nhất giữa|thời điểm gần nhất)\b",
    re.I,
)

_RECOGNITION_KEYWORDS = re.compile(
    r"\b(định nghĩa|khái niệm|nêu|kể tên|chỉ ra|công thức nào sau đây|ký hiệu nào|đơn vị của|"
    r"chất nào sau đây|kim loại nào|quốc gia nào|tỉnh nào|nguyên tố nào|loại nào|"
    r"phát biểu nào sau đây đúng|phát biểu nào sau đây sai|mệnh đề nào sau đây đúng|mệnh đề nào sau đây sai|"
    r"nhận xét nào sau đây đúng|chọn ý đúng|đâu là|kí hiệu|là đơn vị của|cho biết:|"
    r"theo định luật|hằng số|tiệm cận đứng|tiệm cận ngang|tập xác định|đạo hàm của hàm số)\b",
    re.I,
)

_APPLICATION_KEYWORDS = re.compile(
    r"\b(tính|tính toán|tìm giá trị|xác định giá trị|tìm x|tìm m|chứng minh|vẽ biểu đồ|"
    r"tốc độ tăng trưởng|tính mật độ|tỉ trọng|cơ cấu|khoảng cách|gia tốc|vận tốc|"
    r"chu kì|bước sóng|công suất|cường độ|hiệu điện thế|điện áp|nhiệt dung|nhiệt lượng|"
    r"động năng|thế năng|áp suất|thể tích|khối lượng|nồng độ|hiệu suất|nguyên hàm|"
    r"tích phân|diện tích hình phẳng|thể tích khối|chênh lệch|giá trị của|bằng bao nhiêu)\b",
    re.I,
)

_UNDERSTANDING_KEYWORDS = re.compile(
    r"\b(tại sao|vì sao|giải thích|nguyên nhân|phân tích nguyên nhân|so sánh|ý nghĩa của|"
    r"mối quan hệ|vai trò|tác động của|ảnh hưởng của|hậu quả|biểu hiện|thể hiện điều gì|"
    r"dẫn đến|nguyên nhân chủ yếu|nguyên nhân cơ bản|nguyên nhân sâu xa)\b",
    re.I,
)


def classify_difficulty(
    content: str,
    choices: list[str] | None = None,
    number: int = 0,
    total_questions: int = 0,
    question_type: str = "",
) -> str:
    """Classify the cognitive difficulty of a question based on its text, options, and position."""
    text = unicodedata.normalize("NFC", content or "").strip()
    norm = text.lower()

    # 0. Olympiad / HSG keywords
    if _HSG_KEYWORDS.search(norm):
        return "HSG"

    # 1. Advanced application keywords
    if _ADVANCED_KEYWORDS.search(norm):
        return "ADVANCED_APPLICATION"

    # 2. Recognition check before simple formula check unless explicit calculation
    recog_match = bool(_RECOGNITION_KEYWORDS.search(norm))
    app_match = bool(_APPLICATION_KEYWORDS.search(norm))

    if recog_match and not app_match:
        return "RECOGNITION"

    # 3. Strong application keywords (calculation, quantitative mechanics/chemistry)
    has_math_formula = "$" in text and any(op in text for op in ["=", "\\frac", "\\cdot", "\\sqrt", "^", "_"])
    if app_match or has_math_formula:
        # If at the very end of an exam (> 85% mark), likely advanced
        if total_questions >= 25 and number > 0 and number >= total_questions * 0.88:
            return "ADVANCED_APPLICATION"
        return "APPLICATION"

    # 4. Understanding keywords (why, explain, compare, cause/effect)
    if _UNDERSTANDING_KEYWORDS.search(norm):
        return "UNDERSTANDING"

    # 5. Position-based heuristics in standard Vietnamese exams
    if total_questions >= 20 and number > 0:
        ratio = number / total_questions
        if ratio <= 0.35:
            return "RECOGNITION"
        if ratio <= 0.65:
            return "UNDERSTANDING"
        if ratio <= 0.88:
            return "APPLICATION"
        return "ADVANCED_APPLICATION"

    # 6. Fallback based on question characteristics
    # Short question with simple options -> Recognition
    if choices and len(choices) >= 2 and all(len(c.strip()) < 15 for c in choices) and len(text) < 120:
        return "RECOGNITION"

    # Long essay questions without specific calculation -> Understanding
    if question_type == "ESSAY" or len(text) > 200:
        return "UNDERSTANDING"

    return "RECOGNITION"
