"""Structured-output schemas sent to the model. Field order matters: the model writes fields in order,
so the worked solution comes before the chosen answer (chain of thought inside the schema)."""

from typing import Literal

from pydantic import BaseModel, Field

ChoiceLabel = Literal["A", "B", "C", "D", "E", "F"]
QuestionType = Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]
DifficultyName = Literal["RECOGNITION", "UNDERSTANDING", "APPLICATION"]


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
    content: str = Field(description="Nguyên văn đề bài tiếng Việt, mọi công thức/ký hiệu/số mũ viết LaTeX trong $...$")
    choices: list[ExtractedChoice] = Field(description="Các lựa chọn theo thứ tự; rỗng nếu không phải trắc nghiệm")
    figure_bbox: BoundingBox | None = Field(
        default=None, description="Khung bao quanh riêng hình vẽ của câu này (không lấy chữ); null nếu không có hình"
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
    matches_an_option: bool = Field(description="True nếu kết quả khớp đúng một lựa chọn của đề")
    correct_label: ChoiceLabel | None = Field(description="Nhãn lựa chọn đúng; null nếu câu không có lựa chọn")
    short_answer: str | None = Field(description="Đáp án ngắn cho câu SHORT_ANSWER; null nếu không áp dụng")
    difficulty: DifficultyName = Field(
        description="RECOGNITION = Nhận biết, UNDERSTANDING = Thông hiểu, APPLICATION = Vận dụng"
    )


class SolveResult(BaseModel):
    solutions: list[Solution]
