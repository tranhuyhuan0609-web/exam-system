"""Library: Google Drive sync (folders, rights, AI import targets) and access for teachers with Pro."""

import asyncio
import unicodedata
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.config import get_settings
from app.infrastructure.storage import get_bronze_storage, get_storage
from app.main import app
from app.models import Category, ImportJob, LibraryDocument, LibraryKind, LibraryStatus, User, UserRole
from app.pipeline.adapters.drive import FOLDER, DriveFile
from app.pipeline.adapters.memory import MemoryRawStore
from app.pipeline.use_cases.drive_sync import DriveSource, sync_drive

EXAMS = unicodedata.normalize("NFD", "9. FULL ĐỀ THI THPTQG2024 (BỘ GD&ĐT)")  # Drive may store decomposed names


class FakeDrive:
    def __init__(self):
        self.tree = {
            "root": [
                DriveFile("f-exams", EXAMS, FOLDER),
                DriveFile("f-books", "8. KHO SÁCH CHƯƠNG TRÌNH MỚI", FOLDER),
                DriveFile("f-k10", "1. Khối 10 - Chương trình mới", FOLDER),
            ],
            "f-exams": [
                DriveFile("f-math", "Môn Toán", FOLDER),
                DriveFile("x-chem", "Đề Hóa học mã 201.pdf", "application/pdf", 9, "m1"),
                DriveFile("x-zip", "all.zip", "application/zip", 9, "m2"),
            ],
            "f-math": [DriveFile("x-math", "Đề Toán mã 101.pdf", "application/pdf", 9, "m3")],
            "f-books": [DriveFile("x-book", "SGK Toán 10.pdf", "application/pdf", 9, "m4")],
            "f-k10": [DriveFile("x-doc", "Chuyên đề hàm số", "application/vnd.google-apps.document", 0, None)],
        }
        self.downloads = []

    async def children(self, folder_id):
        return self.tree.get(folder_id, [])

    async def download(self, file):
        self.downloads.append(file.id)
        mime = "application/pdf" if file.mime == "application/vnd.google-apps.document" else file.mime
        return b"%PDF-1.4 " + file.id.encode(), mime


SOURCE = DriveSource.model_validate(
    {
        "source_id": "demo",
        "name": "Demo",
        "folder_id": "root",
        "include": [
            {
                "folder": "9. FULL ĐỀ THI THPTQG2024 (BỘ GD&ĐT)",
                "kind": "EXAM",
                "status": "PUBLISHED",
                "downloadable": True,
                "license_note": "Đề chính thức",
                "targets": [{"match": "to[aá]n", "category": ["Ôn thi THPT – Toán", "Đề thi THPT 2024"]}],
            },
            {"folder": "1. Khối 10 - Chương trình mới", "kind": "TOPIC"},
        ],
    }
)


def _sync(db, drive):
    store = MemoryRawStore()
    return db(lambda s: sync_drive(s, SOURCE, drive, get_bronze_storage(), store, get_storage())), store


def test_drive_sync_takes_chosen_folders_and_queues_ai_import_for_targets(db):
    drive = FakeDrive()
    stats, store = _sync(db, drive)
    assert stats == {"new": 3, "updated": 0, "unchanged": 0, "skipped": 1, "failed": 0, "import_jobs": 1}
    docs = {d.title: d for d in db(lambda s: s.scalars(select(LibraryDocument)))}
    assert set(docs) == {"Đề Toán mã 101", "Đề Hóa học mã 201", "Chuyên đề hàm số"}  # no textbook, no zip
    math = docs["Đề Toán mã 101"]
    assert (math.kind, math.status, math.downloadable) == (LibraryKind.EXAM, LibraryStatus.PUBLISHED, True)
    assert math.folder_path == [EXAMS, "Môn Toán"] and math.storage_key == "drive/demo/x-math.pdf"
    assert (
        docs["Chuyên đề hàm số"].status == LibraryStatus.PENDING_REVIEW
        and docs["Chuyên đề hàm số"].mime == "application/pdf"
    )
    job = db(lambda s: s.get(ImportJob, math.import_job_id))
    category = db(lambda s: s.get(Category, job.category_id))
    assert category.name == "Đề thi THPT 2024" and docs["Đề Hóa học mã 201"].import_job_id is None
    assert asyncio.run(get_bronze_storage().get(math.storage_key)) == b"%PDF-1.4 x-math"
    assert len(store.docs) == 3  # Bronze record of each file

    # Nothing changed on Drive: nothing downloaded again; an admin's decisions survive a later change.
    drive.downloads.clear()
    stats, _ = _sync(db, drive)
    assert stats["unchanged"] == 2 and drive.downloads == ["x-doc"]  # Google Docs have no checksum
    db(lambda s: s.execute(update(LibraryDocument).values(downloadable=False)))
    drive.tree["f-math"][0].md5 = "m3-new"
    stats, _ = _sync(db, drive)
    assert stats["updated"] == 2 and db(lambda s: s.scalar(select(func.count()).select_from(ImportJob))) == 1
    assert not db(
        lambda s: s.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == "x-math"))
    ).downloadable


