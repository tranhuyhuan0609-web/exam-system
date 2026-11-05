"""Parse question sets from uploaded files (.xlsx, .docx, .txt).

Text format (.docx / .txt), one block per question:

    Câu 1: Question text, may span lines and contain $LaTeX$
    A. first choice
    *B. correct choice          <- trắc nghiệm: mark with "*" ...
    C. third choice    D. fourth choice   <- several choices per line if separated by a tab or 2+ spaces
    Đáp án: B                   <- ... or give an answer line
    Mức độ: Thông hiểu          (optional, otherwise the default chosen on upload)
    Lời giải: explanation, may span lines until the next "Câu"

    Câu 2: ...                  <- đúng/sai: four statements with lower-case labels
    a) statement   b) statement   c) ...   d) ...
    Đáp án: Đ S Đ S

    Câu 3: ...                  <- trả lời ngắn: no options, just the answer
    Đáp án: 30

Excel format: first sheet, header row with columns
    Dạng | Nội dung | A | B | C | D | E | F | Đáp án | Mức độ | Lời giải
("Dạng" is optional: Trắc nghiệm / Đúng sai / Trả lời ngắn; it is guessed from the row when missing.)
"""

import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from app import question_rules as rules
from app.models import QUESTION_MAX_CHOICES, Difficulty, QuestionType

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
    "vận dụng cao": Difficulty.ADVANCED_APPLICATION,
    "vdc": Difficulty.ADVANCED_APPLICATION,
    **{d.value.lower(): d for d in Difficulty},
}

TYPE_ALIASES = {
    "trắc nghiệm": QuestionType.MULTIPLE_CHOICE,
    "tn": QuestionType.MULTIPLE_CHOICE,
    "nhiều lựa chọn": QuestionType.MULTIPLE_CHOICE,
    "đúng sai": QuestionType.TRUE_FALSE,
    "đúng/sai": QuestionType.TRUE_FALSE,
    "đ/s": QuestionType.TRUE_FALSE,
    "ds": QuestionType.TRUE_FALSE,
    "trả lời ngắn": QuestionType.SHORT_ANSWER,
    "tln": QuestionType.SHORT_ANSWER,
    "điền số": QuestionType.SHORT_ANSWER,
    **{t.value.lower(): t for t in QuestionType},
}


class ImportFileError(Exception):
    """The file as a whole cannot be read; the message is shown to the admin."""


@dataclass
class ParsedQuestion:
    label: str  # "Câu 3" or "Dòng 5", used in error messages
    type: str = QuestionType.MULTIPLE_CHOICE.value
    content: str = ""
    choices: list[str] = field(default_factory=list)  # options, or statements for TRUE_FALSE
    correct: int | None = None  # MULTIPLE_CHOICE
    truths: list[bool | None] = field(default_factory=list)  # TRUE_FALSE
    answers: list[str] = field(default_factory=list)  # SHORT_ANSWER
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
        kind = data.get("type")
        truths = data.get("truths") if isinstance(data.get("truths"), list) else []
        return cls(
            label=str(data.get("label", "")),
            type=kind if kind in QuestionType.__members__ else QuestionType.MULTIPLE_CHOICE.value,
            content=str(data.get("content", "")),
            choices=[str(c) for c in data.get("choices", [])],
            correct=data["correct"] if isinstance(data.get("correct"), int) else None,
            truths=[t if isinstance(t, bool) else None for t in truths],
            answers=[str(a) for a in data.get("answers", []) if str(a).strip()],
            difficulty=data.get("difficulty") if data.get("difficulty") in Difficulty.__members__ else None,
            explanation=str(data.get("explanation", "")),
        )

    def stored_choices(self) -> list[dict[str, Any]]:
        return rules.build_choices(self.type, self.choices, self.correct, self.truths)


def _nfc(text: str) -> str:
    # Word often stores Vietnamese in decomposed form; normalise so regexes and duplicate checks work.
    return unicodedata.normalize("NFC", text).replace("\u00a0", " ")


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", _nfc(text).strip().lower())


def parse_difficulty(text: str) -> Difficulty | None:
    return DIFFICULTY_ALIASES.get(_key(text))


def parse_type(text: str) -> QuestionType | None:
    return TYPE_ALIASES.get(_key(text).replace(" / ", "/"))


def validate(q: ParsedQuestion) -> list[str]:
    return rules.validate(q.type, q.content, q.choices, q.correct, q.truths, q.answers)


