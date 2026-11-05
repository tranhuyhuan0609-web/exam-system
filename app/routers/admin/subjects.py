from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import delete, func, select

from app.deps import AdminUser, SessionDep
from app.models import Chapter, Exam, Lesson, Question, Subject
from app.routers.admin.common import clean_text, get_or_404, move, redirect
from app.scope import load_subject_tree
from app.templating import flash, templates
from app.text import slugify

router = APIRouter()

TextForm = Annotated[str, Form()]


async def _unique_slug(session, base: str, exclude_id: int | None = None) -> str:
    slug, n = base, 2
    while True:
        stmt = select(Subject.id).where(Subject.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(Subject.id != exclude_id)
        if not await session.scalar(stmt):
            return slug
        slug, n = f"{base}-{n}", n + 1


async def _count(session, model, *conditions) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


# ---------------------------------------------------------------- subjects


@router.get("/subjects")
async def subjects_page(request: Request, user: AdminUser, session: SessionDep):
    subjects = await load_subject_tree(session)
    question_counts = dict(
        (await session.execute(select(Question.subject_id, func.count()).group_by(Question.subject_id))).all()
    )
    exam_counts = dict((await session.execute(select(Exam.subject_id, func.count()).group_by(Exam.subject_id))).all())
    return templates.TemplateResponse(
        request,
        "admin/subjects.html",
        {"user": user, "subjects": subjects, "question_counts": question_counts, "exam_counts": exam_counts},
    )


@router.post("/subjects")
async def create_subject(
    request: Request, session: SessionDep, name: TextForm, slug: TextForm = "", description: TextForm = ""
):
    clean = clean_text(name, 200)
    if not clean:
        flash(request, "Tên môn học không hợp lệ (1–200 ký tự).", "error")
        return redirect("/admin/subjects")
    subject = Subject(
        name=clean,
        slug=await _unique_slug(session, slugify(slug or clean)),
        description=description.strip() or None,
    )
    session.add(subject)
    await session.commit()
    flash(request, f"Đã tạo môn “{clean}”.")
    return redirect(f"/admin/subjects/{subject.id}")


@router.get("/subjects/{subject_id}")
async def subject_detail(request: Request, user: AdminUser, session: SessionDep, subject_id: int):
    tree = await load_subject_tree(session, subject_id)
    if not tree:
        raise HTTPException(404, "Không tìm thấy môn học.")
    subject = tree[0]
    by_chapter = dict(
        (
            await session.execute(
                select(Question.chapter_id, func.count())
                .where(Question.subject_id == subject_id)
                .group_by(Question.chapter_id)
            )
        ).all()
    )
    by_lesson = dict(
        (
            await session.execute(
                select(Question.lesson_id, func.count())
                .where(Question.subject_id == subject_id)
                .group_by(Question.lesson_id)
            )
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "admin/subject_detail.html",
        {"user": user, "subject": subject, "by_chapter": by_chapter, "by_lesson": by_lesson},
    )


@router.post("/subjects/{subject_id}")
async def update_subject(
    request: Request,
    session: SessionDep,
    subject_id: int,
    name: TextForm,
    slug: TextForm = "",
    description: TextForm = "",
):
    subject = await get_or_404(session, Subject, subject_id)
    clean = clean_text(name, 200)
    if not clean:
        flash(request, "Tên môn học không hợp lệ (1–200 ký tự).", "error")
        return redirect(f"/admin/subjects/{subject_id}")
    subject.name = clean
    subject.slug = await _unique_slug(session, slugify(slug or clean), exclude_id=subject_id)
    subject.description = description.strip() or None
    await session.commit()
    flash(request, "Đã lưu môn học.")
    return redirect(f"/admin/subjects/{subject_id}")


@router.post("/subjects/{subject_id}/delete")
async def delete_subject(request: Request, session: SessionDep, subject_id: int):
    subject = await get_or_404(session, Subject, subject_id)
    questions = await _count(session, Question, Question.subject_id == subject_id)
    exams = await _count(session, Exam, Exam.subject_id == subject_id)
    if questions or exams:
        flash(request, f"Không thể xoá: môn còn {questions} câu hỏi và {exams} đề thi.", "error")
        return redirect(f"/admin/subjects/{subject_id}")
    await session.execute(delete(Subject).where(Subject.id == subject_id))  # chapters/lessons cascade in DB
    await session.commit()
    flash(request, f"Đã xoá môn “{subject.name}”.")
    return redirect("/admin/subjects")


# ---------------------------------------------------------------- chapters


@router.post("/subjects/{subject_id}/chapters")
async def create_chapter(request: Request, session: SessionDep, subject_id: int, name: TextForm):
    await get_or_404(session, Subject, subject_id)
    clean = clean_text(name, 300)
    if not clean:
        flash(request, "Tên chương không hợp lệ (1–300 ký tự).", "error")
    else:
        last = await session.scalar(select(func.max(Chapter.position)).where(Chapter.subject_id == subject_id))
        session.add(Chapter(subject_id=subject_id, name=clean, position=(last or 0) + 1))
        await session.commit()
        flash(request, f"Đã thêm “{clean}”.")
    return redirect(f"/admin/subjects/{subject_id}")


@router.post("/chapters/{chapter_id}")
async def rename_chapter(request: Request, session: SessionDep, chapter_id: int, name: TextForm):
    chapter = await get_or_404(session, Chapter, chapter_id)
    if clean := clean_text(name, 300):
        chapter.name = clean
        await session.commit()
        flash(request, "Đã đổi tên chương.")
    else:
        flash(request, "Tên chương không hợp lệ (1–300 ký tự).", "error")
    return redirect(f"/admin/subjects/{chapter.subject_id}")


@router.post("/chapters/{chapter_id}/move")
async def move_chapter(session: SessionDep, chapter_id: int, direction: TextForm):
    chapter = await get_or_404(session, Chapter, chapter_id)
    siblings = list(
        (
            await session.scalars(
                select(Chapter).where(Chapter.subject_id == chapter.subject_id).order_by(Chapter.position, Chapter.id)
            )
        ).all()
    )
    move(siblings, chapter, direction)
    await session.commit()
    return redirect(f"/admin/subjects/{chapter.subject_id}")


@router.post("/chapters/{chapter_id}/delete")
async def delete_chapter(request: Request, session: SessionDep, chapter_id: int):
    chapter = await get_or_404(session, Chapter, chapter_id)
    questions = await _count(session, Question, Question.chapter_id == chapter_id)
    exams = await _count(session, Exam, Exam.chapter_id == chapter_id)
    if questions or exams:
        flash(request, f"Không thể xoá chương: còn {questions} câu hỏi và {exams} đề thi gắn với chương.", "error")
    else:
        await session.execute(delete(Chapter).where(Chapter.id == chapter_id))  # lessons cascade in DB
        await session.commit()
        flash(request, f"Đã xoá “{chapter.name}”.")
    return redirect(f"/admin/subjects/{chapter.subject_id}")


# ---------------------------------------------------------------- lessons


@router.post("/chapters/{chapter_id}/lessons")
async def create_lesson(request: Request, session: SessionDep, chapter_id: int, name: TextForm):
    chapter = await get_or_404(session, Chapter, chapter_id)
    clean = clean_text(name, 300)
    if not clean:
        flash(request, "Tên bài học không hợp lệ (1–300 ký tự).", "error")
    else:
        last = await session.scalar(select(func.max(Lesson.position)).where(Lesson.chapter_id == chapter_id))
        session.add(Lesson(chapter_id=chapter_id, name=clean, position=(last or 0) + 1))
        await session.commit()
        flash(request, f"Đã thêm “{clean}”.")
    return redirect(f"/admin/subjects/{chapter.subject_id}")


async def _lesson_with_chapter(session, lesson_id: int) -> tuple[Lesson, Chapter]:
    lesson = await get_or_404(session, Lesson, lesson_id)
    return lesson, await session.get(Chapter, lesson.chapter_id)


@router.post("/lessons/{lesson_id}")
async def rename_lesson(request: Request, session: SessionDep, lesson_id: int, name: TextForm):
    lesson, chapter = await _lesson_with_chapter(session, lesson_id)
    if clean := clean_text(name, 300):
        lesson.name = clean
        await session.commit()
        flash(request, "Đã đổi tên bài học.")
    else:
        flash(request, "Tên bài học không hợp lệ (1–300 ký tự).", "error")
    return redirect(f"/admin/subjects/{chapter.subject_id}")


@router.post("/lessons/{lesson_id}/move")
async def move_lesson(session: SessionDep, lesson_id: int, direction: TextForm):
    lesson, chapter = await _lesson_with_chapter(session, lesson_id)
    siblings = list(
        (
            await session.scalars(
                select(Lesson).where(Lesson.chapter_id == chapter.id).order_by(Lesson.position, Lesson.id)
            )
        ).all()
    )
    move(siblings, lesson, direction)
    await session.commit()
    return redirect(f"/admin/subjects/{chapter.subject_id}")


@router.post("/lessons/{lesson_id}/delete")
async def delete_lesson(request: Request, session: SessionDep, lesson_id: int):
    lesson, chapter = await _lesson_with_chapter(session, lesson_id)
    questions = await _count(session, Question, Question.lesson_id == lesson_id)
    exams = await _count(session, Exam, Exam.lesson_id == lesson_id)
    if questions or exams:
        flash(request, f"Không thể xoá bài: còn {questions} câu hỏi và {exams} đề thi gắn với bài.", "error")
    else:
        await session.execute(delete(Lesson).where(Lesson.id == lesson_id))
        await session.commit()
        flash(request, f"Đã xoá “{lesson.name}”.")
    return redirect(f"/admin/subjects/{chapter.subject_id}")
