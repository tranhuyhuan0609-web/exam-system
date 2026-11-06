"""Admin data warehouse page: coverage subject x grade (grade 1 to university), queues, library."""

import pytest

from app.models import Difficulty, Question
from app.pipeline.use_cases.toc import ensure_path
from app.routers.admin.data import EXTRA, coverage, subject_and_grade


@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("Vật lí 11", ("Vật lí", "11")),
        ("Toán 1", ("Toán", "1")),
        ("Tiếng Việt lớp 3", ("Tiếng Việt", "3")),
        ("Ôn thi THPT – Toán", ("Toán", EXTRA)),
        ("Luyện thi HSG", ("Luyện thi HSG", EXTRA)),
        ("Đề thi THPT 2024", ("Đề thi THPT 2024", EXTRA)),  # a year, not a grade
    ],
)
def test_subject_and_grade(root, expected):
    assert subject_and_grade(root) == expected


def test_coverage_puts_each_subject_on_one_row():
    columns, rows = coverage([("Toán 10", 5), ("Toán 11", 2), ("Ôn thi THPT – Toán", 3), ("Vật lí 10", 4)])
    assert rows[0]["subject"] == "Toán" and rows[0]["total"] == 10
    assert rows[0]["cells"][columns.index("10")] == 5 and rows[0]["cells"][columns.index(EXTRA)] == 3


def test_data_page_shows_the_bank_by_subject_and_grade(admin_client, db):
    async def make(s):
        for path in (["Toán 10"], ["Vật lí 11"]):
            category = await ensure_path(s, path)
            s.add(
                Question(
                    root_id=category.root_id,
                    category_id=category.id,
                    difficulty=Difficulty.RECOGNITION,
                    content=f"Câu của {path[0]}",
                    choices=[
                        {"id": "a", "content": "1", "is_correct": True},
                        {"id": "b", "content": "2", "is_correct": False},
                    ],
                )
            )

    db(make)
    page = admin_client.get("/admin/data")
    assert page.status_code == 200
    assert "Độ phủ: môn × lớp" in page.text and "Vật lí" in page.text and "Lớp 11" in page.text
