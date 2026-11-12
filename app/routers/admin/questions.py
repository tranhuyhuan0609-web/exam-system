import json
from dataclasses import dataclass, field
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Body, HTTPException, Request, status
from sqlalchemy import delete, func, select
from starlette.datastructures import FormData

from app import question_rules as rules
from app.deps import AdminUser, SessionDep
from app.domain.question_hash import hash_of_choices
from app.models import Category, QUESTION_MAX_CHOICES, Difficulty, Exam, ExamQuestion, Question, QuestionType
from app.routers.admin.common import get_or_404, redirect
from app.scope import (
    Scope,
    SectionScope,
    TargetScope,
    build_drilldown_conditions,
    get_all_scope_paths,
    get_drilldown_catalog,
    get_section_scope_data,
    load_categories,
    resolve_scope,
    scope_of,
    scope_options,
)
from app.templating import flash, templates
from app.text import like_pattern

router = APIRouter()

PAGE_SIZE = 20
MAX_CHOICES = QUESTION_MAX_CHOICES


def _blank_choices(n: int = 4) -> list[dict[str, Any]]:
    return [{"id": "", "content": "", "is_correct": i == 0} for i in range(n)]


@dataclass
class QuestionForm:
    scope_key: str = ""
    question_type: str = QuestionType.MULTIPLE_CHOICE.value
    difficulty: str = Difficulty.RECOGNITION.value
    content: str = ""
    explanation: str = ""
    choices: list[dict[str, Any]] = field(default_factory=_blank_choices)
    accepted_answers: str = ""  # "30; 30,0"
    points: str = ""  # essay points, blank = default

    @classmethod
    def from_question(cls, q: Question) -> "QuestionForm":
        return cls(
            scope_key=scope_of(q).key,
            question_type=q.question_type.value,
            difficulty=q.difficulty.value,
            content=q.content,
            explanation=q.explanation or "",
            choices=[dict(c) for c in q.choices] or _blank_choices(),
            accepted_answers="; ".join(q.accepted_answers or []),
            points="" if q.points is None else f"{q.points:g}",
        )


def _truth(value: Any) -> bool | None:
    return {"1": True, "0": False}.get(str(value))


async def _parse(session, form: FormData) -> tuple[QuestionForm, Scope | None, list[str]]:
    question_type = str(form.get("question_type", QuestionType.MULTIPLE_CHOICE.value))
    if question_type not in QuestionType.__members__:
        question_type = QuestionType.MULTIPLE_CHOICE.value
    data = QuestionForm(
        scope_key=str(form.get("scope", "")),
        question_type=question_type,
        difficulty=str(form.get("difficulty", "")),
        content=str(form.get("content", "")).strip(),
        explanation=str(form.get("explanation", "")).strip(),
        accepted_answers=str(form.get("accepted_answers", "")).strip(),
        points=str(form.get("points", "")).strip(),
    )
    errors: list[str] = []
    scope = await resolve_scope(session, data.scope_key)
    if scope is None:
        errors.append("Vui lòng chọn môn / chương / bài cho câu hỏi.")
    if data.difficulty not in Difficulty.__members__:
        errors.append("Mức độ không hợp lệ.")

    contents = [str(v).strip() for v in form.getlist("choice_content")]
    ids = [str(v) for v in form.getlist("choice_id")]
    correct_raw = str(form.get("correct", ""))
    correct = int(correct_raw) if correct_raw.isdigit() else None
    truths = [_truth(v) for v in form.getlist("choice_truth")]
    accepted = rules.split_accepted(data.accepted_answers)
    if question_type in (QuestionType.SHORT_ANSWER, QuestionType.ESSAY):
        contents, ids = [], []
    if question_type == QuestionType.ESSAY and (problem := rules.parse_essay_points(data.points)[1]):
        errors.append(problem)

    errors += rules.validate(question_type, data.content, contents, correct, truths, accepted)
    # Keep existing ids so stored answers stay valid; new rows get fresh ids.
    data.choices = rules.build_choices(question_type, contents, correct, truths, ids)
    return data, scope, errors


