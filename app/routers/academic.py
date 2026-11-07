"""Academic Portal Router:
Integrates OpenAlex, Semantic Scholar, Open Library, and Open Trivia Database APIs.
"""


from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.deps import CurrentUser, SessionDep
from app.models import Category, Exam, ExamMode, ExamVisibility, Question, QuestionType
from app.services.academic_api import (
    OPEN_TRIVIA_CATEGORIES,
    get_open_trivia_quizzes,
    search_books,
    search_research_papers,
)
from app.templating import templates

router = APIRouter(tags=["academic"])


@router.get("/academic")
async def academic_portal_page(
    request: Request,
    user: CurrentUser,
    tab: str = "papers",
    q: str = "",
):
    """Main Academic & Open Knowledge Portal."""
    initial_papers = []
    initial_books = []
    initial_trivia = []

    if tab == "books":
        initial_books = search_books(q or "science", limit=8)
    elif tab == "trivia":
        initial_trivia = get_open_trivia_quizzes(amount=6)
    else:
        tab = "papers"
        initial_papers = search_research_papers(q or "artificial intelligence education", limit=8)

    return templates.TemplateResponse(
        request,
        "academic_portal.html",
        {
            "user": user,
            "active_tab": tab,
            "query": q,
            "papers": initial_papers,
            "books": initial_books,
            "trivia_categories": OPEN_TRIVIA_CATEGORIES,
            "trivia_quizzes": initial_trivia,
        },
    )


@router.get("/api/academic/papers")
async def api_search_papers(
    q: str = Query("education", description="Search query"),
    limit: int = Query(10, ge=1, le=25),
    page: int = Query(1, ge=1),
):
    papers = search_research_papers(q, limit=limit, page=page)
    return JSONResponse({"papers": papers, "count": len(papers)})


@router.get("/api/academic/books")
async def api_search_books(
    q: str = Query("science", description="Search query"),
    limit: int = Query(12, ge=1, le=30),
    page: int = Query(1, ge=1),
):
    books = search_books(q, limit=limit, page=page)
    return JSONResponse({"books": books, "count": len(books)})


@router.get("/api/academic/trivia")
async def api_get_trivia(
    amount: int = Query(10, ge=1, le=50),
    category_id: int = Query(0),
    difficulty: str = Query(""),
):
    quizzes = get_open_trivia_quizzes(amount=amount, category_id=category_id, difficulty=difficulty)
    return JSONResponse({"quizzes": quizzes, "count": len(quizzes)})


@router.post("/api/academic/create-exam-from-trivia")
async def api_create_exam_from_trivia(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
):
    """Converts current trivia quiz into a real live Exam in the database."""
    data = await request.json()
    title = data.get("title") or "Bài thi Trắc nghiệm Tri thức Quốc tế (Open Trivia)"
    questions_data = data.get("questions") or []
    duration = int(data.get("duration", 15))

    if not questions_data:
        return JSONResponse({"error": "Không có câu hỏi hợp lệ."}, status_code=400)

    # Find root category or use default
    root = (await session.scalars(select(Category).where(Category.parent_id.is_(None)).limit(1))).first()
    category_id = root.id if root else None

    exam = Exam(
        title=title,
        description=f"Bộ đề trắc nghiệm tri thức đa lĩnh vực được tích hợp từ Open Trivia Database với {len(questions_data)} câu hỏi.",
        exam_mode=ExamMode.PRACTICE,
        visibility=ExamVisibility.PUBLIC,
        is_published=True,
        time_limit_minutes=duration,
        category_id=category_id,
        root_id=root.id if root else None,
        created_by=user.id if user else None,
    )
    session.add(exam)
    await session.flush()

    for idx, qd in enumerate(questions_data, start=1):
        q = Question(
            category_id=category_id,
            question_type=QuestionType.MULTIPLE_CHOICE,
            content=qd.get("question", ""),
            choices=[{"key": c["letter"], "content": c["text"]} for c in qd.get("choices", [])],
            answer=qd.get("correct_letter", "A"),
            explanation=f"Đáp án chính xác: {qd.get('correct_answer', '')}",
            difficulty="UNDERSTANDING",
            exam_id=exam.id,
            order=idx,
        )
        session.add(q)

    await session.commit()
    return JSONResponse({"success": True, "exam_id": exam.id, "link": f"/exams/{exam.id}"})