def apply_answer(q: ParsedQuestion, raw: str) -> None:
    """Interpret an answer cell / line according to the question type."""
    raw = raw.strip()
    if not raw:
        return
    if q.type == QuestionType.TRUE_FALSE:
        q.truths = rules.parse_truths(raw)
    elif q.type == QuestionType.SHORT_ANSWER:
        q.answers = rules.split_accepted(raw)
    else:
        letter = raw.upper().strip(" .()")
        if len(letter) == 1 and letter in LETTERS:
            q.correct = LETTERS.index(letter)
        else:
            q.errors.append(f"Đáp án “{raw}” không hợp lệ (ghi một chữ cái A–{LETTERS[-1]}).")


# ---------------------------------------------------------------- text (.txt / .docx)

_Q_START = re.compile(r"^\s*(?:câu|cau|question)\s*(\d+)\s*[:.)\-–]?\s*(.*)$", re.IGNORECASE)
# Upper-case "A." / "A)" = option; lower-case "a)" / "a." = true/false statement.
_CHOICE = re.compile(r"^\s*(\*?)\s*([A-Fa-f])\s*[.)]\s*(.*)$", re.DOTALL)
# Further choices on the same line must be separated by a tab or at least two spaces.
_INLINE_SPLIT = re.compile(r"(?:\t+|\s{2,})(?=\*?\s*[A-Fa-f]\s*[.)]\s)")
_ANSWER = re.compile(r"^\s*(?:đáp\s*án(?:\s*đúng)?|đa|answer)\s*[:.\-–]\s*(.+?)\s*$", re.IGNORECASE)
_ANSWER_NO_COLON = re.compile(r"^\s*(?:đáp\s*án(?:\s*đúng)?|đa)\s+([A-Fa-f])\s*\.?\s*$", re.IGNORECASE)
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
    answer: str | None = None
    lower_labels: int = 0

    def add_choice(self, star: str, letter: str, text: str) -> None:
        if LETTERS.find(letter.upper()) != len(self.choices):
            self.q.errors.append(f"Lựa chọn {letter} không đúng thứ tự A, B, C… / a, b, c…")
        if letter.islower():
            self.lower_labels += 1
        if star:
            self.star = len(self.choices)
        self.choices.append([text.strip()])
        self.section = "choices"

    def finish(self) -> ParsedQuestion:
        q = self.q
        q.content = "\n".join(self.content).strip()
        q.choices = ["\n".join(lines).strip() for lines in self.choices]
        q.explanation = "\n".join(self.explanation).strip()
        if not q.choices:
            q.type = QuestionType.SHORT_ANSWER.value
        elif self.lower_labels and self.lower_labels == len(q.choices):
            q.type = QuestionType.TRUE_FALSE.value
        if self.answer is not None:
            apply_answer(q, self.answer)
        if q.type == QuestionType.MULTIPLE_CHOICE:
            if self.star is not None and q.correct is not None and self.star != q.correct:
                q.errors.append("Đáp án đánh dấu * khác với dòng “Đáp án”.")
            if q.correct is None:
                q.correct = self.star
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

        if m := (_ANSWER.match(line) or _ANSWER_NO_COLON.match(line)):
            current.answer = m.group(1)
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
    "type": {"dạng", "loại", "dạng câu", "loại câu", "type"},
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
        f"ý {_letter.lower()}",
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _nfc(str(value)).strip()


def _guess_type(choices: list[str], answer: str) -> QuestionType:
    if not choices:
        return QuestionType.SHORT_ANSWER
    compact = answer.upper().strip(" .()")
    if len(compact) == 1 and compact in LETTERS:
        return QuestionType.MULTIPLE_CHOICE
    truths = rules.parse_truths(answer)
    if truths and len(truths) == len(choices) and all(t is not None for t in truths):
        return QuestionType.TRUE_FALSE
    return QuestionType.MULTIPLE_CHOICE


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
            key = _key(_cell(name))
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
            answer = get(row, "answer")
            type_text = get(row, "type")
            kind = parse_type(type_text) if type_text else _guess_type(choices, answer)
            if kind is None:
                q.errors.append(f"Dạng câu “{type_text}” không hợp lệ (Trắc nghiệm / Đúng sai / Trả lời ngắn).")
                kind = _guess_type(choices, answer)
            q.type = kind.value
            q.choices = [] if kind == QuestionType.SHORT_ANSWER else choices
            apply_answer(q, answer)
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
