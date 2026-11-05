"""Parse question sets from uploaded files (.xlsx, .docx, .txt).

Text format (.docx / .txt), one block per question:

    Câu 1: Question text, may span lines and contain $LaTeX$
    A. first choice
    *B. correct choice          <- either mark with "*" ...
    C. third choice    D. fourth choice   <- several choices per line if separated by a tab or 2+ spaces
    Đáp án: B                   <- ... or give an answer line
    Mức độ: Thông hiểu          (optional, otherwise the default chosen on upload)
    Lời giải: explanation, may span lines until the next "Câu"

Excel format: first sheet, header row with columns
    Nội dung | A | B | C | D | E | F | Đáp án | Mức độ | Lời giải
"""

import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from app.models import QUESTION_MAX_CHOICES, QUESTION_MIN_CHOICES, Difficulty

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_QUESTIONS = 500
LETTERS = "ABCDEF"[:QUESTION_MAX_CHOICES]
SUPPORTED_EXTENSIONS = (".xlsx", ".docx", ".txt")

DIFFICULTY_ALIASES = {
    "nhận biết": Difficulty.RECOGNITION,
    "nb": Difficulty.RECOGNITION,
    "biết": Difficulty.RECOGNITION,
    "thông hiểu": Difficulty.UNDERSTANDING,
    "th": Difficulty.UNDERSTANDING,
    "hiểu": Difficulty.UNDERSTANDING,
    "vận dụng": Difficulty.APPLICATION,
    "vd": Difficulty.APPLICATION,
    "vận dụng cao": Difficulty.APPLICATION,
    "vdc": Difficulty.APPLICATION,
    **{d.value.lower(): d for d in Difficulty},
}


class ImportFileError(Exception):
    """The file as a whole cannot be read; the message is shown to the admin."""


@dataclass
class ParsedQuestion:
    label: str  # "Câu 3" or "Dòng 5", used in error messages
    content: str = ""
    choices: list[str] = field(default_factory=list)
    correct: int | None = None
    difficulty: str | None = None  # Difficulty value, or None to use the upload default
    explanation: str = ""
    errors: list[str] = field(default_factory=list)
    duplicate: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ParsedQuestion":
        return cls(
            label=str(data.get("label", "")),
            content=str(data.get("content", "")),
            choices=[str(c) for c in data.get("choices", [])],
            correct=data["correct"] if isinstance(data.get("correct"), int) else None,
            difficulty=data.get("difficulty") if data.get("difficulty") in Difficulty.__members__ else None,
            explanation=str(data.get("explanation", "")),
        )


def _nfc(text: str) -> str:
    # Word often stores Vietnamese in decomposed form; normalise so regexes and duplicate checks work.
    return unicodedata.normalize("NFC", text).replace(" ", " ")


def parse_difficulty(text: str) -> Difficulty | None:
    return DIFFICULTY_ALIASES.get(re.sub(r"\s+", " ", _nfc(text).strip().lower()))


def validate(q: ParsedQuestion) -> list[str]:
    errors = []
    if not q.content.strip():
        errors.append("Thiếu nội dung câu hỏi.")
    if not QUESTION_MIN_CHOICES <= len(q.choices) <= QUESTION_MAX_CHOICES:
        errors.append(f"Cần từ {QUESTION_MIN_CHOICES} đến {QUESTION_MAX_CHOICES} lựa chọn (đang có {len(q.choices)}).")
    if any(not c.strip() for c in q.choices):
        errors.append("Có lựa chọn bị trống.")
    if q.correct is None:
        errors.append("Chưa có đáp án đúng (thêm dòng “Đáp án: B” hoặc đánh dấu * trước lựa chọn đúng).")
    elif not 0 <= q.correct < len(q.choices):
        errors.append(f"Đáp án {LETTERS[q.correct] if q.correct < len(LETTERS) else '?'} không có trong các lựa chọn.")
    return errors


# ---------------------------------------------------------------- text (.txt / .docx)

