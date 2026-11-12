"""Tests for the 3-layer Freemium & PRO Library Management Architecture:
1. Category tree default_tier inheritance & FREE matrix question bank filtering.
2. Funnel A: Free exams blur step-by-step AI solutions on Vận dụng cao (ADVANCED_APPLICATION) questions for Free users.
3. Funnel B: Exclusive PRO exams expose only the first `preview_question_count` (5) questions to Free users.
4. Funnel C: PRO / HSG PDFs in the library auto-slice a 2-page watermarked preview via PyMuPDF for Free users,
   while Pro users get full access and 60s presigned URLs.
5. Admin upload toggle (`is_pro=on`) stores raw files in the private vault and tags jobs/questions/exams as PRO/ORIGINAL.
"""  # noqa: E501

import asyncio
from datetime import timedelta

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import func, update

from app.application.ai_publish import create_exam, publish_drafts
from app.application.categories import create_category
from app.infrastructure.storage import get_bronze_storage
from app.main import app
from app.models import (
    AccessTier,
    Difficulty,
    Exam,
    ExamOriginType,
    ExamQuestion,
    ImportJob,
    LibraryDocument,
    LibraryKind,
    LibraryStatus,
    Question,
    QuestionOriginType,
    QuestionSource,
    User,
    UserRole,
)


def _login(make_user, email: str, role: UserRole = UserRole.STUDENT) -> TestClient:
    make_user(email, role=role)
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": "password123"})
    return client


def _grant_pro(db, email: str) -> None:
    db(lambda s: s.execute(update(User).where(User.email == email).values(pro_until=func.now() + timedelta(days=30))))


def _make_pdf(pages: int) -> bytes:
    with pymupdf.open() as doc:
        for i in range(1, pages + 1):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Trang noi dung so {i} / {pages} - Chuyen de HSG Vat Li")
        return doc.tobytes()


def test_category_tier_inheritance_and_free_matrix_excludes_pro_questions(db, make_user):
    async def _setup(s):
        root = await create_category(s, "Vật lí", slug="vat-li")
        hsg = await create_category(s, "Bồi dưỡng Học Sinh Giỏi Tỉnh")  # auto-detected as PRO
        child = await create_category(s, "Chuyên đề Cơ vật rắn", parent=hsg)
        assert hsg.default_tier == AccessTier.PRO
        assert child.default_tier == AccessTier.PRO

        # Add 2 FREE questions and 2 PRO authored questions in `root`
        for i in range(1, 3):
            s.add(
                Question(
                    root_id=root.id,
                    category_id=root.id,
                    difficulty=Difficulty.RECOGNITION,
                    content=f"Câu công khai {i}",
                    choices=[
                        {"id": "a", "content": "Đúng", "is_correct": True},
                        {"id": "b", "content": "Sai", "is_correct": False},
                    ],
                    access_tier=AccessTier.FREE,
                    origin_type=QuestionOriginType.PUBLIC,
                )
            )
            s.add(
                Question(
                    root_id=root.id,
                    category_id=root.id,
                    difficulty=Difficulty.RECOGNITION,
                    content=f"Câu độc quyền PRO {i}",
                    choices=[
                        {"id": "a", "content": "Đúng", "is_correct": True},
                        {"id": "b", "content": "Sai", "is_correct": False},
                    ],
                    access_tier=AccessTier.PRO,
                    origin_type=QuestionOriginType.AUTHORED,
                )
            )
        exam = Exam(
            root_id=root.id,
            category_id=root.id,
            title="Đề ma trận FREE",
            duration_seconds=600,
            is_published=True,
            access_tier=AccessTier.FREE,
            question_source=QuestionSource.MATRIX,
            matrix=[{"scope": f"c:{root.id}", "difficulty": "RECOGNITION", "type": "MULTIPLE_CHOICE", "count": 2}],
        )
        s.add(exam)
        await s.flush()
        return exam.id

    exam_id = db(_setup)
    student = _login(make_user, "free@example.com")
    r = student.post(f"/exams/{exam_id}/start", follow_redirects=False)
    attempt_id = int(r.headers["location"].rsplit("/", 1)[1])
    paper = student.get(f"/api/attempts/{attempt_id}/paper").json()
    contents = [q["content"] for q in paper["questions"]]
    assert len(contents) == 2
    assert all("Câu công khai" in c for c in contents)
    assert all("PRO" not in c for c in contents)


