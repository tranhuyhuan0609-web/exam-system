"""Admin router for Data Self-Healing & Verification Audit Queue (/admin/audit-queue)."""


from fastapi import APIRouter, Form, HTTPException, Request, status
from sqlalchemy import func, select
from starlette.responses import RedirectResponse

from app.ai.background_audit_jobs import QuestionBankAuditWorker
from app.deps import AdminUser, SessionDep
from app.models import KnowledgeUnit, Question
from app.templating import flash, templates

router = APIRouter()

PAGE_SIZE = 20


@router.get("/audit-queue")
async def audit_queue_page(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    tab: str = "review",
    page: int = 1,
):
    # 1. KPI counters across states
    cnt_pending = await session.scalar(
        select(func.count(Question.id)).where(Question.audit_status == "PENDING")
    ) or 0
    cnt_healed = await session.scalar(
        select(func.count(Question.id)).where(Question.audit_status == "FONT_OR_TAXONOMY_FIXED")
    ) or 0
    cnt_consensus = await session.scalar(
        select(func.count(Question.id)).where(Question.audit_status == "VERIFIED_BY_CONSENSUS")
    ) or 0
    cnt_review = await session.scalar(
        select(func.count(Question.id)).where(Question.audit_status == "NEEDS_HUMAN_REVIEW")
    ) or 0
    cnt_suspect_source = await session.scalar(
        select(func.count(Question.id)).where(Question.quality_flags.contains(["SUSPECT_SOURCE_ERROR"]))
    ) or 0
    cnt_missing_table = await session.scalar(
        select(func.count(Question.id)).where(Question.quality_flags.contains(["MISSING_TABLE"]))
    ) or 0

    # 2. Condition by active tab
    conditions = []
    if tab == "review":
        conditions.append(Question.audit_status == "NEEDS_HUMAN_REVIEW")
    elif tab == "table":
        conditions.append(Question.quality_flags.contains(["MISSING_TABLE"]))
    elif tab == "suspect":
        conditions.append(Question.quality_flags.contains(["SUSPECT_SOURCE_ERROR"]))
    elif tab == "consensus":
        conditions.append(Question.audit_status == "VERIFIED_BY_CONSENSUS")
    elif tab == "healed":
        conditions.append(Question.audit_status == "FONT_OR_TAXONOMY_FIXED")
    elif tab == "pending":
        conditions.append(Question.audit_status == "PENDING")
    else:
        tab = "review"
        conditions.append(Question.audit_status == "NEEDS_HUMAN_REVIEW")

    total_in_tab = await session.scalar(
        select(func.count(Question.id)).where(*conditions)
    ) or 0
    pages = max(1, -(-total_in_tab // PAGE_SIZE))
    page = min(max(page, 1), pages)

    questions = list((await session.scalars(
        select(Question)
        .where(*conditions)
        .order_by(Question.id.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )).all())

    # Map knowledge units
    ku_ids = [q.knowledge_unit_id for q in questions if q.knowledge_unit_id]
    ku_map: dict[int, str] = {}
    if ku_ids:
        ku_rows = (await session.execute(
            select(KnowledgeUnit.id, KnowledgeUnit.name).where(KnowledgeUnit.id.in_(ku_ids))
        )).all()
        ku_map = {r[0]: r[1] for r in ku_rows}

    # Extract dynamic topics suggested across recent questions
    suggested_topics: dict[str, int] = {}
    topic_stmt = select(Question.quality_flags).where(Question.quality_flags.is_not(None)).limit(500)
    for (flags,) in (await session.execute(topic_stmt)).all():
        if isinstance(flags, list):
            for f in flags:
                if str(f).startswith("SUGGESTED_TOPIC:"):
                    tname = str(f).split(":", 1)[1].strip()
                    suggested_topics[tname] = suggested_topics.get(tname, 0) + 1

    return templates.TemplateResponse(
        request,
        "admin/audit_queue.html",
        {
            "user": user,
            "questions": questions,
            "tab": tab,
            "page": page,
            "pages": pages,
            "total": total_in_tab,
            "cnt_pending": cnt_pending,
            "cnt_healed": cnt_healed,
            "cnt_consensus": cnt_consensus,
            "cnt_review": cnt_review,
            "cnt_suspect_source": cnt_suspect_source,
            "cnt_missing_table": cnt_missing_table,
            "ku_map": ku_map,
            "suggested_topics": suggested_topics,
        },
    )


@router.post("/audit-queue/run-job1")
async def trigger_job1(request: Request, user: AdminUser, session: SessionDep, batch_size: int = 15):
    worker = QuestionBankAuditWorker(session)
    res = await worker.run_job1_heal_font_and_taxonomy(batch_size=batch_size)
    flash(request, res.get("message", "Job 1 hoàn thành."), kind="success" if res.get("status") == "ok" else "error")
    return RedirectResponse(url="/admin/audit-queue?tab=healed", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/audit-queue/run-job2")
async def trigger_job2(request: Request, user: AdminUser, session: SessionDep, batch_size: int = 10):
    worker = QuestionBankAuditWorker(session)
    res = await worker.run_job2_verify_and_attach_to_exam(batch_size=batch_size)
    flash(request, res.get("message", "Job 2 hoàn thành."), kind="success" if res.get("status") == "ok" else "error")
    return RedirectResponse(url="/admin/audit-queue?tab=consensus", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/audit-queue/{question_id}/resolve")
async def resolve_question(
    request: Request,
    user: AdminUser,
    session: SessionDep,
    question_id: int,
    action: str = Form(...),
    custom_answer: str = Form(""),
):
    q = await session.get(Question, question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu hỏi")

    if action == "first" and q.first_ai_answer:
        chosen = q.first_ai_answer.upper()
    elif action == "second" and q.second_ai_answer:
        chosen = q.second_ai_answer.upper()
    elif action == "custom":
        chosen = custom_answer.strip().upper()
    else:
        chosen = ""

    if chosen:
        if q.choices:
            new_choices = []
            for c in q.choices:
                cc = dict(c)
                cc["is_correct"] = (cc.get("id", "").upper() == chosen)
                new_choices.append(cc)
            q.choices = new_choices

        q.is_verified = True
        q.audit_status = "VERIFIED_BY_HUMAN"
        # Remove disagreement flag
        q.quality_flags = [f for f in (q.quality_flags or []) if f != "AI_DISAGREEMENT"]
        await session.commit()
        flash(request, f"Đã duyệt câu hỏi #{q.id} với đáp án [{chosen}].", kind="success")
    else:
        q.is_verified = True
        q.audit_status = "VERIFIED_BY_HUMAN"
        await session.commit()
        flash(request, f"Đã xác nhận câu hỏi #{q.id}.", kind="success")

    return RedirectResponse(url=request.headers.get("referer", "/admin/audit-queue"), status_code=status.HTTP_303_SEE_OTHER)  # noqa: E501