def _login(make_user, email, role=UserRole.STUDENT):
    make_user(email, role=role)
    client = TestClient(app)
    client.post("/login", data={"email": email, "password": "password123"})
    return client


@pytest.fixture
def library(db):
    _sync(db, FakeDrive())
    return {d.title: d.id for d in db(lambda s: s.scalars(select(LibraryDocument)))}


def _pro(db, email):
    db(lambda s: s.execute(update(User).where(User.email == email).values(pro_until=func.now() + timedelta(days=30))))


def test_free_accounts_read_a_few_documents_a_day_and_never_download(make_user, db, library, monkeypatch):
    monkeypatch.setattr(get_settings(), "free_docs_per_day", 1)
    student = _login(make_user, "hs@example.com")
    root = student.get("/library").text
    assert (
        "9. FULL ĐỀ THI THPTQG2024" in unicodedata.normalize("NFC", root) and "Khối 10" not in root
    )  # pending: hidden
    assert "còn 1/1 lượt xem" in root
    math, chem = library["Đề Toán mã 101"], library["Đề Hóa học mã 201"]
    pdf = student.get(f"/library/{math}/view")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("inline")
    assert student.get(f"/library/{math}/view").status_code == 200  # the same document again: no new read
    assert student.get(f"/library/{chem}/view").status_code == 403  # daily limit reached
    assert student.get(f"/library/{math}/download").status_code == 403  # downloadable exam, but a student
    assert student.get(f"/library/{library['Chuyên đề hàm số']}/view").status_code == 404  # waiting for review

    _pro(db, "hs@example.com")  # student Pro: unlimited reading, still no downloads
    assert student.get(f"/library/{chem}/view").status_code == 200
    assert student.get(f"/library/{math}/download").status_code == 403
    assert "Tải về" not in student.get("/library", params={"folder": f"{EXAMS}/Môn Toán"}).text


def test_teacher_pro_downloads_exam_papers_but_not_books(admin_client, make_user, db, library):
    teacher = _login(make_user, "gv@example.com", UserRole.TEACHER)
    math = library["Đề Toán mã 101"]
    assert teacher.get(f"/library/{math}/download").status_code == 403  # no Pro yet
    _pro(db, "gv@example.com")
    assert teacher.get(f"/library/{math}/download").status_code == 200
    assert "Tải về" in teacher.get("/library", params={"folder": f"{EXAMS}/Môn Toán"}).text
    admin_client.post("/admin/library/bulk", data={"ids": [str(math)], "action": "kind:BOOK"})
    assert teacher.get(f"/library/{math}/download").status_code == 403  # books: read online only
    assert teacher.get(f"/library/{math}/view").status_code == 200
    assert admin_client.get(f"/library/{math}/download").status_code == 200  # admins: everything


def test_admin_reviews_and_restricts_downloads(admin_client, make_user, db, library):
    page = admin_client.get("/admin/library").text
    assert "Chuyên đề hàm số" in page  # the pending tab by default
    doc_id = library["Chuyên đề hàm số"]
    admin_client.post("/admin/library/bulk", data={"ids": [str(doc_id)], "action": "publish"})
    admin_client.post("/admin/library/bulk", data={"ids": [str(library["Đề Toán mã 101"])], "action": "view_only"})
    teacher = _login(make_user, "gv@example.com", UserRole.TEACHER)
    db(
        lambda s: s.execute(
            update(User).where(User.email == "gv@example.com").values(pro_until=func.now() + timedelta(days=30))
        )
    )
    assert teacher.get(f"/library/{doc_id}/view").status_code == 200
    assert teacher.get(f"/library/{library['Đề Toán mã 101']}/download").status_code == 403
    assert "Tải về" not in teacher.get("/library", params={"folder": f"{EXAMS}/Môn Toán"}).text


def test_drive_sync_stops_when_google_blocks_automated_downloads(db):
    from app.pipeline.adapters.drive import DriveBlocked

    class BlockedDrive(FakeDrive):
        async def download(self, file):
            self.downloads.append(file.id)
            raise DriveBlocked("sorry")

    drive = BlockedDrive()
    stats, _ = _sync(db, drive)
    assert stats["blocked"] == 1 and len(drive.downloads) == 1  # no hammering after the first refusal
    assert db(lambda s: s.scalar(select(func.count()).select_from(LibraryDocument))) == 0