_Q_START = re.compile(r"^\s*(?:câu|cau|question)\s*(\d+)\s*[:.)\-–]?\s*(.*)$", re.IGNORECASE)
_CHOICE = re.compile(r"^\s*(\*?)\s*([A-Fa-f])\s*[.)]\s*(.*)$", re.DOTALL)
# Further choices on the same line must be separated by a tab or at least two spaces.
_INLINE_SPLIT = re.compile(r"(?:\t+|\s{2,})(?=\*?\s*[A-F]\s*[.)]\s)")
_ANSWER = re.compile(
    r"^\s*(?:đáp\s*án(?:\s*đúng)?|đa|answer)\s*[:.\-–]?\s*\(?\s*([A-Fa-f])\s*\)?\s*\.?\s*$", re.IGNORECASE
)
_LEVEL = re.compile(r"^\s*(?:mức\s*độ|level|difficulty)\s*[:.\-–]\s*(.+?)\s*$", re.IGNORECASE)
_EXPLANATION = re.compile(
    r"^\s*(?:lời\s*giải(?:\s*chi\s*tiết)?|hướng\s*dẫn(?:\s*giải)?|giải\s*thích|giải|explanation)\s*[:.\-–]\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class _Block:
    q: ParsedQuestion
    content: list[str] = field(default_factory=list)
    choices: list[list[str]] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    section: str = "content"  # content | choices | explanation | meta
    star: int | None = None
    answer: int | None = None

    def add_choice(self, star: str, letter: str, text: str) -> None:
        index = LETTERS.find(letter.upper())
        if index != len(self.choices):
            self.q.errors.append(f"Lựa chọn {letter.upper()} không đúng thứ tự A, B, C…")
        if star:
            self.star = len(self.choices)
        self.choices.append([text.strip()])
        self.section = "choices"

    def finish(self) -> ParsedQuestion:
        q = self.q
        q.content = "\n".join(self.content).strip()
        q.choices = ["\n".join(lines).strip() for lines in self.choices]
        q.explanation = "\n".join(self.explanation).strip()
        if self.star is not None and self.answer is not None and self.star != self.answer:
            q.errors.append("Đáp án đánh dấu * khác với dòng “Đáp án”.")
        q.correct = self.answer if self.answer is not None else self.star
        return q


def parse_text(text: str) -> list[ParsedQuestion]:
    blocks: list[_Block] = []
    current: _Block | None = None

    for raw in _nfc(text).splitlines():
        line = raw.rstrip()
        if m := _Q_START.match(line):
            current = _Block(ParsedQuestion(label=f"Câu {m.group(1)}"))
            blocks.append(current)
            if m.group(2).strip():
                current.content.append(m.group(2).strip())
            continue
        if current is None:  # title / instructions before the first question
            continue

        if m := _ANSWER.match(line):
            current.answer = LETTERS.find(m.group(1).upper())
            current.section = "meta"
            continue
        if m := _LEVEL.match(line):
            level = parse_difficulty(m.group(1))
            if level is None:
                current.q.errors.append(f"Mức độ “{m.group(1)}” không hợp lệ (Nhận biết / Thông hiểu / Vận dụng).")
            else:
                current.q.difficulty = level.value
            current.section = "meta"
            continue
        if m := _EXPLANATION.match(line):
            current.section = "explanation"
            if m.group(1).strip():
                current.explanation.append(m.group(1).strip())
            continue

        if current.section != "explanation":
            parts = _INLINE_SPLIT.split(line.strip())
            matches = [_CHOICE.match(p) for p in parts]
            if parts and all(matches):
                for m in matches:
                    current.add_choice(m.group(1), m.group(2), m.group(3))
                continue

        if not line.strip():
            if current.section in ("content", "explanation"):
                getattr(current, current.section).append("")
            continue
        if current.section == "content":
            current.content.append(line.strip())
        elif current.section == "choices":
            current.choices[-1].append(line.strip())
        elif current.section == "explanation":
            current.explanation.append(line.strip())
        # text after "Đáp án"/"Mức độ" that is not a known field is ignored

    return [b.finish() for b in blocks]


def _docx_lines(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types for corrupt files
        raise ImportFileError("Không đọc được file Word. Hãy lưu lại dưới dạng .docx rồi thử lại.") from exc

    lines: list[str] = []
    # Walk the body in order so paragraphs and tables (e.g. 2×2 choice grids) keep their position.
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.extend(Paragraph(child, doc).text.split("\n"))
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                seen = set()
                for cell in row.cells:
                    if id(cell._tc) in seen:  # merged cells repeat
                        continue
                    seen.add(id(cell._tc))
                    for p in cell.paragraphs:
                        lines.extend(p.text.split("\n"))
    return "\n".join(lines)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1258", errors="replace")


# ---------------------------------------------------------------- Excel

_HEADERS = {
    "content": {"nội dung", "câu hỏi", "nội dung câu hỏi", "đề bài", "content", "question"},
    "answer": {"đáp án", "đáp án đúng", "answer", "correct"},
    "level": {"mức độ", "level", "difficulty"},
    "explanation": {"lời giải", "giải thích", "hướng dẫn giải", "lời giải chi tiết", "explanation"},
}
for _letter in LETTERS:
    _HEADERS[_letter] = {
        _letter.lower(),
        f"đáp án {_letter.lower()}",
        f"phương án {_letter.lower()}",
        f"lựa chọn {_letter.lower()}",
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _nfc(str(value)).strip()


def parse_xlsx(data: bytes) -> list[ParsedQuestion]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFileError("Không đọc được file Excel. Hãy dùng định dạng .xlsx.") from exc
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = next(rows, None) or ()
        columns: dict[str, int] = {}
        for index, name in enumerate(header):
            key = re.sub(r"\s+", " ", _cell(name).lower())
            for field_name, aliases in _HEADERS.items():
                if key in aliases and field_name not in columns:
                    columns[field_name] = index
        if "content" not in columns:
            raise ImportFileError("Dòng đầu file Excel phải có cột “Nội dung” (xem file mẫu).")

        def get(row, name):
            i = columns.get(name)
            return _cell(row[i]) if i is not None and i < len(row) else ""

        questions = []
        for row_number, row in enumerate(rows, start=2):
            if not any(_cell(v) for v in row):
                continue
            q = ParsedQuestion(
                label=f"Dòng {row_number}", content=get(row, "content"), explanation=get(row, "explanation")
            )
            choices = [get(row, letter) for letter in LETTERS]
            while choices and not choices[-1]:
                choices.pop()
            q.choices = choices
            answer = get(row, "answer").upper().strip(" .()")
            if answer:
                if len(answer) == 1 and answer in LETTERS:
                    q.correct = LETTERS.index(answer)
                else:
                    q.errors.append(f"Đáp án “{answer}” không hợp lệ (ghi một chữ cái A–{LETTERS[-1]}).")
            if level_text := get(row, "level"):
                level = parse_difficulty(level_text)
                if level is None:
                    q.errors.append(f"Mức độ “{level_text}” không hợp lệ.")
                else:
                    q.difficulty = level.value
            questions.append(q)
        return questions
    finally:
        wb.close()


# ---------------------------------------------------------------- entry point


def parse_file(filename: str, data: bytes) -> list[ParsedQuestion]:
    name = filename.lower()
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(f"File quá lớn (tối đa {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    if name.endswith(".xlsx"):
        questions = parse_xlsx(data)
    elif name.endswith(".docx"):
        questions = parse_text(_docx_lines(data))
    elif name.endswith(".txt"):
        questions = parse_text(_decode(data))
    else:
        raise ImportFileError("Chỉ hỗ trợ file .xlsx, .docx hoặc .txt.")
    if not questions:
        raise ImportFileError(
            "Không tìm thấy câu hỏi nào. Mỗi câu cần bắt đầu bằng “Câu 1:”, “Câu 2:”… (xem file mẫu)."
        )
    if len(questions) > MAX_QUESTIONS:
        raise ImportFileError(f"File có {len(questions)} câu, vượt giới hạn {MAX_QUESTIONS} câu mỗi lần nhập.")
    for q in questions:
        q.errors += validate(q)
    return questions
