"""Structured-output schemas sent to the model. Field order matters: the model writes fields in order,
so the worked solution comes before the chosen answer (chain of thought inside the schema)."""

from typing import Literal

from pydantic import BaseModel, Field

ChoiceLabel = Literal["A", "B", "C", "D", "E", "F"]
QuestionType = Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]
DifficultyName = Literal["RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION", "HSG"]
TargetCodeName = Literal["SGK_KNTT", "THPTQG", "DGTD_TSA", "DGNL_HSA", "VAO_10", "HSG_TINH", "HSG_QG"]


class BoundingBox(BaseModel):
    """Figure location on the page, each value on a 0–1000 scale."""

    ymin: int
    xmin: int
    ymax: int
    xmax: int


class ExtractedChoice(BaseModel):
    label: ChoiceLabel
    content: str = Field(description="Nội dung lựa chọn, KHÔNG kèm nhãn “A.”; công thức LaTeX trong $...$")


class ExtractedQuestion(BaseModel):
    number: int = Field(description="Số thứ tự câu như in trên đề (Câu 5 → 5)")
    page: int = Field(description="Số trang (theo nhãn TRANG n trong yêu cầu) nơi câu hỏi bắt đầu")
    type: QuestionType = Field(
        description="MULTIPLE_CHOICE: có các lựa chọn A, B, C, D; TRUE_FALSE: các ý a), b), c), d) đúng/sai; "
        "SHORT_ANSWER: trả lời bằng một số/kết quả ngắn; ESSAY: tự luận"
    )
    difficulty: DifficultyName | None = Field(
        default=None,
        description="Mức độ nhận thức của câu: RECOGNITION (Nhận biết), UNDERSTANDING (Thông hiểu), "
        "APPLICATION (Vận dụng), ADVANCED_APPLICATION (Vận dụng cao), HSG (Học sinh giỏi / Olympic)",
    )
    kntt_unit_code: str | None = Field(
        default=None,
        description="Mã bài học SGK Kết nối tri thức (VD: PHY12-KNTT-C1-B2, MATH12-KNTT-C1-B1) nếu xác định được",
    )
    is_thinking_skill: bool = Field(
        default=False,
        description="True nếu câu hỏi đánh giá tư duy, đọc hiểu khoa học, phân tích thí nghiệm/đồ thị (TSA/HSA)",
    )
    suitable_targets: list[str] = Field(
        default_factory=list,
        description="Các kỳ thi phù hợp: SGK_KNTT, THPTQG, DGTD_TSA, DGNL_HSA, VAO_10, HSG_TINH, HSG_QG",
    )
    content: str = Field(description="Nguyên văn đề bài tiếng Việt, mọi công thức/ký hiệu/số mũ viết LaTeX trong $...$")
    choices: list[ExtractedChoice] = Field(description="Các lựa chọn theo thứ tự; rỗng nếu không phải trắc nghiệm")
    figure_bbox: BoundingBox | None = Field(
        default=None, description="Khung bao quanh riêng hình vẽ của câu này (không lấy chữ); null nếu không có hình"
    )
    key_label: str | None = Field(
        default=None,
        description="Trắc nghiệm: chữ cái đáp án đúng CỦA CÂU NÀY nếu tài liệu có in (bảng đáp án, phần đáp án); "
        "null nếu không thấy",
    )
    key: str | None = Field(
        default=None,
        description="Đáp án / lời giải / hướng dẫn chấm CỦA CÂU NÀY nếu tài liệu có in (phần ĐÁP ÁN, HƯỚNG DẪN CHẤM), "
        "chép gọn, bỏ cột điểm; null nếu không thấy",
    )


class ExtractionResult(BaseModel):
    questions: list[ExtractedQuestion]


class Solution(BaseModel):
    number: int
    step_by_step_solution: str = Field(
        description="Lời giải chi tiết từng bước bằng tiếng Việt, công thức LaTeX trong $...$. "
        "Viết công thức, thay số, tính kết quả TRƯỚC khi chốt đáp án."
    )
    final_value: str = Field(description="Kết quả cuối cùng tính được, kèm đơn vị")
    matches_an_option: bool = Field(
        description="MULTIPLE_CHOICE: True nếu kết quả khớp đúng một lựa chọn. Dạng khác: True nếu chắc chắn."
    )
    correct_label: ChoiceLabel | None = Field(description="MULTIPLE_CHOICE: nhãn lựa chọn đúng; dạng khác: null")
    statement_truths: list[bool] | None = Field(
        default=None,
        description="TRUE_FALSE: Đúng (true) / Sai (false) cho từng ý a, b, c, d theo thứ tự; dạng khác: null",
    )
    short_answer: str | None = Field(
        description="SHORT_ANSWER: CHỈ con số đáp án, dấu phẩy thập phân, không kèm đơn vị (VD: 30 hoặc 1,25); dạng khác: null"
    )
    difficulty: DifficultyName = Field(
        description="RECOGNITION = Nhận biết, UNDERSTANDING = Thông hiểu, APPLICATION = Vận dụng, "
        "ADVANCED_APPLICATION = Vận dụng cao, HSG = Học sinh giỏi / Chuyên"
    )
    kntt_unit_code: str | None = Field(
        default=None,
        description="Mã bài học SGK Kết nối tri thức (VD: PHY12-KNTT-C1-B2, MATH12-KNTT-C1-B1) nếu xác định được",
    )
    is_thinking_skill: bool = Field(
        default=False,
        description="True nếu câu hỏi có tính thực tiễn, mô hình hóa, phân tích bảng biểu/thí nghiệm (TSA/HSA)",
    )
    suitable_targets: list[str] = Field(
        default_factory=list,
        description="Các mục tiêu kỳ thi phù hợp: SGK_KNTT, THPTQG, DGTD_TSA, DGNL_HSA, VAO_10, HSG_TINH, HSG_QG",
    )


class SolveResult(BaseModel):
    solutions: list[Solution]


class EssayGrade(BaseModel):
    number: int
    reference_solution: str = Field(
        description="Lời giải đúng, từng bước, tiếng Việt, công thức LaTeX trong $...$ (dùng lời giải mẫu nếu được cung cấp)"
    )
    feedback: str = Field(
        description="Nhận xét bài làm của học sinh: ý đúng, chỗ sai (sai công thức, đơn vị, tính toán), cách sửa. Xưng 'em'."
    )
    points: float = Field(description="Điểm của bài làm, từ 0 đến max_points, làm tròn tới 0,25")


class EssayGradeResult(BaseModel):
    grades: list[EssayGrade]


class PaperKey(BaseModel):
    code: str = Field(description="Mã đề như in trong bảng (VD: 101, 0102)")
    multiple_choice: list[ChoiceLabel] = Field(
        default_factory=list, description="Đáp án trắc nghiệm nhiều lựa chọn theo thứ tự câu 1, 2, 3, ... (phần I)"
    )
    true_false: list[str] = Field(
        default_factory=list,
        description="Phần đúng/sai: mỗi câu một chuỗi Đ/S theo thứ tự ý a, b, c, d (VD: ĐSĐS); rỗng nếu không có",
    )
    short_answer: list[str] = Field(
        default_factory=list, description="Phần trả lời ngắn: đáp số từng câu theo thứ tự (VD: 0,5); rỗng nếu không có"
    )


class AnswerKeyResult(BaseModel):
    papers: list[PaperKey] = Field(description="Mỗi mã đề có trong bảng đáp án")
