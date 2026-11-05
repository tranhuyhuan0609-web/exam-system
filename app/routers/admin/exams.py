from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Request, status
from sqlalchemy import delete, func, select
from starlette.datastructures import FormData

from app.deps import AdminUser, SessionDep
from app.models import Difficulty, Exam, ExamAttempt, ExamMode, ExamQuestion, Question
from app.routers.admin.common import clean_text, get_or_404, move, redirect
from app.scope import Scope, load_subject_tree, resolve_scope, scope_of, scope_options, scope_paths
from app.templating import flash, templates

router = APIRouter()

MAX_DURATION_MINUTES = 600
PICKER_LIMIT = 200


@dataclass
class ExamForm:
    title: str = ""
    description: str = ""
    scope_key: str = ""
    duration_minutes: str = "15"
    max_attempts: str = ""
    is_published: bool = False

    @classmethod
    def from_exam(cls, e: Exam) -> "ExamForm":
        return cls(
            e.title,
            e.description or "",
            scope_of(e).key,
            str(e.duration_seconds // 60),
            "" if e.max_attempts is None else str(e.max_attempts),
            e.is_published,
        )


@dataclass
class ExamValues:
    title: str
    description: str | None
    scope: Scope
    duration_seconds: int
    max_attempts: int | None
    is_published: bool


async def _parse(session, form: FormData) -> tuple[ExamForm, ExamValues | None, list[str]]:
    data = ExamForm(
        title=str(form.get("title", "")),
        description=str(form.get("description", "")),
        scope_key=str(form.get("scope", "")),
        duration_minutes=str(form.get("duration_minutes", "")).strip(),
        max_attempts=str(form.get("max_attempts", "")).strip(),
        is_published=form.get("is_published") == "on",
    )
    errors = []
    title = clean_text(data.title, 300)
    if not title:
        errors.append("Tên đề không hợp lệ (1–300 ký tự).")
    scope = await resolve_scope(session, data.scope_key)
    if scope is None:
        errors.append("Vui lòng chọn môn / chương / bài cho đề.")
    duration = int(data.duration_minutes) if data.duration_minutes.isdigit() else 0
    if not 1 <= duration <= MAX_DURATION_MINUTES:
        errors.append(f"Thời gian làm bài phải từ 1 đến {MAX_DURATION_MINUTES} phút.")
    max_attempts = None
    if data.max_attempts:
        if data.max_attempts.isdigit() and int(data.max_attempts) >= 1:
            max_attempts = int(data.max_attempts)
        else:
            errors.append("Số lần làm tối đa phải là số nguyên ≥ 1 (để trống = không giới hạn).")
    if errors:
        return data, None, errors
    return (
        data,
        ExamValues(title, data.description.strip() or None, scope, duration * 60, max_attempts, data.is_published),
        [],
    )


def _apply(exam: Exam, v: ExamValues) -> None:
    exam.title, exam.description = v.title, v.description
    exam.subject_id, exam.chapter_id, exam.lesson_id = v.scope.subject_id, v.scope.chapter_id, v.scope.lesson_id
    exam.duration_seconds, exam.max_attempts, exam.is_published = v.duration_seconds, v.max_attempts, v.is_published


async def _items(session, exam_id: int) -> list[tuple[ExamQuestion, Question]]:
    stmt = (
        select(ExamQuestion, Question)
        .join(Question, Question.id == ExamQuestion.question_id)
        .where(ExamQuestion.exam_id == exam_id)
        .order_by(ExamQuestion.position, Question.id)
    )
    return [(eq, q) for eq, q in (await session.execute(stmt)).all()]


async def _render_new(request, session, user, data: ExamForm, errors=None):
    return templates.TemplateResponse(
        request,
        "admin/exam_new.html",
        {
            "user": user,
            "form": data,
            "errors": errors or [],
            "scope_options": scope_options(await load_subject_tree(session)),
        },
        status_code=status.HTTP_400_BAD_REQUEST if errors else status.HTTP_200_OK,
    )


async def _render_detail(
    request, session, user, exam: Exam, data: ExamForm, errors=None, pick_scope: str = "", pick_difficulty: str = ""
):
    items = await _items(session, exam.id)
    in_exam = {q.id for _, q in items}

    pick_conditions = [Question.subject_id == exam.subject_id, Question.is_verified]
    picked_scope = await resolve_scope(session, pick_scope or scope_of(exam).key)
    if picked_scope and picked_scope.subject_id == exam.subject_id:
        pick_conditions += picked_scope.conditions(Question)
    if pick_difficulty in Difficulty.__members__:
        pick_conditions.append(Question.difficulty == Difficulty(pick_difficulty))
    if in_exam:
        pick_conditions.append(Question.id.not_in(in_exam))
    candidates = (
        await session.scalars(select(Question).where(*pick_conditions).order_by(Question.id).limit(PICKER_LIMIT))
    ).all()

    all_subjects = await load_subject_tree(session)
    own_subject = [s for s in all_subjects if s.id == exam.subject_id]
    attempts = await session.scalar(select(func.count()).select_from(ExamAttempt).where(ExamAttempt.exam_id == exam.id))
    return templates.TemplateResponse(
        request,
        "admin/exam_detail.html",
        {
            "user": user,
            "exam": exam,
            "form": data,
            "errors": errors or [],
            "items": items,
            "difficulty_counts": Counter(q.difficulty for _, q in items),
            "candidates": candidates,
            "pick_scope": picked_scope.key if picked_scope else "",
            "pick_difficulty": pick_difficulty,
            "scope_options": scope_options(all_subjects),
            "pick_scope_options": scope_options(own_subject),
            "scope_paths": scope_paths(all_subjects),
            "scope_of": scope_of,
            "attempt_count": attempts or 0,
        },
        status_code=status.HTTP_400_BAD_REQUEST if errors else status.HTTP_200_OK,
    )


@router.get("/exams")
async def list_exams(request: Request, user: AdminUser, session: SessionDep):
    question_counts = func.count(ExamQuestion.question_id)
    rows = (
        await session.execute(
            select(Exam, question_counts)
            .outerjoin(ExamQuestion, ExamQuestion.exam_id == Exam.id)
            .group_by(Exam.id)
            .order_by(Exam.id.desc())
        )
    ).all()
    subjects = await load_subject_tree(session)
    return templates.TemplateResponse(
        request,
        "admin/exams.html",
        {"user": user, "rows": rows, "scope_paths": scope_paths(subjects), "scope_of": scope_of},
    )


@router.get("/exams/new")
async def new_exam(request: Request, user: AdminUser, session: SessionDep, scope: str = ""):
    return await _render_new(request, session, user, ExamForm(scope_key=scope))


@router.post("/exams")
async def create_exam(request: Request, user: AdminUser, session: SessionDep):
    data, values, errors = await _parse(session, await request.form())
    if errors:
        return await _render_new(request, session, user, data, errors)
    values.is_published = False  # an exam can only be published once it has questions
    exam = Exam(exam_mode=ExamMode.PRACTICE, created_by=user.id)
    _apply(exam, values)
    session.add(exam)
    await session.commit()
    flash(request, "Đã tạo đề. Giờ hãy thêm câu hỏi vào đề.")
    return redirect(f"/admin/exams/{exam.id}")


@router.get("/exams/{exam_id}")
async def exam_detail(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    exam_id: int,
    pick_scope: str = "",
    pick_difficulty: str = "",
):
    exam = await get_or_404(session, Exam, exam_id)
    return await _render_detail(
        request, session, user, exam, ExamForm.from_exam(exam), pick_scope=pick_scope, pick_difficulty=pick_difficulty
    )


@router.post("/exams/{exam_id}")
async def update_exam(request: Request, user: AdminUser, session: SessionDep, exam_id: int):
    exam = await get_or_404(session, Exam, exam_id)
    data, values, errors = await _parse(session, await request.form())
    if values:
        items = await _items(session, exam_id)
        if values.scope.subject_id != exam.subject_id and items:
            errors.append("Đề đã có câu hỏi nên không thể chuyển sang môn khác. Hãy gỡ hết câu hỏi trước.")
        if values.is_published and not items:
            errors.append("Đề phải có ít nhất 1 câu hỏi mới công khai được.")
    if errors:
        return await _render_detail(request, session, user, exam, data, errors)
    _apply(exam, values)
    await session.commit()
    flash(request, "Đã lưu cài đặt đề.")
    return redirect(f"/admin/exams/{exam_id}")


@router.post("/exams/{exam_id}/questions")
async def add_questions(request: Request, session: SessionDep, exam_id: int):
    exam = await get_or_404(session, Exam, exam_id)
    form = await request.form()
    ids = {int(v) for v in form.getlist("question_id") if str(v).isdigit()}
    back = f"/admin/exams/{exam_id}?" + urlencode(
        {"pick_scope": form.get("pick_scope", ""), "pick_difficulty": form.get("pick_difficulty", "")}
    )
    if not ids:
        flash(request, "Chưa chọn câu hỏi nào.", "error")
        return redirect(back)

    existing = set(
        (await session.scalars(select(ExamQuestion.question_id).where(ExamQuestion.exam_id == exam_id))).all()
    )
    # Only verified questions from the exam's own subject can be added.
    valid = (
        await session.scalars(
            select(Question.id)
            .where(Question.id.in_(ids - existing), Question.subject_id == exam.subject_id, Question.is_verified)
            .order_by(Question.id)
        )
    ).all()
    last = await session.scalar(select(func.max(ExamQuestion.position)).where(ExamQuestion.exam_id == exam_id)) or 0
    for offset, qid in enumerate(valid, start=1):
        session.add(ExamQuestion(exam_id=exam_id, question_id=qid, position=last + offset))
    await session.commit()
    skipped = len(ids) - len(valid)
    flash(request, f"Đã thêm {len(valid)} câu vào đề." + (f" Bỏ qua {skipped} câu không hợp lệ." if skipped else ""))
    return redirect(back)


@router.post("/exams/{exam_id}/questions/{question_id}/remove")
async def remove_question(request: Request, session: SessionDep, exam_id: int, question_id: int):
    exam = await get_or_404(session, Exam, exam_id)
    items = [eq for eq, _ in await _items(session, exam_id)]
    target = next((eq for eq in items if eq.question_id == question_id), None)
    if target is None:
        return redirect(f"/admin/exams/{exam_id}")
    if exam.is_published and len(items) == 1:
        flash(request, "Đề đang công khai phải còn ít nhất 1 câu. Hãy ẩn đề trước khi gỡ câu cuối.", "error")
        return redirect(f"/admin/exams/{exam_id}")
    await session.delete(target)
    items.remove(target)
    for pos, eq in enumerate(items, start=1):
        eq.position = pos
    await session.commit()
    flash(request, f"Đã gỡ câu #{question_id} khỏi đề.")
    return redirect(f"/admin/exams/{exam_id}")


@router.post("/exams/{exam_id}/questions/{question_id}/move")
async def move_question(session: SessionDep, exam_id: int, question_id: int, request: Request):
    await get_or_404(session, Exam, exam_id)
    items = [eq for eq, _ in await _items(session, exam_id)]
    target = next((eq for eq in items if eq.question_id == question_id), None)
    if target is not None:
        move(items, target, str((await request.form()).get("direction", "")))
        await session.commit()
    return redirect(f"/admin/exams/{exam_id}")


@router.post("/exams/{exam_id}/delete")
async def delete_exam(request: Request, session: SessionDep, exam_id: int):
    exam = await get_or_404(session, Exam, exam_id)
    attempts = await session.scalar(select(func.count()).select_from(ExamAttempt).where(ExamAttempt.exam_id == exam_id))
    if attempts:
        flash(request, f"Không thể xoá: đề đã có {attempts} lượt làm bài. Hãy ẩn đề thay vì xoá.", "error")
        return redirect(f"/admin/exams/{exam_id}")
    await session.execute(delete(Exam).where(Exam.id == exam_id))  # exam_questions cascade in DB
    await session.commit()
    flash(request, f"Đã xoá đề “{exam.title}”.")
    return redirect("/admin/exams")
