"""Open sources: papers and open books into the library (one row per work), Open Trivia into the question bank."""

import asyncio
import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import LibraryDocument, LibraryKind
from app.pipeline.adapters.memory import MemoryRawStore
from app.pipeline.harvest import files, trivia
from app.pipeline.harvest.sources import OpenRecord, openalex
from app.pipeline.harvest.store import PlanItem, TriviaPlan, save_records
from app.pipeline.parsers.opentrivia import parse


class FakeHttp:
    def __init__(self, *pages):
        self.pages, self.calls = list(pages), []

    async def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return self.pages.pop(0)

    async def close(self):
        pass


def _record(key, source="openalex", title="Giáo dục STEM ở trường phổ thông", **extra):
    return OpenRecord(
        key=key,
        source=source,
        title=title,
        kind=LibraryKind.PAPER,
        folder=["Bài báo & nghiên cứu", "Giáo dục học"],
        url="https://journal.example/a",
        **extra,
    )


def test_openalex_pages_follow_the_cursor_and_file_papers_by_field(monkeypatch):
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1/ABC",
        "title": "Dạy học  hàm số",
        "publication_year": 2024,
        "language": "vi",
        "type": "article",
        "best_oa_location": {"pdf_url": "https://j.example/1.pdf", "landing_page_url": "https://j.example/1"},
        "authorships": [{"author": {"display_name": f"Tác giả {i}"}} for i in range(7)],
        "primary_topic": {"field": {"id": "https://openalex.org/fields/26"}},
    }
    http = FakeHttp({"results": [work], "meta": {"next_cursor": "c2"}}, {"results": [], "meta": {"next_cursor": None}})
    item = PlanItem(name="vi", folder=["Bài báo & nghiên cứu", "Tiếng Việt"], filter="language:vi", by_field=True)
    records, state = asyncio.run(openalex(http, item, None))
    record = records[0]
    assert state == "p:2" and http.calls[0][1]["page"] == 1 and http.calls[0][1]["filter"] == "language:vi"
    assert record.key == "doi:10.1/abc" and record.title == "Dạy học hàm số" and record.file_url.endswith("1.pdf")
    assert record.folder == ["Bài báo & nghiên cứu", "Tiếng Việt", "Toán học"]
    assert record.authors.endswith("Tác giả 4 và cộng sự")
    assert asyncio.run(openalex(http, item, "p:2")) == ([], None)  # the last page: the item is done

    monkeypatch.setenv("OPENALEX_API_KEY", "k")  # with a key: cursor paging, no 10,000 limit
    keyed = FakeHttp({"results": [work], "meta": {"next_cursor": "c2"}})
    assert asyncio.run(openalex(keyed, item, None))[1] == "c2" and keyed.calls[0][1]["cursor"] == "*"
    last_page = FakeHttp({"results": [work], "meta": {}})  # page 50 read before the key: a cursor from the top
    assert asyncio.run(openalex(last_page, item, "p:50"))[1] == "*"


def test_a_work_found_by_two_sources_is_kept_once_and_listed_with_its_link(db, make_user):
    first = [_record("doi:10.1/abc", file_url="https://j.example/1.pdf", license="cc-by", year=2024)]
    assert db(lambda s: save_records(s, first)) == 1
    again = [_record("doi:10.1/abc", source="crossref"), _record("openlibrary:OL1W", source="openlibrary")]
    assert db(lambda s: save_records(s, again)) == 1  # the DOI is known
    doc = db(lambda s: s.scalar(select(LibraryDocument).where(LibraryDocument.source_file_id == "doi:10.1/abc")))
    assert doc.source == "openalex" and doc.storage_key == "" and doc.license_note == "cc-by · OpenAlex"

    make_user("hs@example.com")
    student = TestClient(app)
    student.post("/login", data={"email": "hs@example.com", "password": "password123"})
    home = student.get("/library").text
    assert "Bài báo &amp; nghiên cứu" in home and '<span class="muted small">2</span>' in home
    folder = student.get("/library", params={"folder": "Bài báo & nghiên cứu/Giáo dục học"}).text
    assert "Đọc tại nguồn" in folder and "https://journal.example/a" in folder and "2024" in folder
    assert "Giáo dục STEM" in student.get("/library", params={"q": "stem"}).text
    assert "Giáo dục STEM" not in student.get("/library", params={"q": "100%"}).text  # % is not a wildcard
    assert student.get(f"/library/{doc.id}/view").status_code == 404  # no stored file yet