def test_funnel_a_free_exam_blurs_vdc_solutions_for_free_user_and_unlocks_for_pro(db, make_user):
    async def _setup(s):
        root = await create_category(s, "Vật lí 12", slug="vat-li-12")
        q_easy = Question(
            root_id=root.id,
            category_id=root.id,
            difficulty=Difficulty.RECOGNITION,
            content="Câu cơ bản Nhận biết",
            choices=[
                {"id": "a", "content": "A", "is_correct": True},
                {"id": "b", "content": "B", "is_correct": False},
            ],
            explanation="LOI_GIAI_CO_BAN_HIEN_THI_MIEN_PHI",
        )
        q_vdc = Question(
            root_id=root.id,
            category_id=root.id,
            difficulty=Difficulty.ADVANCED_APPLICATION,
            content="Câu Vận dụng cao cuối đề",
            choices=[
                {"id": "a", "content": "A", "is_correct": True},
                {"id": "b", "content": "B", "is_correct": False},
            ],
            explanation="PHUONG_PHAP_GIAI_NHANH_VDC_BI_MAT_PRO",
        )
        s.add_all([q_easy, q_vdc])
        exam = Exam(
            root_id=root.id,
            category_id=root.id,
            title="Đề THPT Công khai",
            duration_seconds=900,
            is_published=True,
            access_tier=AccessTier.FREE,
        )
        s.add(exam)
        await s.flush()
        s.add_all(
            [
                ExamQuestion(exam_id=exam.id, question_id=q_easy.id, position=1),
                ExamQuestion(exam_id=exam.id, question_id=q_vdc.id, position=2),
            ]
        )
        return exam.id, q_easy.id, q_vdc.id

    exam_id, q1, q2 = db(_setup)
    student = _login(make_user, "student_free@example.com")
    r = student.post(f"/exams/{exam_id}/start", follow_redirects=False)
    attempt_id = int(r.headers["location"].rsplit("/", 1)[1])
    student.post(f"/api/attempts/{attempt_id}/submit", json={"answers": {str(q1): "a", str(q2): "b"}})

    # Free user sees recognition explanation, but VDC explanation is stripped & replaced with upgrade CTA
    sol_free = student.get(f"/attempts/{attempt_id}/solutions").text
    assert "LOI_GIAI_CO_BAN_HIEN_THI_MIEN_PHI" in sol_free
    assert "PHUONG_PHAP_GIAI_NHANH_VDC_BI_MAT_PRO" not in sol_free
    assert "Mở khóa tài khoản PRO để xem phương pháp giải nhanh câu này" in sol_free

    # Upgrade to PRO -> full VDC explanation is unlocked!
    _grant_pro(db, "student_free@example.com")
    sol_pro = student.get(f"/attempts/{attempt_id}/solutions").text
    assert "PHUONG_PHAP_GIAI_NHANH_VDC_BI_MAT_PRO" in sol_pro


def test_funnel_b_pro_exam_exposes_only_5_preview_questions_to_free_users(db, make_user):
    async def _setup(s):
        root = await create_category(s, "Vật lí Chuyên", slug="vat-li-chuyen", default_tier=AccessTier.PRO)
        questions = [
            Question(
                root_id=root.id,
                category_id=root.id,
                difficulty=Difficulty.APPLICATION,
                content=f"NOI_DUNG_CAU_PRO_{i}",
                choices=[
                    {"id": "a", "content": "Đúng", "is_correct": True},
                    {"id": "b", "content": "Sai", "is_correct": False},
                ],
                access_tier=AccessTier.PRO,
                origin_type=QuestionOriginType.AUTHORED,
            )
            for i in range(1, 9)
        ]
        s.add_all(questions)
        exam = Exam(
            root_id=root.id,
            category_id=root.id,
            title="Bộ đề thực chiến độc quyền số 1",
            duration_seconds=3000,
            is_published=True,
            access_tier=AccessTier.PRO,
            origin_type=ExamOriginType.ORIGINAL,
            preview_question_count=5,
        )
        s.add(exam)
        await s.flush()
        s.add_all(ExamQuestion(exam_id=exam.id, question_id=q.id, position=i) for i, q in enumerate(questions, 1))
        return exam.id

    exam_id = db(_setup)
    student = _login(make_user, "free_viewer@example.com")

    # 1. HTML exam page shows first 5 questions and hides #6..#8
    html = student.get(f"/exams/{exam_id}").text
    for i in range(1, 6):
        assert f"NOI_DUNG_CAU_PRO_{i}" in html
    for i in range(6, 9):
        assert f"NOI_DUNG_CAU_PRO_{i}" not in html
    assert "Còn 3 câu hỏi nâng cao" in html

    # 2. JSON preview API returns strictly 5 questions and locked_questions_count == 3
    preview = student.get(f"/api/exams/{exam_id}/preview").json()
    assert preview["preview_only"] is True
    assert preview["preview_question_count"] == 5
    assert preview["total_questions"] == 8
    assert preview["locked_questions_count"] == 3
    assert len(preview["questions"]) == 5
    assert "NOI_DUNG_CAU_PRO_6" not in str(preview)

    # 3. Starting full attempt is blocked for Free user, allowed once upgraded to PRO
    start_res = student.post(f"/exams/{exam_id}/start", follow_redirects=False)
    assert start_res.headers["location"] == f"/exams/{exam_id}"

    _grant_pro(db, "free_viewer@example.com")
    pro_preview = student.get(f"/api/exams/{exam_id}/preview").json()
    assert pro_preview["preview_only"] is False and len(pro_preview["questions"]) == 8
    start_pro = student.post(f"/exams/{exam_id}/start", follow_redirects=False)
    assert start_pro.headers["location"].startswith("/attempts/")


