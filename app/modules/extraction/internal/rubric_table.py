"""A07 3-column grading rubric parser & complex colspan/rowspan matrix normalizer."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class TableCellSpan:
    text: str
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True, slots=True)
class RubricRowItem:
    question_label: str
    criterion_text: str
    points: Decimal


@dataclass(frozen=True, slots=True)
class RubricParseReport:
    rows: tuple[RubricRowItem, ...]
    total_points: Decimal
    warnings: tuple[str, ...]


MAX_SPAN = 50
MAX_GRID_ROWS = 500
MAX_GRID_COLS = 50
MAX_GRID_CELLS = 10_000


class TableGridLimitError(ValueError):
    """Raised when a table (or one of its spans) exceeds the safe expansion limits."""


def expand_table_grid(raw_rows: Sequence[Sequence[TableCellSpan]]) -> list[list[str]]:
    """Expand complex tables containing `colspan` and `rowspan` into a rectangular 2D text matrix."""
    grid: list[list[str | None]] = []

    if len(raw_rows) > MAX_GRID_ROWS:
        raise TableGridLimitError(f"Table has more than {MAX_GRID_ROWS} rows")
    for r_idx, row in enumerate(raw_rows):
        while len(grid) <= r_idx:
            grid.append([])
        c_idx = 0
        for cell in row:
            while c_idx < len(grid[r_idx]) and grid[r_idx][c_idx] is not None:
                c_idx += 1

            if cell.rowspan < 1 or cell.colspan < 1:
                raise ValueError("Cell rowspan and colspan must be >= 1")
            if cell.rowspan > MAX_SPAN or cell.colspan > MAX_SPAN:
                raise TableGridLimitError(f"Cell rowspan/colspan must be <= {MAX_SPAN}")
            if r_idx + cell.rowspan > MAX_GRID_ROWS or c_idx + cell.colspan > MAX_GRID_COLS:
                raise TableGridLimitError(f"Table grid exceeds {MAX_GRID_ROWS} rows x {MAX_GRID_COLS} columns")

            clean_txt = cell.text.strip()
            for dr in range(cell.rowspan):
                target_r = r_idx + dr
                while len(grid) <= target_r:
                    grid.append([])
                needed_cols = c_idx + cell.colspan
                if len(grid[target_r]) < needed_cols:
                    grid[target_r].extend([None] * (needed_cols - len(grid[target_r])))
                for dc in range(cell.colspan):
                    # Propagate cell value cleanly across merged slots (vertical/horizontal fill).
                    grid[target_r][c_idx + dc] = clean_txt

            c_idx += cell.colspan

    max_cols = max((len(r) for r in grid), default=0)
    if len(grid) * max_cols > MAX_GRID_CELLS:
        raise TableGridLimitError(f"Expanded table grid exceeds {MAX_GRID_CELLS} total cells")
    return [[cell or "" for cell in (row + [None] * (max_cols - len(row)))] for row in grid]


_NUMBER = r"[+-]?[0-9]+(?:[.,][0-9]+)?"
_POINTS_RE = re.compile(
    rf"^\s*(?P<sum>{_NUMBER}(?:\s*\+\s*{_NUMBER})*)\s*(?:điểm|diem|đ|d)?\s*$",
    re.IGNORECASE,
)
_HEADER_WORDS = {"cau", "câu", "noi dung", "nội dung", "diem", "điểm"}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def _parse_decimal_points(raw: str) -> Decimal | None:
    """Parse `0,5`, `0,5 điểm`, `0,25 + 0,25`; anything else (including mixed text) is `None`."""
    match = _POINTS_RE.match(_norm(raw))
    if not match:
        return None
    try:
        return sum(
            (Decimal(part.strip().replace(",", ".")) for part in match.group("sum").split("+") if part.strip()),
            Decimal("0"),
        )
    except InvalidOperation:
        return None


def parse_three_column_rubric(
    raw_rows: Sequence[Sequence[TableCellSpan]],
    *,
    expected_total_points: Decimal | None = Decimal("10.0"),
) -> RubricParseReport:
    """
    Parse Vietnamese 3-column scoring rubrics (`Câu | Nội dung | Điểm`), expanding merged cells
    first and reconciling cumulative point totals.
    """
    matrix = expand_table_grid(raw_rows)
    items: list[RubricRowItem] = []
    warnings: list[str] = []

    for idx, row in enumerate(matrix):
        if len(row) < 3:
            warnings.append(f"Row {idx} has fewer than 3 columns")
            continue
        col_q, col_body, col_pts = _norm(row[0]), _norm(row[1]), _norm(row[2])
        if (
            idx == 0
            and _parse_decimal_points(col_pts) is None
            and col_q.casefold() in _HEADER_WORDS
            and col_pts.casefold() in _HEADER_WORDS
        ):
            continue
        if not col_q and not col_body and not col_pts:
            continue

        pts = _parse_decimal_points(col_pts)
        if pts is None:
            warnings.append(f"Row {idx} ({col_q or '?'}): unparseable points {col_pts!r}")
            continue
        if pts < 0:
            warnings.append(f"Row {idx} ({col_q or '?'}): negative points {col_pts!r} ignored")
            continue
        items.append(RubricRowItem(question_label=col_q, criterion_text=col_body, points=pts))

    total = sum((it.points for it in items), Decimal("0"))
    if expected_total_points is not None and total != expected_total_points:
        warnings.append(
            f"Rubric points sum to {total}, expected {expected_total_points}"
        )

    return RubricParseReport(rows=tuple(items), total_points=total, warnings=tuple(warnings))
