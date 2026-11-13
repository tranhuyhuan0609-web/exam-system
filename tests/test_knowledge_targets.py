"""Tests for the Multi-dimensional Knowledge Unit (SGK Kết nối tri thức)
& Exam Target (THPTQG, ĐGTD TSA, ĐGNL HSA, Vào 10, HSG Tỉnh/QG) Single-Source-of-Truth Architecture."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.application.ai_publish import publish_drafts
from app.application.categories import create_category
from app.application.knowledge_linker import (
    hsg_questions_for_unit,
    questions_for_target,
    seed_knowledge_architecture,
)
from app.main import app
from app.models import (
    AccessTier,
    Difficulty,
    ImportJob,
    KnowledgeUnit,
    Question,
    QuestionType,
    SourceType,
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


def test_single_source_of_truth_inheritance_and_hsg_isolation(db):
    """Mechanism 1 & 2:
    - A question anchored to `PHY12-KNTT-C1-B2` with difficulty APPLICATION (VD) automatically flows
      into both `SGK_KNTT` and `THPTQG` without manual tagging, and with `is_thinking_skill=True`
      also flows into `DGTD_TSA` — all from a single row in `questions`.
    - An Olympiad (`Difficulty.HSG`) question anchored to the same `PHY12-KNTT-C1-B2` flows into
      `HSG_TINH` / `HSG_QG` and does NOT leak into `THPTQG` or `SGK_KNTT`.
    """

    async def _run(session):
        root = await create_category(session, "Vật lí 12", slug="lop-12_vat-li-lop-12")
        await seed_knowledge_architecture(session)
        unit = await session.scalar(
            select(KnowledgeUnit).where(KnowledgeUnit.code == "PHY12-KNTT-C1-B2")
        )
        assert unit is not None

        before_count = await session.scalar(select(func.count()).select_from(Question))

        # 1. Create ONE question in PHY12-KNTT-C1-B2 (VD + Thinking Skill)
        q_multi = Question(
            root_id=root.id,
            category_id=root.id,
            knowledge_unit_id=unit.id,
            difficulty=Difficulty.APPLICATION,
            is_thinking_skill=True,
            question_type=QuestionType.MULTIPLE_CHOICE,
            content="Một con lắc lò xo dao động điều hòa có đồ thị biểu diễn lực kéo về theo thời gian.",
            choices=[
                {"id": "a", "content": "2 s", "is_correct": True},
                {"id": "b", "content": "1 s", "is_correct": False},
                {"id": "c", "content": "3 s", "is_correct": False},
                {"id": "d", "content": "4 s", "is_correct": False},
            ],
            source_type=SourceType.MANUAL,
            access_tier=AccessTier.FREE,
            is_verified=True,
        )
        # 2. Create ONE Olympiad (HSG) question in the same unit PHY12-KNTT-C1-B2
        q_hsg = Question(
            root_id=root.id,
            category_id=root.id,
            knowledge_unit_id=unit.id,
            difficulty=Difficulty.HSG,
            is_thinking_skill=False,
            question_type=QuestionType.MULTIPLE_CHOICE,
            content="Bài toán Học sinh giỏi Quốc gia: Hệ hai con lắc lò xo gắn với vật rắn quay không ma sát.",
            choices=[
                {"id": "a", "content": "10 rad/s", "is_correct": True},
                {"id": "b", "content": "20 rad/s", "is_correct": False},
                {"id": "c", "content": "15 rad/s", "is_correct": False},
                {"id": "d", "content": "25 rad/s", "is_correct": False},
            ],
            source_type=SourceType.MANUAL,
            access_tier=AccessTier.PRO,
            is_verified=True,
        )
        session.add_all([q_multi, q_hsg])
        await session.flush()

        after_count = await session.scalar(select(func.count()).select_from(Question))
        assert after_count == before_count + 2  # Zero duplication

        # Check SGK_KNTT view for chapter PHY12-KNTT-C1 (parent chapter expands to child lesson PHY12-KNTT-C1-B2)
        sgk_qs = await questions_for_target(
            session, "SGK_KNTT", knowledge_unit_code="PHY12-KNTT-C1"
        )
        sgk_ids = {q.id for q in sgk_qs}
        assert q_multi.id in sgk_ids
        assert q_hsg.id not in sgk_ids  # HSG excluded from standard SGK practice

        # Check THPTQG view: automatically inherits q_multi from PHY12-KNTT-C1-B2, excludes q_hsg
        thpt_qs = await questions_for_target(
            session, "THPTQG", knowledge_unit_code="PHY12-KNTT-C1"
        )
        thpt_ids = {q.id for q in thpt_qs}
        assert q_multi.id in thpt_ids
        assert q_hsg.id not in thpt_ids

        # Check DGTD_TSA view: includes q_multi via is_thinking_skill=True
        tsa_qs = await questions_for_target(
            session, "DGTD_TSA", knowledge_unit_code="PHY12-KNTT-C1"
        )
        tsa_ids = {q.id for q in tsa_qs}
        assert q_multi.id in tsa_ids
        assert q_hsg.id not in tsa_ids

        # Check HSG_TINH & chapter HSG helper: includes q_hsg and q_multi (since HSG now includes VD + VDC)
        hsg_qs = await hsg_questions_for_unit(
            session, knowledge_unit_code="PHY12-KNTT-C1"
        )
        hsg_ids = {q.id for q in hsg_qs}
        assert q_hsg.id in hsg_ids
        assert q_multi.id in hsg_ids

    db(_run)


def test_ai_publish_drafts_populates_2d_coordinates_and_target_tags(db):
    """Publishing AI drafts automatically sets `knowledge_unit_id`, `is_thinking_skill`,
    `access_tier`, and inserts `QuestionTargetTag` rows."""

    async def _run(session):
        root = await create_category(session, "Vật lí 12", slug="vat-li-12")
        await seed_knowledge_architecture(session)

        job = ImportJob(
            root_id=root.id,
            category_id=root.id,
            original_filename="de_vat_li_12.pdf",
            stored_path="uploads/de_vat_li_12.pdf",
            default_difficulty=Difficulty.UNDERSTANDING,
            drafts=[
                {
                    "number": 1,
                    "page": 1,
                    "type": "MULTIPLE_CHOICE",
                    "content": "Cho đồ thị biểu diễn sự phụ thuộc của li độ dao động điều hòa vào thời gian.",
                    "choices": ["2 cm", "4 cm", "6 cm", "8 cm"],
                    "correct": 1,
                    "difficulty": "APPLICATION",
                    "kntt_unit_code": "PHY12-KNTT-C1-B2",
                    "is_thinking_skill": True,
                    "suitable_targets": ["SGK_KNTT", "THPTQG", "DGTD_TSA"],
                    "explanation": "Từ đồ thị ta đọc được biên độ A = 4 cm.",
                },
                {
                    "number": 2,
                    "page": 1,
                    "type": "MULTIPLE_CHOICE",
                    "content": "Câu hỏi Học sinh giỏi Olympic: Tìm chu kì dao động nhỏ của hệ cơ vật rắn.",
                    "choices": ["1,2 s", "1,5 s", "2,0 s", "2,5 s"],
                    "correct": 0,
                    "difficulty": "HSG",
                    "kntt_unit_code": "PHY12-KNTT-C1-B2",
                    "is_thinking_skill": False,
                    "suitable_targets": ["HSG_TINH", "HSG_QG"],
                    "explanation": "Áp dụng định lý Huygens-Steiner.",
                },
            ],
        )
        session.add(job)
        await session.flush()

        published = await publish_drafts(session, job, [0, 1], verified=True)
        assert len(published) == 2
        q1, q2 = published[0], published[1]

        unit = await session.scalar(
            select(KnowledgeUnit).where(KnowledgeUnit.code == "PHY12-KNTT-C1-B2")
        )
        assert q1.knowledge_unit_id == unit.id
        assert q1.is_thinking_skill is True
        assert q1.access_tier == AccessTier.FREE

        assert q2.knowledge_unit_id == unit.id
        assert q2.difficulty == Difficulty.ADVANCED_APPLICATION  # HSG drafts are stored as ADVANCED_APPLICATION
        assert q2.access_tier == AccessTier.PRO

        # Verify q1 is retrievable via DGTD_TSA and THPTQG, and q2 via HSG_QG
        tsa_ids = {q.id for q in await questions_for_target(session, "DGTD_TSA")}
        hsg_qg_ids = {q.id for q in await questions_for_target(session, "HSG_QG")}
        assert q1.id in tsa_ids
        assert q2.id in hsg_qg_ids

    db(_run)


def test_question_bank_is_admin_only(db, make_user):
    """Verify that the raw question bank is strictly accessible to Admin only,
    and public / student navigation does not expose a question bank browsing view."""

    async def _setup(session):
        root = await create_category(session, "Vật lí 12", slug="vat-li-12-bank")
        await seed_knowledge_architecture(session)
        unit = await session.scalar(
            select(KnowledgeUnit).where(KnowledgeUnit.code == "PHY12-KNTT-C1-B2")
        )
        session.add(
            Question(
                root_id=root.id,
                category_id=root.id,
                knowledge_unit_id=unit.id,
                difficulty=Difficulty.APPLICATION,
                question_type=QuestionType.MULTIPLE_CHOICE,
                content="Câu hỏi bảo mật trong ngân hàng đề chỉ Admin xem được.",
                choices=[
                    {"id": "a", "content": "1", "is_correct": True},
                    {"id": "b", "content": "2", "is_correct": False},
                ],
                source_type=SourceType.MANUAL,
                access_tier=AccessTier.FREE,
                is_verified=True,
            )
        )
        await session.flush()

    db(_setup)

    # 1. Student cannot access /admin/questions
    client_student = _login(make_user, "student_bank@test.local", role=UserRole.STUDENT)
    res_student = client_student.get("/admin/questions")
    assert res_student.status_code in (403, 303, 302)

    # 2. Public /targets returns 404
    res_targets = client_student.get("/targets")
    assert res_targets.status_code == 404

    # 3. Admin can access /admin/questions
    client_admin = _login(make_user, "admin_bank@test.local", role=UserRole.ADMIN)
    res_admin = client_admin.get("/admin/questions")
    assert res_admin.status_code == 200
    assert "Ngân hàng câu hỏi" in res_admin.text
    assert "Câu hỏi bảo mật trong ngân hàng đề" in res_admin.text


def test_cross_curriculum_auto_mapping_to_kntt():
    """Verify that topics from other textbooks (Cánh Diều, Chân Trời Sáng Tạo)
    automatically map to the corresponding SGK Kết nối tri thức chapter."""
    from app.domain.cross_curriculum_mapper import map_other_book_to_kntt

    kntt_chapters = [
        {"id": 20, "name": "Chương I. Mở đầu"},
        {"id": 21, "name": "Chương II. Động học"},
        {"id": 22, "name": "Chương III. Động lực học"},
        {"id": 61, "name": "Chương IV. Năng lượng, công, công suất"},
        {"id": 62, "name": "Chương V. Chuyển động tròn"},
    ]

    # Test Cánh diều chapter: "Chủ đề 1. Mô tả chuyển động" -> KNTT "Chương II. Động học"
    match1 = map_other_book_to_kntt("Chủ đề 1. Mô tả chuyển động", kntt_chapters)
    assert match1 is not None
    assert match1["id"] == 21

    # Test Chân trời sáng tạo: "Chương 6. Năng lượng" -> KNTT "Chương IV. Năng lượng, công, công suất"
    match2 = map_other_book_to_kntt("Chương 6. Năng lượng", kntt_chapters)
    assert match2 is not None
    assert match2["id"] == 61

    # Test Cánh diều: "Chủ đề 2. Lực và chuyển động" -> KNTT "Chương III. Động lực học"
    match3 = map_other_book_to_kntt("Chủ đề 2. Lực và chuyển động", kntt_chapters)
    assert match3 is not None
    assert match3["id"] == 22


def test_admin_question_bank_3_part_toc_and_target_filter(db, make_user):
    """Verify the 2-level cascading selector in the admin question bank
    (Pillars: Curriculum, Major Exams, Olympiad -> Dynamic Scope selector)
    and filtering by target scopes (THPTQG, HSG_TINH)."""
    from app.scope import (
        TargetScope,
        get_section_scope_data,
        resolve_scope,
    )

    async def _test(session):
        await create_category(session, "Toán 11", slug="lop-11_toan-lop-11")
        await seed_knowledge_architecture(session)
        scope_data = await get_section_scope_data(session)
        sec_opts = scope_data["section_options"]

        # 1. Must have all 4 section keys
        assert "all" in sec_opts
        assert "curriculum" in sec_opts
        assert "major_exam" in sec_opts
        assert "olympiad" in sec_opts

        # Check curriculum options: only clean curriculum subjects
        curr_keys = [o["key"] for o in sec_opts["curriculum"]]
        assert any(k.startswith("c:") for k in curr_keys if k)

        # Check major_exam options
        me_keys = [o["key"] for o in sec_opts["major_exam"]]
        assert "target:THPTQG" in me_keys
        assert "target:DGTD_TSA" in me_keys
        assert "target:VAO_10" in me_keys

        # Check olympiad options
        oly_keys = [o["key"] for o in sec_opts["olympiad"]]
        assert "target:HSG_TINH" in oly_keys
        assert "target:HSG_QG" in oly_keys
        assert "target:OLYMPIAD_CHUYEN" in oly_keys

        # 2. Test resolve_scope on targets
        t_scope = await resolve_scope(session, "target:THPTQG")
        assert isinstance(t_scope, TargetScope)
        assert t_scope.code == "THPTQG"
        conds = t_scope.conditions(Question)
        assert len(conds) > 0

        hsg_scope = await resolve_scope(session, "target:HSG_TINH")
        assert isinstance(hsg_scope, TargetScope)
        assert hsg_scope.code == "HSG_TINH"
        assert len(hsg_scope.conditions(Question)) > 0

    db(_test)

    # 3. Test HTTP Admin Question Bank page rendering with 2-level cascading selector
    client_admin = _login(make_user, "admin_toc@test.local", role=UserRole.ADMIN)
    resp = client_admin.get("/admin/questions?section=major_exam&scope=target:THPTQG")
    assert resp.status_code == 200
    assert "section-select" in resp.text
    assert "scope-select" in resp.text
    assert "Phần 1: Bám sát chương trình học" in resp.text
    assert "Phần 2: Kỳ thi quan trọng" in resp.text
    assert "Phần 3: Bồi dưỡng Học sinh giỏi" in resp.text
    assert "target:THPTQG" in resp.text


