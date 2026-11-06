"""An exam matrix (ma trận đề) uploaded by a teacher: Excel, Word, PDF or a photo, read into matrix rules.

Tables in Excel and Word, and PDFs with a text layer, are read locally and given to a text model as text; scans and
photos go to a model that reads pictures. Either way the model also maps each row's topic to our table of contents
(the subject's chapters and lessons), so the rows become MatrixRule objects the paper drawer understands.
"""

import asyncio
import io
import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.application.paper import MatrixRule
from app.models import Category, Difficulty, QuestionType
from app.scope import scope_options

log = logging.getLogger(__name__)

MAX_ROWS = 30
MAX_TEXT = 20_000  # characters of table text sent to the model
MAX_PAGES = 4  # pages of a scanned PDF
TEXT_KINDS = {".xlsx", ".xlsm", ".docx", ".pdf"}
IMAGE_KINDS = {".png", ".jpg", ".jpeg", ".webp"}


class MatrixError(Exception):
    """The matrix could not be read; the message is user-facing."""


class _Row(BaseModel):
    topic: str = Field(description="Chủ đề / nội dung kiến thức như ghi trong ma trận")
    category: str = Field(description='Khoá mục lục khớp nhất ("c:<id>") trong danh sách, hoặc "" nếu không có')
    level: Literal["RECOGNITION", "UNDERSTANDING", "APPLICATION", "ADVANCED_APPLICATION"]
    type: Literal["MULTIPLE_CHOICE", "TRUE_FALSE", "SHORT_ANSWER", "ESSAY"]
    count: int


class _Matrix(BaseModel):
    title: str = Field("", description="Tên đề / bài kiểm tra nếu ma trận có ghi")
    minutes: int = Field(0, description="Thời gian làm bài (phút) nếu có ghi, không có thì 0")
    rows: list[_Row]


PROMPT = """Đây là MA TRẬN ĐỀ KIỂM TRA của giáo viên (bảng: chủ đề/nội dung × mức độ × dạng câu hỏi → số câu).
Hãy chuyển nó thành danh sách dòng, MỖI DÒNG = một ô có số câu > 0: chủ đề, mức độ, dạng câu, số câu.

Mức độ: Nhận biết / Biết → RECOGNITION; Thông hiểu / Hiểu → UNDERSTANDING; Vận dụng → APPLICATION;
Vận dụng cao → ADVANCED_APPLICATION.
Dạng câu: trắc nghiệm nhiều lựa chọn / TNKQ / phần I → MULTIPLE_CHOICE; đúng sai / phần II → TRUE_FALSE;
trả lời ngắn / phần III → SHORT_ANSWER; tự luận → ESSAY. Ma trận chỉ ghi "TN" và "TL" thì TN = MULTIPLE_CHOICE,
TL = ESSAY. Với câu đúng sai, mỗi CÂU (gồm 4 ý) tính là 1, không đếm từng ý.
Bỏ các dòng/cột tổng, tỉ lệ %, điểm số; chỉ lấy số câu.

Ghép mỗi chủ đề với mục KHỚP NHẤT trong MỤC LỤC dưới đây (ghi đúng khoá "c:<id>"); chủ đề trải nhiều bài thì
chọn mục cha chung (chương). Không có mục nào khớp thì để category = "".

MỤC LỤC MÔN {subject}:
{toc}
"""


@dataclass
class MatrixRow:
    topic: str
    scope_key: str  # "c:<id>", or "" when no chapter matched (the teacher picks one)
    level: str
    question_type: str
    count: int

    def rule(self) -> MatrixRule:
        return MatrixRule(self.scope_key, self.level, self.question_type, self.count)


@dataclass
class ReadMatrix:
    rows: list[MatrixRow]
    title: str = ""
    minutes: int = 0