def test_funnel_c_pro_library_document_slices_2_page_preview_for_free_and_full_for_pro(db, make_user):
    pdf_6_pages = _make_pdf(6)
    key = "hsg-materials/chuyen-de-dao-dong-6-trang.pdf"
    asyncio.run(get_bronze_storage().put(key, pdf_6_pages, "application/pdf"))

    async def _setup(s):
        doc = LibraryDocument(
            title="Chuyên đề Dao động cơ HSG Tỉnh (30 trang)",
            kind=LibraryKind.HSG,
            status=LibraryStatus.PUBLISHED,
            access_tier=AccessTier.PRO,
            folder_path=["2. Bồi dưỡng Học Sinh Giỏi"],
            source="upload",
            source_file_id="hsg-doc-1",
            checksum="abc123def456",
            storage_key=key,
            mime="application/pdf",
            size=len(pdf_6_pages),
        )
        s.add(doc)
        await s.flush()
        return doc.id

    doc_id = db(_setup)
    student = _login(make_user, "free_hsg@example.com")

    # Free user opening /library/{id}/view gets only the 2-page sliced preview PDF
    res = student.get(f"/library/{doc_id}/view")
    assert res.status_code == 200
    assert res.headers.get("x-preview-only") == "true"
    assert res.headers.get("x-preview-pages") == "2"
    assert res.headers.get("x-total-pages") == "6"
    with pymupdf.open(stream=res.content, filetype="pdf") as sliced_pdf:
        assert len(sliced_pdf) == 2

    # Presigned URL endpoint rejects Free user with 403 + preview_url
    presign_free = student.get(f"/api/library/{doc_id}/presigned-url")
    assert presign_free.status_code == 403
    assert presign_free.json()["preview_url"] == f"/library/{doc_id}/preview.pdf"

    # HTML preview page renders conversion banner
    preview_html = student.get(f"/library/{doc_id}/preview").text
    assert "Bạn đang xem thử 2 trang đầu tiên miễn phí" in preview_html
    assert "Còn 4/6 trang chuyên sâu bị khóa" in preview_html

    # Upgrade to PRO -> receives all 6 pages and 200 on presigned-url
    _grant_pro(db, "free_hsg@example.com")
    full_res = student.get(f"/library/{doc_id}/view")
    assert full_res.status_code == 200
    assert full_res.headers.get("x-preview-only") is None
    with pymupdf.open(stream=full_res.content, filetype="pdf") as full_pdf:
        assert len(full_pdf) == 6
    presign_pro = student.get(f"/api/library/{doc_id}/presigned-url")
    assert presign_pro.status_code == 200
    assert presign_pro.json()["expires_in"] == 60


def test_admin_upload_pro_toggle_tags_job_questions_and_exam_as_pro_original(admin_client, db):
    cat_id = db(lambda s: create_category(s, "Vật lí 12", slug="vat-li-12")).id
    pdf_bytes = _make_pdf(1)
    r = admin_client.post(
        "/admin/ai-imports",
        data={"scope": f"c:{cat_id}", "default_difficulty": "APPLICATION", "is_pro": "on"},
        files={"file": ("de-doc-quyen.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    job_id = int(r.headers["location"].rsplit("/", 1)[1])

    async def _publish(s):
        job = await s.get(ImportJob, job_id)
        assert job.access_tier == AccessTier.PRO
        assert job.origin_type == ExamOriginType.ORIGINAL
        job.drafts = [
            {
                "number": 1,
                "page": 1,
                "type": "MULTIPLE_CHOICE",
                "difficulty": "ADVANCED_APPLICATION",
                "content": "Câu hỏi tự biên soạn độc quyền",
                "choices": ["10 W", "20 W", "30 W", "40 W"],
                "correct": 0,
                "explanation": "Lời giải độc quyền",
                "flags": [],
            }
        ]
        questions = await publish_drafts(s, job, [0], verified=True)
        exam = await create_exam(
            s, job, questions, title="Đề Độc Quyền 01", minutes=50, published=True, user_id=None
        )
        return questions[0].id, exam.id

    qid, eid = db(_publish)
    q = db(lambda s: s.get(Question, qid))
    e = db(lambda s: s.get(Exam, eid))
    assert (q.access_tier, q.origin_type) == (AccessTier.PRO, QuestionOriginType.AUTHORED)
    assert (e.access_tier, e.origin_type, e.preview_question_count) == (
        AccessTier.PRO,
        ExamOriginType.ORIGINAL,
        5,
    )

