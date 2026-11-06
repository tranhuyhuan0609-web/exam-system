"""Admin: the data warehouse at a glance — how big the bank and the library are, where coverage is thin
(subject x grade, grade 1 to university), and what is waiting in the pipelines (crawler, AI import, AI quota)."""

import re
from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import func, select, text

from app.deps import AdminUser, SessionDep
from app.models import (
    AIUsage,
    AnswerKey,
    Category,
    CrawledQuestion,
    Exam,
    ImportJob,
    JobStatus,
    LibraryDocument,
    Question,
)
from app.routers.admin.crawler import STATUS_LABELS
from app.routers.library import KIND_LABELS
from app.templating import templates

router = APIRouter()

GRADES = [str(g) for g in range(1, 13)]
EXTRA = "Ôn thi / HSG / ĐH"  # roots without a grade: exam preparation, gifted students, university
_GRADE = re.compile(r"^(.*?)\s*(?:lớp\s*)?(\d{1,2})$", re.IGNORECASE)
_PREFIX = re.compile(r"^(?:ôn thi thpt|luyện thi hsg|đgnl|đgtd)\s*[–-]\s*", re.IGNORECASE)
SOURCE_LABELS = {"MANUAL": "Nhập tay", "FILE_IMPORT": "Nhập file", "AI_UPLOAD": "AI nhập đề", "CRAWLER": "Crawler"}
TYPE_LABELS = {
    "MULTIPLE_CHOICE": "Trắc nghiệm",
    "TRUE_FALSE": "Đúng/sai",
    "SHORT_ANSWER": "Trả lời ngắn",
    "ESSAY": "Tự luận",
}


def subject_and_grade(root_name: str) -> tuple[str, str]:
    """ "Vật lí 11" -> ("Vật lí", "11"); "Ôn thi THPT – Toán" -> ("Toán", EXTRA); "Luyện thi HSG" -> (it, EXTRA)."""
    name = " ".join(root_name.split())
    match = _GRADE.match(name)
    if match and 1 <= int(match.group(2)) <= 12 and match.group(1):
        return match.group(1).strip(), match.group(2)
    return _PREFIX.sub("", name).strip() or name, EXTRA


def coverage(rows: list[tuple[str, int]]) -> tuple[list[str], list[dict]]:
    """(columns, one row per subject with counts per column and a total), subjects with most questions first."""
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for root_name, count in rows:
        subject, column = subject_and_grade(root_name)
        table[subject][column] += count
    columns = [*GRADES, EXTRA]
    out = [
        {"subject": subject, "cells": [cells.get(c, 0) for c in columns], "total": sum(cells.values())}
        for subject, cells in table.items()
    ]
    return columns, sorted(out, key=lambda r: -r["total"])


@router.get("/data")
async def data_overview(request: Request, user: AdminUser, session: SessionDep):
    names = dict((await session.execute(select(Category.id, Category.name).where(Category.parent_id.is_(None)))).all())
    per_root = (await session.execute(select(Question.root_id, func.count()).group_by(Question.root_id))).all()
    columns, matrix = coverage([(names.get(root, "?"), n) for root, n in per_root])
    peak = max((c for row in matrix for c in row["cells"]), default=0)

    by_source = dict(
        (await session.execute(select(Question.source_type, func.count()).group_by(Question.source_type))).all()
    )
    by_type = dict(
        (await session.execute(select(Question.question_type, func.count()).group_by(Question.question_type))).all()
    )
    crawled = dict(
        (await session.execute(select(CrawledQuestion.status, func.count()).group_by(CrawledQuestion.status))).all()
    )
    jobs = dict((await session.execute(select(ImportJob.status, func.count()).group_by(ImportJob.status))).all())
    waiting_admin = await session.scalar(
        select(func.count())
        .select_from(ImportJob)
        .where(ImportJob.status == JobStatus.DONE, ImportJob.stage.contains("Chờ admin duyệt"))
    )
    library = (
        await session.execute(
            # Grouped by position: the "listed only" test written twice would be two different SQL parameters
            select(
                LibraryDocument.kind, LibraryDocument.status, LibraryDocument.storage_key == "", func.count()
            ).group_by(text("1, 2, 3"))
        )
    ).all()
    library_rows: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for kind, status, listed_only, n in library:
        row = library_rows[KIND_LABELS[kind]]
        row["listed" if listed_only else status.value] += n
        row["total"] += n
    today = datetime.now(UTC).date()
    usage = (await session.scalars(select(AIUsage).where(AIUsage.day == today).order_by(AIUsage.provider))).all()
    return templates.TemplateResponse(
        request,
        "admin/data.html",
        {
            "user": user,
            "tiles": {
                "questions": sum(by_source.values()),
                "exams": await session.scalar(select(func.count()).select_from(Exam).where(Exam.is_published)),
                "library": sum(r["total"] for r in library_rows.values()),
                "keys": await session.scalar(select(func.count()).select_from(AnswerKey)),
            },
            "columns": columns,
            "matrix": matrix,
            "peak": peak,
            "by_source": {SOURCE_LABELS.get(k.value, k.value): v for k, v in by_source.items()},
            "by_type": {TYPE_LABELS.get(k.value, k.value): v for k, v in by_type.items()},
            "crawled": [
                (STATUS_LABELS.get(k.value, k.value), v) for k, v in sorted(crawled.items(), key=lambda kv: -kv[1])
            ],
            "jobs": {k.value: v for k, v in jobs.items()},
            "waiting_admin": waiting_admin,
            "library_rows": sorted(library_rows.items(), key=lambda kv: -kv[1]["total"]),
            "usage": usage,
        },
    )