def kind_of(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in TEXT_KINDS | IMAGE_KINDS:
        raise MatrixError("Hãy tải lên file Excel (.xlsx), Word (.docx), PDF hoặc ảnh (PNG, JPG).")
    return suffix


def _xlsx_text(data: bytes) -> str:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    lines = []
    for sheet in book.worksheets:
        lines.append(f"## Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else " ".join(str(v).split()) for v in row]
            if any(cells):
                lines.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(lines)


def _docx_text(data: bytes) -> str:
    import pypandoc

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "matrix.docx"
        path.write_bytes(data)
        return pypandoc.convert_file(str(path), "gfm", extra_args=["--wrap=none"])


def _pdf_text_or_pages(data: bytes) -> tuple[str, list[bytes]]:
    """The text layer when there is one; otherwise the first pages as PNG for a model that reads pictures."""
    import fitz

    with fitz.open(stream=data, filetype="pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        if len(text.split()) >= 30:
            return text, []
        return "", [pdf[i].get_pixmap(dpi=150).tobytes("png") for i in range(min(MAX_PAGES, pdf.page_count))]


def _image_png(data: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((2200, 2200))
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()


async def content_of(filename: str, data: bytes) -> tuple[str, list[bytes]]:
    """(table text, page images): exactly one of them is filled."""
    suffix = kind_of(filename)
    try:
        if suffix in (".xlsx", ".xlsm"):
            return await asyncio.to_thread(_xlsx_text, data), []
        if suffix == ".docx":
            return await asyncio.to_thread(_docx_text, data), []
        if suffix == ".pdf":
            return await asyncio.to_thread(_pdf_text_or_pages, data)
        return "", [await asyncio.to_thread(_image_png, data)]
    except Exception as exc:  # a broken or password-protected file
        log.info("Matrix file %s unreadable: %s", filename, exc)
        raise MatrixError("Không mở được file ma trận (file hỏng hoặc có mật khẩu).") from exc


def toc_lines(root: Category) -> str:
    return "\n".join(f"{o.key} | {o.path}" for o in scope_options([root]))


def _rows(result: _Matrix, root: Category) -> ReadMatrix:
    keys = {o.key for o in scope_options([root])}
    rows = []
    for row in result.rows:
        if row.count <= 0:
            continue
        key = row.category.strip() if row.category.strip() in keys else ""
        rows.append(MatrixRow(" ".join(row.topic.split())[:300], key, row.level, row.type, min(row.count, 200)))
    if not rows:
        raise MatrixError("Không tìm thấy dòng nào có số câu trong ma trận.")
    return ReadMatrix(rows[:MAX_ROWS], " ".join(result.title.split())[:300], max(0, min(result.minutes, 300)))


async def read_matrix(
    root: Category, filename: str, data: bytes, text_models, vision_models, gemini=None
) -> ReadMatrix:
    """Read the file and map it onto `root`'s table of contents. Text: free text models, then Gemini.
    Pictures: models that read pictures, then Gemini."""
    text, images = await content_of(filename, data)
    prompt = PROMPT.format(subject=root.name, toc=toc_lines(root))
    schema = json.dumps(_Matrix.model_json_schema(), ensure_ascii=False)
    system = "Bạn đọc ma trận đề kiểm tra. Trả về DUY NHẤT một JSON đúng schema: " + schema
    if text:
        if not text.strip():
            raise MatrixError("File không có nội dung.")
        user = f"{prompt}\nMA TRẬN (bảng, các ô ngăn bởi |):\n{text[:MAX_TEXT]}"
        candidates = [(m, user) for m in text_models]
    else:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for png in images:
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(png)}})
        candidates = [(m, content) for m in vision_models]
    for model, user in candidates:
        try:
            if not await model.available():
                continue
            raw = await model._ask(system, user, 1)
            return _rows(_Matrix.model_validate_json(raw), root)
        except MatrixError:
            raise
        except Exception as exc:  # quota, a bad answer: the next model
            log.warning("Matrix with %s:%s failed: %s", model.name, model.model, exc)
    if gemini is None:
        raise MatrixError("Chưa đọc được ma trận: các model AI đang bận hoặc hết lượt, hãy thử lại sau.")
    from google.genai import types

    contents: list = [prompt]
    if text:
        contents.append("MA TRẬN (bảng, các ô ngăn bởi |):\n" + text[:MAX_TEXT])
    contents += [types.Part.from_bytes(data=png, mime_type="image/png") for png in images]
    try:
        result = await gemini._generate(contents, _Matrix, models=gemini._extract_models or None)
    except (ValidationError, Exception) as exc:
        log.warning("Matrix with Gemini failed: %s", exc)
        raise MatrixError("Chưa đọc được ma trận: các model AI đang bận hoặc hết lượt, hãy thử lại sau.") from exc
    return _rows(result, root)


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


LEVEL_VALUES = [d.value for d in Difficulty]
TYPE_VALUES = [t.value for t in QuestionType]
