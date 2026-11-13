"""Exam and solution (HDC) pairing across 4 weighted signals (modules A08/A03).

Fixes several defects from the architecture pseudo-code:
- Recognizes accented, NFD-decomposed, hyphenated and underscored solution markers.
- Never treats missing (`None`/blank) `folder_id`, `subject_code` or `question_count` as matches.
- Enforces deterministic 1-to-1 pairing sorted by descending score then `doc_id`.
- Uses `Decimal` arithmetic with single-point rounding at `0.001`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal

_WEIGHT_FILENAME = Decimal("0.40")
_WEIGHT_FOLDER = Decimal("0.25")
_WEIGHT_SUBJECT = Decimal("0.20")
_WEIGHT_COUNT = Decimal("0.15")
_SCORE_QUANT = Decimal("0.001")

DEFAULT_AUTO_CONFIRM_THRESHOLD = Decimal("0.85")
DEFAULT_PROPOSE_THRESHOLD = Decimal("0.60")

_SEPARATORS_RE = re.compile(r"[^0-9a-z]+")
_UNICODE_TOKEN_RE = re.compile(r"[^\W_]+")
_SOLUTION_MARKER_RE = re.compile(
    r"\b(?:hdc|huong dan cham|dap an|loi giai)\b"
)
_NOISE_PHRASE_RE = re.compile(
    r"\b(?:huong dan cham|giai chi tiet|dap an|loi giai|chi tiet|chinh thuc|hdc|de)\b"
)


class DocumentKind(StrEnum):
    """Classification of a pairing candidate document by filename."""

    EXAM = "EXAM"
    SOLUTION = "SOLUTION"
    EXAM_WITH_SOLUTION = "EXAM_WITH_SOLUTION"


@dataclass(frozen=True, slots=True)
class PairingDocumentInput:
    """Document metadata evaluated for exam-to-solution pairing."""

    doc_id: str
    filename: str
    folder_id: str | None = None
    subject_code: str | None = None
    question_count: int | None = None


@dataclass(frozen=True, slots=True)
class PairingSignals:
    """Individual normalized component scores for a candidate exam-solution pair."""

    filename_similarity: Decimal
    folder_proximity: Decimal
    subject_match: Decimal
    item_count_similarity: Decimal


@dataclass(frozen=True, slots=True)
class PairingScore:
    """Composite pairing score and its 4 underlying signal values."""

    total_score: Decimal
    signals: PairingSignals


@dataclass(frozen=True, slots=True)
class DocumentPairing:
    """Resulting 1-to-1 pairing recommendation between an exam and solution document."""

    exam_doc_id: str
    solution_doc_id: str
    similarity_score: Decimal
    signals: PairingSignals
    status: Literal["CONFIRMED", "PROPOSED"]


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCORE_QUANT, rounding=ROUND_HALF_UP)


def fold_vietnamese_ascii(raw: str) -> str:
    """Fold NFC/NFD Vietnamese text and separators into space-delimited ASCII tokens."""
    nfc = unicodedata.normalize("NFC", raw or "")
    nfc = nfc.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", nfc)
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    lowered = stripped.casefold()
    return _SEPARATORS_RE.sub(" ", lowered).strip()


def classify_pairing_document(filename: str) -> DocumentKind:
    """Classify a document as EXAM, pure SOLUTION, or combined EXAM_WITH_SOLUTION."""
    stem = Path(filename).stem
    folded = fold_vietnamese_ascii(stem)
    has_solution = bool(_SOLUTION_MARKER_RE.search(folded))
    if not has_solution:
        return DocumentKind.EXAM

    nfc_tokens = {
        tok
        for tok in _UNICODE_TOKEN_RE.findall(unicodedata.normalize("NFC", stem).casefold())
    }
    has_exam = "đề" in nfc_tokens or "de" in nfc_tokens
    if has_exam:
        return DocumentKind.EXAM_WITH_SOLUTION
    return DocumentKind.SOLUTION


def is_solution_filename(filename: str) -> bool:
    """Return True only when a filename represents a pure solution/rubric document."""
    return classify_pairing_document(filename) == DocumentKind.SOLUTION


def clean_pairing_stem(filename: str) -> str:
    """Strip exam/solution boilerplate tokens from a filename stem for Jaccard matching."""
    folded = fold_vietnamese_ascii(Path(filename).stem)
    cleaned = _NOISE_PHRASE_RE.sub(" ", folded)
    return " ".join(cleaned.split())


def score_exam_and_solution_pairing(
    exam: PairingDocumentInput,
    solution: PairingDocumentInput,
) -> PairingScore:
    """Compute weighted 4-signal similarity score between an exam and a solution file."""
    tokens_a = set(clean_pairing_stem(exam.filename).split())
    tokens_b = set(clean_pairing_stem(solution.filename).split())
    union_count = len(tokens_a | tokens_b)
    name_sim = (
        Decimal(len(tokens_a & tokens_b)) / Decimal(union_count)
        if union_count > 0
        else Decimal("0")
    )

    same_folder = (
        Decimal("1")
        if exam.folder_id and solution.folder_id and exam.folder_id == solution.folder_id
        else Decimal("0")
    )
    same_subject = (
        Decimal("1")
        if (
            exam.subject_code
            and solution.subject_code
            and exam.subject_code.strip().casefold() == solution.subject_code.strip().casefold()
        )
        else Decimal("0")
    )

    if exam.question_count is not None and solution.question_count is not None:
        q_diff = abs(exam.question_count - solution.question_count)
        count_sim = max(Decimal("0"), Decimal("1") - (Decimal(q_diff) / Decimal("10")))
    else:
        count_sim = Decimal("0")

    raw_total = (
        _WEIGHT_FILENAME * name_sim
        + _WEIGHT_FOLDER * same_folder
        + _WEIGHT_SUBJECT * same_subject
        + _WEIGHT_COUNT * count_sim
    )

    return PairingScore(
        total_score=_quantize(raw_total),
        signals=PairingSignals(
            filename_similarity=_quantize(name_sim),
            folder_proximity=_quantize(same_folder),
            subject_match=_quantize(same_subject),
            item_count_similarity=_quantize(count_sim),
        ),
    )


def auto_pair_exam_and_hdc_documents(
    candidate_docs: Sequence[PairingDocumentInput],
    *,
    auto_confirm_threshold: Decimal = DEFAULT_AUTO_CONFIRM_THRESHOLD,
    propose_threshold: Decimal = DEFAULT_PROPOSE_THRESHOLD,
) -> tuple[DocumentPairing, ...]:
    """Deterministically pair exam documents with solution documents (max 1-to-1).

    Candidate pairs meeting `propose_threshold` are ranked by descending similarity score
    with deterministic tie-breaking on `(exam_doc_id, solution_doc_id)`. Each solution and
    each exam may participate in at most one pairing.
    """
    exams: list[PairingDocumentInput] = []
    solutions: list[PairingDocumentInput] = []

    for doc in sorted(candidate_docs, key=lambda d: d.doc_id):
        kind = classify_pairing_document(doc.filename)
        if kind == DocumentKind.EXAM_WITH_SOLUTION:
            continue
        if kind == DocumentKind.SOLUTION:
            solutions.append(doc)
        else:
            exams.append(doc)

    scored_candidates: list[tuple[Decimal, str, str, PairingScore]] = []
    for ex in exams:
        for sol in solutions:
            scored = score_exam_and_solution_pairing(ex, sol)
            if scored.total_score >= propose_threshold:
                scored_candidates.append((scored.total_score, ex.doc_id, sol.doc_id, scored))

    scored_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_exams: set[str] = set()
    used_solutions: set[str] = set()
    pairings: list[DocumentPairing] = []

    for total_score, exam_id, sol_id, scored in scored_candidates:
        if exam_id in used_exams or sol_id in used_solutions:
            continue
        used_exams.add(exam_id)
        used_solutions.add(sol_id)
        status: Literal["CONFIRMED", "PROPOSED"] = (
            "CONFIRMED" if total_score >= auto_confirm_threshold else "PROPOSED"
        )
        pairings.append(
            DocumentPairing(
                exam_doc_id=exam_id,
                solution_doc_id=sol_id,
                similarity_score=total_score,
                signals=scored.signals,
                status=status,
            )
        )

    return tuple(pairings)