def test_open_pdfs_are_stored_and_links_without_a_pdf_are_dropped(db, monkeypatch):
    db(
        lambda s: save_records(
            s, [_record("doi:1", file_url="https://a/1.pdf"), _record("doi:2", file_url="https://b/2")]
        )
    )

    async def fetch(client, url, max_bytes):
        if url.endswith(".pdf"):
            return b"%PDF-1.7 paper"
        raise files.NotAPdf("a landing page")

    monkeypatch.setattr(files, "fetch_pdf", fetch)
    assert asyncio.run(files.store_files(limit=10)) == {"stored": 1, "failed": 1}
    docs = {d.source_file_id: d for d in db(lambda s: s.scalars(select(LibraryDocument)))}
    assert docs["doi:1"].mime == "application/pdf" and docs["doi:1"].storage_key.startswith("open/openalex/")
    assert docs["doi:2"].file_url is None and docs["doi:2"].storage_key == ""
    assert asyncio.run(files.store_files(limit=10)) == {"stored": 0, "failed": 0}  # nothing tried twice


class States:
    def __init__(self):
        self.rows = {}

    async def get(self, key):
        return self.rows.get(key, {"_id": key, "done": False, "records": 0})

    async def put(self, key, **values):
        self.rows[key] = {**self.rows.get(key, {}), **values, "updated_at": datetime.now(UTC)}


class Bus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, value, key=None):
        self.events.append(value)


def test_open_trivia_takes_every_question_of_a_category_once(db, monkeypatch):
    question = {
        "type": "multiple",
        "difficulty": "easy",
        "category": "Science: Mathematics",
        "question": "What is 2 &amp; 2?",
        "correct_answer": "4",
        "incorrect_answers": ["3", "5", "22"],
    }
    pages = [
        {"token": "t"},
        {"response_code": 0, "results": [question]},
        {"response_code": 1, "results": []},  # fewer than 50 left: ask for 10
        {"response_code": 0, "results": [{**question, "question": "What is 3 + 3?", "correct_answer": "6"}]},
        {"response_code": 4, "results": []},  # all given
    ]
    monkeypatch.setattr(trivia, "Http", lambda interval: FakeHttp(*pages))
    store, bus, states = MemoryRawStore(), Bus(), States()
    plan = TriviaPlan(categories={19: "Toán học"})
    stats = asyncio.run(trivia.harvest_trivia(plan, store, bus, states))
    assert stats == {"batches": 2, "questions": 2} and len(bus.events) == 2 and states.rows["opentrivia:19"]["done"]
    doc = next(iter(store.docs.values()))
    assert doc.site_id == "opentrivia" and doc.context == {
        "root": "Ngân hàng trắc nghiệm quốc tế",
        "chapter": "Toán học",
    }
    page = parse(doc.html, doc.url)
    assert page.questions[0].content == "What is 2 & 2?"
    assert [c["is_correct"] for c in page.questions[0].choices].count(True) == 1

    monkeypatch.setattr(trivia, "Http", lambda interval: FakeHttp({"token": "t"}))
    assert asyncio.run(trivia.harvest_trivia(plan, store, bus, states)) == {"batches": 0, "questions": 0}
    assert db(lambda s: s.scalar(select(func.count()).select_from(LibraryDocument))) == 0
    assert json.loads(doc.html)["results"][0]["correct_answer"] == "4"
