import asyncio

import pytest
from sqlalchemy import select, update

from app.application.categories import create_category
from app.domain.question_hash import content_hash, hash_of_choices
from app.infrastructure.storage import LocalStorage, StorageError, get_storage
from app.models import Exam, ExamQuestion, Question, SourceType

# ---------------------------------------------------------------- hashing


def test_hash_ignores_spacing_case_and_trailing_punctuation_but_not_meaning():
    a = content_hash("MULTIPLE_CHOICE", "Chu kì   của vật là?", ["$0{,}5$ s", "2 s"])
    assert a == content_hash("MULTIPLE_CHOICE", "chu kì của vật là", ["$0{,}5$s", "2 s."])
    assert a != content_hash("MULTIPLE_CHOICE", "Chu kì của vật là", ["2 s", "$0{,}5$ s"])  # option order matters
    assert a != content_hash("TRUE_FALSE", "Chu kì của vật là", ["$0{,}5$ s", "2 s"])
    assert hash_of_choices("SHORT_ANSWER", "x", []) == content_hash("SHORT_ANSWER", "x")
    assert len(a) == 64


# ---------------------------------------------------------------- storage


def test_local_storage_round_trip_and_key_safety(tmp_path):
    storage = LocalStorage(tmp_path)

    async def scenario():
        await storage.put("pages/1/page-001.png", b"png")
        assert await storage.get("pages/1/page-001.png") == b"png"
        assert await storage.exists("pages/1/page-001.png")
        await storage.delete_prefix("pages/1/")
        assert not await storage.exists("pages/1/page-001.png")
        with pytest.raises(StorageError):
            await storage.get("missing.png")
        for bad in ("../secret", "/etc/passwd", "a\\b", ""):
            with pytest.raises(StorageError):
                await storage.put(bad, b"x")

    asyncio.run(scenario())
    assert storage.public_url("figures/a.png") == "/media/figures/a.png"


def test_only_figures_are_publicly_served(client):
    storage = get_storage()
    asyncio.run(storage.put("figures/public.png", b"fig"))
    asyncio.run(storage.put("raw/secret.pdf", b"exam"))
    asyncio.run(storage.put("pages/1/page-001.png", b"page"))
    assert client.get("/media/figures/public.png").content == b"fig"
    assert client.get("/media/raw/secret.pdf").status_code == 404
    assert client.get("/media/pages/1/page-001.png").status_code == 404


# ---------------------------------------------------------------- provenance & verification


@pytest.fixture
def subject_id(db):
    async def _make(s):
        subject = await create_category(s, "Vật lí 12", slug="vat-li-12")
        return subject.id

    return db(_make)


def test_manual_questions_get_hash_and_source(admin_client, db, subject_id):
    admin_client.post(
        "/admin/questions",
        data={
            "scope": f"c:{subject_id}",
            "question_type": "MULTIPLE_CHOICE",
            "difficulty": "RECOGNITION",
            "content": "Đơn vị của lực là",
            "choice_content": ["N", "J"],
            "choice_id": ["", ""],
            "correct": "0",
        },
    )
    q = db(lambda s: s.scalar(select(Question)))
    assert (q.source_type, q.is_verified) == (SourceType.MANUAL, True)
    assert q.content_hash == content_hash("MULTIPLE_CHOICE", "Đơn vị của lực là", ["N", "J"])


def test_unverified_questions_cannot_be_added_to_exams_until_verified(admin_client, db, subject_id):
    async def _make(s):
        q = Question(
            root_id=subject_id,
            category_id=subject_id,
            difficulty="RECOGNITION",
            content="Câu thu thập từ web",
            choices=[{"id": "a", "content": "x", "is_correct": True}, {"id": "b", "content": "y", "is_correct": False}],
            source_type=SourceType.CRAWLER,
            is_verified=False,
        )
        exam = Exam(root_id=subject_id, category_id=subject_id, title="Đề", duration_seconds=600)
        s.add_all([q, exam])
        await s.flush()
        return q.id, exam.id

    qid, exam_id = db(_make)
    page = admin_client.get(f"/admin/exams/{exam_id}")
    assert "Câu thu thập từ web" not in page.text  # not offered in the picker
    admin_client.post(f"/admin/exams/{exam_id}/questions", data={"question_id": [str(qid)]})
    assert db(lambda s: s.scalars(select(ExamQuestion))) == []

    listing = admin_client.get("/admin/questions?verified=no")
    assert "Chưa duyệt" in listing.text and "Thu thập" in listing.text
    admin_client.post(f"/admin/questions/{qid}/verify")
    assert db(lambda s: s.get(Question, qid)).is_verified
    admin_client.post(f"/admin/exams/{exam_id}/questions", data={"question_id": [str(qid)]})
    assert len(db(lambda s: s.scalars(select(ExamQuestion)))) == 1


def test_file_import_flags_duplicates_by_hash_even_with_different_spacing(admin_client, db, subject_id):
    async def _seed(s):
        s.add(
            Question(
                root_id=subject_id,
                category_id=subject_id,
                difficulty="RECOGNITION",
                content="Đơn vị của lực là",
                choices=[
                    {"id": "a", "content": "N", "is_correct": True},
                    {"id": "b", "content": "J", "is_correct": False},
                ],
                content_hash=content_hash("MULTIPLE_CHOICE", "Đơn vị của lực là", ["N", "J"]),
            )
        )

    db(_seed)
    db(lambda s: s.execute(update(Question).values(is_verified=True)))
    text = "Câu 1:   đơn vị  của lực là\nA. N\nB. J\nĐáp án: A\n"
    r = admin_client.post(
        "/admin/questions/import/preview",
        data={"scope": f"c:{subject_id}", "default_difficulty": "RECOGNITION"},
        files={"file": ("de.txt", text.encode(), "text/plain")},
    )
    assert "Trùng" in r.text
