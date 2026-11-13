"""A07 3-column grading rubric parser & complex colspan/rowspan matrix normalizer."""

from __future__ import annotations

import re
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


def expand_table_grid(raw_rows: Sequence[Sequence[TableCellSpan]]) -> list[list[str]]:
    """Expand complex tables containing `colspan` and `rowspan` into a rectangular 2D text matrix."""
    grid: list[list[str | None]] = []

    for r_idx, row in enumerate(raw_rows):
        while len(grid) <= r_idx:
            grid.append([])
        c_idx = 0
        for cell in row:
            while c_idx < len(grid[r_idx]) and grid[r_idx][c_idx] is not None:
                c_idx += 1

            if cell.rowspan < 1 or cell.colspan < 1:
                raise ValueError("Cell rowspan and colspan must be >= 1")

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
    return [[cell or "" for cell in (row + [None] * (max_cols - len(row)))] for row in grid]


_POINTS_RE = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")
_HEADER_WORDS = {"cau", "câu", "noi dung", "nội dung", "diem", "điểm"}


def _parse_decimal_points(raw: str) -> Decimal | None:
    match = _POINTS_RE.search(raw.replace(",", "."))
    if not match:
        return None
    try:
        return Decimal(match.group(1))
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
        col_q, col_body, col_pts = row[0].strip(), row[1].strip(), row[2].strip()
        if idx == 0 and any(w in col_q.lower() for w in _HEADER_WORDS) and any(
            w in col_pts.lower() for w in _HEADER_WORDS
        ):
            continue
        if not col_q and not col_body and not col_pts:
            continue

        pts = _parse_decimal_points(col_pts)
        if pts is None:
            warnings.append(f"Row {idx} ({col_q or '?'}): unparseable points {col_pts!r}")
            continue
        items.append(RubricRowItem(question_label=col_q, criterion_text=col_body, points=pts))

    total = sum((it.points for it in items), Decimal("0"))
    if expected_total_points is not None and total != expected_total_points:
        warnings.append(
            f"Rubric points sum to {total}, expected {expected_total_points}"
        )

    return RubricParseReport(rows=tuple(items), total_points=total, warnings=tuple(warnings))
