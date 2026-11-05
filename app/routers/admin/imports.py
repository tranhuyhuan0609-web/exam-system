"""Bulk question import: upload → preview (nothing saved) → confirm (selected questions saved)."""

import json
from dataclasses import dataclass
from pathlib import PurePath
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from starlette.datastructures import FormData

from app.deps import AdminUser, SessionDep
from app.domain.question_hash import content_hash
from app.import_templates import TEXT_RULES, sample_docx, sample_text, sample_xlsx
from app.models import Difficulty, Exam, ExamMode, ExamQuestion, Question, QuestionType, SourceType
from app.question_import import (
    MAX_FILE_BYTES,
    MAX_QUESTIONS,
    SUPPORTED_EXTENSIONS,
    ImportFileError,
    ParsedQuestion,
    parse_file,
    validate,
)
from app.routers.admin.common import clean_text, redirect
from app.scope import Scope, load_categories, resolve_scope, scope_options, scope_paths
from app.templating import flash, templates

router = APIRouter()

TextForm = Annotated[str, Form()]
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass
class ImportSettings:
    scope_key: str = ""
    default_difficulty: str = Difficulty.UNDERSTANDING.value
    create_exam: bool = False
    exam_title: str = ""
    duration_minutes: str = "15"
    publish: bool = False

    @classmethod
    def from_form(cls, form: FormData) -> "ImportSettings":
        return cls(
            scope_key=str(form.get("scope", "")),
            default_difficulty=str(form.get("default_difficulty", "")),
            create_exam=form.get("create_exam") == "on",
            exam_title=str(form.get("exam_title", "")).strip(),
            duration_minutes=str(form.get("duration_minutes", "")).strip(),
            publish=form.get("publish") == "on",
        )

    async def check(self, session) -> tuple[Scope | None, list[str]]:
        errors = []
        scope = await resolve_scope(session, self.scope_key)
        if scope is None:
            errors.append("Vui lòng chọn môn / chương / bài để lưu câu hỏi.")
        if self.default_difficulty not in Difficulty.__members__:
            errors.append("Mức độ mặc định không hợp lệ.")
        if self.create_exam:
            if not clean_text(self.exam_title, 300):
                errors.append("Tên đề không hợp lệ (1–300 ký tự).")
            if not (self.duration_minutes.isdigit() and 1 <= int(self.duration_minutes) <= 600):
                errors.append("Thời gian làm bài phải từ 1 đến 600 phút.")
        return scope, errors


async def _render_form(request, session, user, settings: ImportSettings, errors=None):
    return templates.TemplateResponse(
        request,
        "admin/import_form.html",
        {
            "user": user,
            "settings": settings,
            "errors": errors or [],
            "scope_options": scope_options(await load_categories(session)),
            "rules": TEXT_RULES,
            "max_mb": MAX_FILE_BYTES // (1024 * 1024),
            "max_questions": MAX_QUESTIONS,
            "extensions": ", ".join(SUPPORTED_EXTENSIONS),
        },
        status_code=status.HTTP_400_BAD_REQUEST if errors else status.HTTP_200_OK,
    )


@router.get("/questions/import")
async def import_form(request: Request, user: AdminUser, session: SessionDep, scope: str = ""):
    return await _render_form(request, session, user, ImportSettings(scope_key=scope))


@router.get("/questions/import/template.xlsx")
async def template_xlsx():
    return Response(sample_xlsx(), media_type=XLSX_TYPE, headers=_attachment("mau-cau-hoi.xlsx"))


@router.get("/questions/import/template.docx")
async def template_docx():
    return Response(sample_docx(), media_type=DOCX_TYPE, headers=_attachment("mau-cau-hoi.docx"))