def _apply(question: Question, data: QuestionForm, scope: Scope) -> None:
    question.root_id, question.category_id = scope.root_id, scope.category_id
    question.question_type = QuestionType(data.question_type)
    question.difficulty = Difficulty(data.difficulty)
    question.content = data.content
    question.explanation = data.explanation or None
    question.choices = data.choices
    question.accepted_answers = (
        rules.split_accepted(data.accepted_answers) if data.question_type == QuestionType.SHORT_ANSWER else None
    )
    question.content_hash = hash_of_choices(data.question_type, data.content, data.choices)
    question.points = rules.parse_essay_points(data.points)[0] if data.question_type == QuestionType.ESSAY else None


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
            "scope_options": scope_options(await load_categories(session)),
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
    section: str = "",
    grade: str = "",
    subject_id: str = "",
    chapter_id: str = "",
    lesson_id: str = "",
    target: str = "",
    exam_subject: str = "",
    scope: str = "",
    difficulty: str = "",
    qtype: str = "",
    verified: str = "",
    q: str = "",
    page: int = 1,
):
    # Backward compatibility: resolve legacy or direct `scope` parameter
    if scope and not (grade or subject_id or chapter_id or lesson_id or target):
        if scope.startswith("c:"):
            raw_id = scope[2:]
            if raw_id.isdigit():
                c_item = await session.get(Category, int(raw_id))
                if c_item:
                    section = "curriculum"
                    if c_item.depth == 2:
                        lesson_id = str(c_item.id)
                        chapter_id = str(c_item.parent_id or "")
                    elif c_item.depth == 1:
                        chapter_id = str(c_item.id)
                    elif c_item.depth == 0:
                        subject_id = str(c_item.id)
        elif scope.startswith("target:"):
            t_code = scope[7:]
            if any(h in t_code for h in ("HSG", "OLYMPIAD")):
                section = "olympiad"
            else:
                section = "major_exam"
            target = t_code
        elif scope == "section:major_exam":
            section = "major_exam"
        elif scope == "section:olympiad":
            section = "olympiad"

    if not section:
        section = "all"

    # Reset cross-section filter parameters to avoid leaks between tabs
    if section in ("major_exam", "olympiad"):
        grade = ""
        subject_id = ""
        chapter_id = ""
        lesson_id = ""
    elif section == "curriculum":
        target = ""
        exam_subject = ""
    elif section == "all":
        grade = ""
        subject_id = ""
        chapter_id = ""
        lesson_id = ""
        target = ""
        exam_subject = ""

    # Build primary drill-down conditions and breadcrumb
    conditions, breadcrumb_label, active_drilldown = await build_drilldown_conditions(
        session,
        section=section,
        grade=grade,
        subject_id=subject_id,
        chapter_id=chapter_id,
        lesson_id=lesson_id,
        target=target,
        exam_subject=exam_subject,
    )

    # Secondary filters
    if verified in ("yes", "no"):
        conditions.append(Question.is_verified == (verified == "yes"))
    if qtype in QuestionType.__members__:
        conditions.append(Question.question_type == QuestionType(qtype))
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

    # Pre-fetch KnowledgeUnit names and Target Tags for fast multi-dimensional badge rendering
    q_ids = [item.id for item in questions]
    ku_ids = [item.knowledge_unit_id for item in questions if item.knowledge_unit_id]
    ku_map: dict[int, str] = {}
    if ku_ids:
        from app.models import KnowledgeUnit
        ku_rows = (await session.execute(
            select(KnowledgeUnit.id, KnowledgeUnit.name).where(KnowledgeUnit.id.in_(ku_ids))
        )).all()
        ku_map = {row[0]: row[1] for row in ku_rows}

    q_targets_map: dict[int, list[str]] = {}
    if q_ids:
        from app.models import ExamTarget, QuestionTargetTag
        target_rows = (await session.execute(
            select(QuestionTargetTag.question_id, ExamTarget.code)
            .join(ExamTarget, QuestionTargetTag.target_id == ExamTarget.id)
            .where(QuestionTargetTag.question_id.in_(q_ids))
        )).all()
        for q_id, t_code in target_rows:
            q_targets_map.setdefault(q_id, []).append(t_code)

    # Heuristic fallback for existing un-tagged questions based on knowledge linking
    from app.application.knowledge_linker import infer_knowledge_metadata
    for item_q in questions:
        inferred = infer_knowledge_metadata(item_q.content, item_q.difficulty)
        if item_q.id not in q_targets_map or not q_targets_map[item_q.id]:
            q_targets_map[item_q.id] = [t for t in inferred.get("suitable_targets", []) if t != "SGK_KNTT"]
        if item_q.id not in ku_map and inferred.get("topic_name"):
            ku_map[item_q.id] = inferred["topic_name"]

    # Determine effective scope key for action buttons (+ Thêm câu hỏi, 🤖 AI nhập đề)
    if lesson_id:
        effective_scope = f"c:{lesson_id}"
    elif chapter_id:
        effective_scope = f"c:{chapter_id}"
    elif subject_id:
        effective_scope = f"c:{subject_id}"
    elif target:
        effective_scope = f"target:{target}"
    else:
        effective_scope = scope

    catalog = await get_drilldown_catalog(session)
    all_roots = await load_categories(session)
    all_paths = await get_all_scope_paths(session, all_roots)

    filters = {
        "section": section,
        "grade": active_drilldown["grade"],
        "subject_id": active_drilldown["subject_id"],
        "chapter_id": active_drilldown["chapter_id"],
        "lesson_id": active_drilldown["lesson_id"],
        "target": active_drilldown["target"],
        "exam_subject": active_drilldown["exam_subject"],
        "difficulty": difficulty,
        "qtype": qtype,
        "verified": verified,
        "q": q if isinstance(q, str) else "",
    }

    # Query string generator for pagination
    def build_page_url(p: int) -> str:
        clean_params = {k: v for k, v in filters.items() if v}
        clean_params["page"] = p
        return "/admin/questions?" + urlencode(clean_params)

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
            "page_url": build_page_url,
            "active_drilldown": active_drilldown,
            "breadcrumb_label": breadcrumb_label,
            "effective_scope": effective_scope,
            "catalog": catalog,
            "catalog_json": json.dumps(catalog),
            "scope_paths": all_paths,
            "scope_of": scope_of,
            "ku_names": ku_map,
            "q_targets": q_targets_map,
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
    await session.flush()
    from app.models import QuestionCategory

    if scope.category_id:
        session.add(QuestionCategory(question_id=question.id, category_id=scope.category_id))
    await session.commit()
    flash(request, f"Đã thêm câu hỏi #{question.id}.")
    if form.get("action") == "new":
        return redirect("/admin/questions/new?" + urlencode({"scope": scope.key}))
    return redirect("/admin/questions?" + urlencode({"scope": scope.key}))


