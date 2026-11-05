import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request, status
from sqlalchemy import delete, func, select
from starlette.datastructures import FormData

from app.deps import AdminUser, SessionDep
from app.models import Difficulty, Exam, ExamQuestion, Question
from app.routers.admin.common import get_or_404, redirect
from app.scope import Scope, load_subject_tree, resolve_scope, scope_of, scope_options, scope_paths
from app.templating import flash, templates
from app.text import like_pattern

router = APIRouter()

PAGE_SIZE = 20
MIN_CHOICES, MAX_CHOICES = 2, 6
_CHOICE_ID = re.compile(r"^[a-z0-9]{1,16}$")


def _blank_choices(n: int = 4) -> list[dict[str, Any]]:
    return [{"id": "", "content": "", "is_correct": i == 0} for i in range(n)]


@dataclass
class QuestionForm:
    scope_key: str = ""
    difficulty: str = Difficulty.RECOGNITION.value
    content: str = ""
    explanation: str = ""
    choices: list[dict[str, Any]] = field(default_factory=_blank_choices)

    @classmethod
    def from_question(cls, q: Question) -> "QuestionForm":
        return cls(scope_of(q).key, q.difficulty.value, q.content, q.explanation or "", [dict(c) for c in q.choices])


async def _parse(session, form: FormData) -> tuple[QuestionForm, Scope | None, list[str]]:
    data = QuestionForm(
        scope_key=str(form.get("scope", "")),
        difficulty=str(form.get("difficulty", "")),
        content=str(form.get("content", "")).strip(),
        explanation=str(form.get("explanation", "")).strip(),
        choices=[],
    )
    errors: list[str] = []

    scope = await resolve_scope(session, data.scope_key)
    if scope is None:
        errors.append("Vui lòng chọn môn / chương / bài cho câu hỏi.")
    if data.difficulty not in Difficulty.__members__:
        errors.append("Mức độ không hợp lệ.")
    if not data.content:
        errors.append("Nội dung câu hỏi không được để trống.")

    ids = [str(v) for v in form.getlist("choice_id")]
    correct = str(form.get("correct", ""))
    seen: set[str] = set()
    for i, raw in enumerate(form.getlist("choice_content")):
        # Keep existing ids so stored answers stay valid; mint new ones for added rows.
        choice_id = ids[i] if i < len(ids) else ""
        if not _CHOICE_ID.match(choice_id) or choice_id in seen:
            choice_id = secrets.token_hex(4)
        seen.add(choice_id)
        data.choices.append({"id": choice_id, "content": str(raw).strip(), "is_correct": correct == str(i)})

    if not MIN_CHOICES <= len(data.choices) <= MAX_CHOICES:
        errors.append(f"Câu hỏi cần từ {MIN_CHOICES} đến {MAX_CHOICES} lựa chọn.")
    if any(not c["content"] for c in data.choices):
        errors.append("Nội dung các lựa chọn không được để trống.")
    if sum(c["is_correct"] for c in data.choices) != 1:
        errors.append("Hãy đánh dấu đúng 1 đáp án đúng.")
    return data, scope, errors


def _apply(question: Question, data: QuestionForm, scope: Scope) -> None:
    question.subject_id, question.chapter_id, question.lesson_id = scope.subject_id, scope.chapter_id, scope.lesson_id
    question.difficulty = Difficulty(data.difficulty)
    question.content = data.content
    question.explanation = data.explanation or None
    question.choices = data.choices


async def _exams_using(session, question_id: int) -> list[Exam]:
    stmt = select(Exam).join(ExamQuestion).where(ExamQuestion.question_id == question_id).order_by(Exam.title)
    return list((await session.scalars(stmt)).all())