@router.get("/questions/import/template.txt")
async def template_txt():
    return Response(
        sample_text().encode("utf-8-sig"),
        media_type="text/plain; charset=utf-8",
        headers=_attachment("mau-cau-hoi.txt"),
    )


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.post("/questions/import/preview")
async def import_preview(
    request: Request, user: AdminUser, session: SessionDep, file: Annotated[UploadFile | None, File()] = None
):
    form = await request.form()
    settings = ImportSettings.from_form(form)
    if settings.create_exam and not settings.exam_title and file and file.filename:
        settings.exam_title = PurePath(file.filename).stem[:300]
    scope, errors = await settings.check(session)

    questions: list[ParsedQuestion] = []
    data = await file.read(MAX_FILE_BYTES + 1) if file else b""
    if not file or not file.filename or not data:
        errors.append("Vui lòng chọn file để tải lên.")
    else:
        try:
            questions = parse_file(file.filename, data)
        except ImportFileError as exc:
            errors.append(str(exc))
    if errors:
        return await _render_form(request, session, user, settings, errors)

    # Flag exact duplicates (same normalised text and options) already in the bank, or repeated within the file.
    hashes = [content_hash(q.type, q.content, q.choices) for q in questions]
    existing = set(
        (await session.scalars(select(Question.content_hash).where(Question.content_hash.in_(hashes)))).all()
    )
    seen: set[str] = set()
    for q, digest in zip(questions, hashes, strict=True):
        q.duplicate = digest in existing or digest in seen
        seen.add(digest)

    subjects = await load_categories(session)
    return templates.TemplateResponse(
        request,
        "admin/import_preview.html",
        {
            "user": user,
            "settings": settings,
            "scope_path": scope_paths(subjects).get(scope.key, ""),
            "filename": file.filename,
            "questions": questions,
            "valid_count": sum(q.ok for q in questions),
            "error_count": sum(not q.ok for q in questions),
            "duplicate_count": sum(q.ok and q.duplicate for q in questions),
            "payload": json.dumps([q.to_dict() for q in questions], ensure_ascii=False),
            "letters": "ABCDEF",
        },
    )


@router.post("/questions/import/confirm")
async def import_confirm(request: Request, user: AdminUser, session: SessionDep):
    form = await request.form()
    settings = ImportSettings.from_form(form)
    scope, errors = await settings.check(session)
    try:
        payload = json.loads(str(form.get("payload", "")))
    except ValueError:
        payload = None
    if not isinstance(payload, list) or len(payload) > MAX_QUESTIONS:
        errors.append("Dữ liệu nhập không hợp lệ, vui lòng tải file lên lại.")
        payload = []
    if errors:
        for e in errors:
            flash(request, e, "error")
        return redirect("/admin/questions/import")

    selected = sorted({int(v) for v in form.getlist("selected") if str(v).isdigit()})
    default_difficulty = Difficulty(settings.default_difficulty)
    created: list[Question] = []
    for index in selected:
        if index >= len(payload) or not isinstance(payload[index], dict):
            continue
        parsed = ParsedQuestion.from_dict(payload[index])
        if validate(parsed):  # re-check: the payload came back from the browser
            continue
        created.append(
            Question(
                root_id=scope.root_id,
                category_id=scope.category_id,
                difficulty=Difficulty(parsed.difficulty) if parsed.difficulty else default_difficulty,
                question_type=QuestionType(parsed.type),
                content=parsed.content.strip(),
                choices=parsed.stored_choices(),
                accepted_answers=parsed.answers if parsed.type == QuestionType.SHORT_ANSWER else None,
                explanation=parsed.explanation.strip() or None,
                source_type=SourceType.FILE_IMPORT,
                content_hash=content_hash(parsed.type, parsed.content.strip(), parsed.choices),
            )
        )
    if not created:
        flash(request, "Không có câu hợp lệ nào được chọn để nhập.", "error")
        return redirect("/admin/questions/import?" + urlencode({"scope": scope.key}))

    session.add_all(created)
    await session.flush()

    exam = None
    if settings.create_exam:
        exam = Exam(
            root_id=scope.root_id,
            category_id=scope.category_id,
            title=clean_text(settings.exam_title, 300),
            exam_mode=ExamMode.PRACTICE,
            duration_seconds=int(settings.duration_minutes) * 60,
            is_published=settings.publish,
            created_by=user.id,
        )
        session.add(exam)
        await session.flush()
        session.add_all(
            ExamQuestion(exam_id=exam.id, question_id=q.id, position=pos) for pos, q in enumerate(created, start=1)
        )
    await session.commit()

    total = await session.scalar(select(func.count()).select_from(Question).where(*scope.conditions(Question)))
    if exam:
        flash(request, f"Đã nhập {len(created)} câu hỏi và tạo đề “{exam.title}”.")
        return redirect(f"/admin/exams/{exam.id}")
    flash(request, f"Đã nhập {len(created)} câu hỏi (phạm vi này hiện có {total} câu).")
    return redirect("/admin/questions?" + urlencode({"scope": scope.key}))