@router.get("/questions/{question_id:int}/edit")
async def edit_question(request: Request, user: AdminUser, session: SessionDep, question_id: int):
    question = await get_or_404(session, Question, question_id)
    return await _render_form(request, session, user, QuestionForm.from_question(question), question)


@router.post("/questions/{question_id:int}")
async def update_question(request: Request, user: AdminUser, session: SessionDep, question_id: int):
    question = await get_or_404(session, Question, question_id)
    form = await request.form()
    data, scope, errors = await _parse(session, form)
    if scope and scope.root_id != question.root_id and await _exams_using(session, question_id):
        errors.append("Câu hỏi đang nằm trong đề thi nên không thể chuyển sang môn khác.")
    if errors:
        return await _render_form(request, session, user, data, question, errors)
    _apply(question, data, scope)
    if form.get("remove_image") == "on":
        question.image_path = None
    await session.commit()
    flash(request, f"Đã lưu câu hỏi #{question_id}.")
    return redirect("/admin/questions?" + urlencode({"scope": scope.key}))


@router.post("/questions/{question_id:int}/delete")
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


@router.post("/questions/{question_id:int}/verify")
async def verify_question(request: Request, session: SessionDep, question_id: int):
    question = await get_or_404(session, Question, question_id)
    question.is_verified = True
    await session.commit()
    flash(request, f"Đã duyệt câu #{question_id}; câu này giờ dùng được trong đề thi.")
    return redirect("/admin/questions?verified=no")


@router.post("/questions/ai-solve")
async def ai_solve(payload: Annotated[dict[str, Any], Body()]):
    """Ask Gemini to solve one question from the form (any type, including essays)."""
    from app.ai.provider import AIError, GeminiProvider
    from app.ai.schemas import ExtractedChoice, ExtractedQuestion
    from app.config import get_settings

    qtype = payload.get("type") if payload.get("type") in QuestionType.__members__ else "MULTIPLE_CHOICE"
    content = str(payload.get("content") or "").strip()
    raw_choices = [str(c).strip() for c in payload.get("choices") or [] if str(c).strip()]
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hãy nhập nội dung câu hỏi trước.")
    if qtype in (QuestionType.SHORT_ANSWER, QuestionType.ESSAY):
        raw_choices = []
    question = ExtractedQuestion(
        number=1,
        page=1,
        type=qtype,
        content=content,
        choices=[ExtractedChoice(label="ABCDEF"[i], content=c) for i, c in enumerate(raw_choices[:6])],
    )
    settings = get_settings()
    try:
        provider = GeminiProvider(settings.gemini_api_key, settings.gemini_model_list)
        solutions = await provider.solve([question], [])
    except AIError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if not solutions:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "AI không trả về lời giải.")
    sol = solutions[0]
    correct = "ABCDEF".index(sol.correct_label) if sol.correct_label and qtype == "MULTIPLE_CHOICE" else None
    return {
        "explanation": sol.step_by_step_solution.strip(),
        "final_value": sol.final_value,
        "correct": correct if correct is not None and correct < len(raw_choices) else None,
        "truths": sol.statement_truths if qtype == "TRUE_FALSE" else None,
        "short_answer": rules.extract_number(sol.short_answer or sol.final_value or "")
        if qtype == "SHORT_ANSWER"
        else None,
        "difficulty": sol.difficulty,
    }