async def _render_form(request, session, user, data: QuestionForm, question=None, errors=None):
    return templates.TemplateResponse(
        request,
        "admin/question_form.html",
        {
            "user": user,
            "form": data,
            "question": question,
            "errors": errors or [],
            "scope_options": scope_options(await load_subject_tree(session)),
            "used_in": await _exams_using(session, question.id) if question else [],
            "max_choices": MAX_CHOICES,
        },
        status_code=status.HTTP_400_BAD_REQUEST if errors else status.HTTP_200_OK,
    )


@router.get("/questions")
async def list_questions(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    scope: str = "",
    difficulty: str = "",
    q: str = "",
    page: int = 1,
):
    conditions = []
    if selected := await resolve_scope(session, scope):
        conditions += selected.conditions(Question)
    if difficulty in Difficulty.__members__:
        conditions.append(Question.difficulty == Difficulty(difficulty))
    if q.strip():
        conditions.append(Question.content.ilike(like_pattern(q.strip()), escape="\\"))

    total = await session.scalar(select(func.count()).select_from(Question).where(*conditions)) or 0
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(max(page, 1), pages)
    questions = (
        await session.scalars(
            select(Question)
            .where(*conditions)
            .order_by(Question.id.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
    ).all()

    subjects = await load_subject_tree(session)
    filters = {"scope": scope, "difficulty": difficulty, "q": q}
    return templates.TemplateResponse(
        request,
        "admin/questions.html",
        {
            "user": user,
            "questions": questions,
            "total": total,
            "page": page,
            "pages": pages,
            "filters": filters,
            "page_url": lambda p: "/admin/questions?" + urlencode({**filters, "page": p}),
            "scope_options": scope_options(subjects),
            "scope_paths": scope_paths(subjects),
            "scope_of": scope_of,
        },
    )


@router.get("/questions/new")
async def new_question(request: Request, user: AdminUser, session: SessionDep, scope: str = ""):
    return await _render_form(request, session, user, QuestionForm(scope_key=scope))


@router.post("/questions")
async def create_question(request: Request, user: AdminUser, session: SessionDep):
    form = await request.form()
    data, scope, errors = await _parse(session, form)
    if errors:
        return await _render_form(request, session, user, data, errors=errors)
    question = Question()
    _apply(question, data, scope)
    session.add(question)
    await session.commit()
    flash(request, f"Đã thêm câu hỏi #{question.id}.")
    if form.get("action") == "new":
        return redirect("/admin/questions/new?" + urlencode({"scope": scope.key}))
    return redirect("/admin/questions?" + urlencode({"scope": scope.key}))


@router.get("/questions/{question_id}/edit")
async def edit_question(request: Request, user: AdminUser, session: SessionDep, question_id: int):
    question = await get_or_404(session, Question, question_id)
    return await _render_form(request, session, user, QuestionForm.from_question(question), question)


@router.post("/questions/{question_id}")
async def update_question(request: Request, user: AdminUser, session: SessionDep, question_id: int):
    question = await get_or_404(session, Question, question_id)
    data, scope, errors = await _parse(session, await request.form())
    if scope and scope.subject_id != question.subject_id and await _exams_using(session, question_id):
        errors.append("Câu hỏi đang nằm trong đề thi nên không thể chuyển sang môn khác.")
    if errors:
        return await _render_form(request, session, user, data, question, errors)
    _apply(question, data, scope)
    await session.commit()
    flash(request, f"Đã lưu câu hỏi #{question_id}.")
    return redirect("/admin/questions?" + urlencode({"scope": scope.key}))


@router.post("/questions/{question_id}/delete")
async def delete_question(request: Request, session: SessionDep, question_id: int):
    await get_or_404(session, Question, question_id)
    if used_in := await _exams_using(session, question_id):
        names = ", ".join(f"“{e.title}”" for e in used_in)
        flash(request, f"Không thể xoá câu #{question_id}: đang được dùng trong đề {names}.", "error")
        return redirect(f"/admin/questions/{question_id}/edit")
    await session.execute(delete(Question).where(Question.id == question_id))
    await session.commit()
    flash(request, f"Đã xoá câu hỏi #{question_id}.")
    return redirect("/admin/questions")
