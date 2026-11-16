"""POST /drive/connections/{id}/fetch-and-import: a Drive folder -> exams to the bank, documents to the library.

Offline: Drive v3 (`files.list`, `files.get?alt=media`) is an httpx MockTransport and the AI a scripted Gemini, both
injected through the route's dependencies. Checked: the API key or the OAuth token of the connection is the one sent,
an exam PDF becomes solved, tagged DRAFT questions, a PDF without questions becomes a DOCUMENT content item, other
files are reported and not read, a second run ingests nothing twice, and only authors / admins may run it.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.modules.drive_sync import router as drive_router
from app.ops.seed_demo import seed_demo
from tests.ai_fakes import fake_solver
from tests.pdf_fixtures import A4, PdfSketch
from tests.test_question_bank_api_db import _login
from tests.test_teacher_pdf_to_exam_room_db import _exam_pdf

FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"


def _document_pdf() -> bytes:
    sketch = PdfSketch()
    page = sketch.new_page(*A4)
    page.text(48, 60, "LY THUYET: DAO HAM VA UNG DUNG", size=13)
    page.text(48, 90, "Dao ham cua ham so tai mot diem la gioi han cua ti so gia.", size=11)
    page.text(48, 110, "Quy tac: (u + v)' = u' + v'; (uv)' = u'v + uv'.", size=11)
    return sketch.tobytes()


class Drive:
    """A Drive folder: listing, contents, and what was asked (to check the credential sent)."""

    def __init__(self) -> None:
        self.files = [
            {"id": "file-exam-0001", "name": "De kiem tra dao ham.pdf", "mimeType": "application/pdf"},
            {"id": "file-doc-00002", "name": "Ly thuyet dao ham.pdf", "mimeType": "application/pdf"},
            {"id": "file-sheet-003", "name": "Bang diem", "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
        self.content = {"file-exam-0001": _exam_pdf(), "file-doc-00002": _document_pdf()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/drive/v3/files":
            assert f"'{FOLDER}' in parents" in request.url.params["q"]
            return httpx.Response(200, json={"files": self.files})
        file_id = path.rsplit("/", 1)[-1]
        if request.url.params.get("alt") == "media" and file_id in self.content:
            return httpx.Response(200, content=self.content[file_id])
        return httpx.Response(404)


@pytest.fixture
def world(client, monkeypatch):
    seeded = asyncio.run(seed_demo())
    drive = Drive()
    solver, _ = fake_solver({"Dao ham cua ham so y": {"answer": "A", "solution_steps": "y' = 3x^2 - 3"}})
    app.dependency_overrides[drive_router.get_drive_transport] = lambda: httpx.MockTransport(drive)
    app.dependency_overrides[drive_router.get_drive_solver] = lambda: solver
    monkeypatch.setattr(get_settings(), "google_drive_api_key", "drive-api-key-test")
    author = _login(client, "content_author@h2edu.demo")
    connected = client.post(
        "/api/v1/integrations/google-drive/connect",
        headers=author,
        json={"name": "Kho đề trường", "secret_vault_ref": "vault://drive/public-folder", "rights_status": "OWNED"},
    )
    assert connected.status_code == 201, connected.text
    yield {
        "tenant_id": uuid.UUID(seeded["tenant"]["id"]),
        "author": author,
        "teacher": _login(client, "teacher@h2edu.demo"),
        "connection_id": connected.json()["id"],
        "drive": drive,
    }
    app.dependency_overrides.pop(drive_router.get_drive_transport, None)
    app.dependency_overrides.pop(drive_router.get_drive_solver, None)


def _run(client, w, headers=None, **body):
    return client.post(
        f"/api/v1/drive/connections/{w['connection_id']}/fetch-and-import",
        headers=headers or w["author"],
        json={"folder_id": FOLDER, **body},
    )


async def _rows(sql: str, **params):
    async with SessionLocal() as session:
        return (await session.execute(text(sql), params)).mappings().all()


def test_a_folder_fills_the_bank_with_its_exam_and_the_library_with_its_document(client, world):
    done = _run(client, world)
    assert done.status_code == 200, done.text
    body = done.json()
    by_id = {f["drive_file_id"]: f for f in body["files"]}
    assert body["listed"] == 3 and body["created_question_count"] == 3 and body["content_item_count"] == 1
    exam, doc, sheet = by_id["file-exam-0001"], by_id["file-doc-00002"], by_id["file-sheet-003"]
    # question 4 has no key and the AI gave none: it is counted, not stored without an answer
    assert exam["kind"] == "EXAM" and len(exam["question_ids"]) == 3 and exam["unresolved_questions"] == 1
    assert doc["kind"] == "DOCUMENT" and doc["content_item_id"] and doc["question_ids"] == []
    assert sheet["kind"] == "SKIPPED" and sheet["reason"].startswith("UNSUPPORTED_MIME")
    assert not any(r.url.path.endswith("file-sheet-003") for r in world["drive"].requests), "never downloaded"
    assert all(r.url.params.get("key") == "drive-api-key-test" for r in world["drive"].requests)

    questions = asyncio.run(
        _rows(
            "SELECT q.publication_status, q.rights_status, a.answer_schema, a.rationale_json "
            "FROM questions q JOIN question_versions v ON v.question_id = q.id "
            "JOIN question_version_answers a ON a.question_version_id = v.id "
            "WHERE q.tenant_id = :t AND q.stable_code LIKE 'GDRIVE-%' ORDER BY q.stable_code",
            t=world["tenant_id"],
        )
    )
    assert [q["answer_schema"] for q in questions] == [
        {"correct_option": "A"}, {"correct_option": "C"}, {"value": "3"}
    ]  # fmt: skip
    assert {(q["publication_status"], q["rights_status"]) for q in questions} == {("DRAFT", "OWNED")}
    assert questions[0]["rationale_json"]["provenance"]["drive_file_id"] == "file-exam-0001"
    item = asyncio.run(
        _rows(
            "SELECT i.content_type, i.publication_status, v.title FROM content_items i "
            "JOIN content_versions v ON v.content_item_id = i.id WHERE i.id = :i",
            i=doc["content_item_id"],
        )
    )[0]
    assert (item["content_type"], item["publication_status"]) == ("DOCUMENT", "DRAFT")
    assert item["title"] == "Ly thuyet dao ham.pdf"
    jobs = asyncio.run(
        _rows("SELECT count(*) AS n FROM acquisition_jobs WHERE tenant_id = :t AND job_type = 'DRIVE_SYNC'",
              t=world["tenant_id"])
    )  # fmt: skip
    assert jobs[0]["n"] == 2, "each PDF is a bronze document version of the Drive source"

    again = _run(client, world)
    assert again.status_code == 200 and again.json()["created_question_count"] == 0, again.text
    assert {f["reason"] for f in again.json()["files"] if f["kind"] == "SKIPPED"} >= {"ALREADY_INGESTED"}


def test_the_mode_forces_a_kind_and_a_token_of_the_connection_is_sent_as_bearer(client, world, monkeypatch):
    monkeypatch.setenv("DRIVE_TOKEN_TEST", "ya29-oauth-token-of-the-school")
    connected = client.post(
        "/api/v1/integrations/google-drive/connect",
        headers=world["author"],
        json={"name": "Drive riêng", "secret_vault_ref": "env:DRIVE_TOKEN_TEST", "rights_status": "OWNED"},
    )
    assert connected.status_code == 201, connected.text
    world["connection_id"] = connected.json()["id"]
    done = _run(client, world, mode="DOCUMENTS", max_files=2)
    assert done.status_code == 200, done.text
    assert {f["kind"] for f in done.json()["files"]} == {"DOCUMENT"} and done.json()["created_question_count"] == 0
    sent = world["drive"].requests[0]
    assert sent.headers["authorization"] == "Bearer ya29-oauth-token-of-the-school" and "key" not in sent.url.params


def test_without_a_credential_nothing_is_asked_of_drive(client, world, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_drive_api_key", "")
    refused = _run(client, world)
    assert refused.status_code == 403 and refused.json()["policy_code"] == "DRIVE_CREDENTIALS_MISSING", refused.text
    assert world["drive"].requests == []


def test_only_authors_and_administrators_run_it_and_inputs_are_checked(client, world):
    assert _run(client, world, headers=world["teacher"]).status_code == 403
    assert _run(client, world, folder_id="../../etc").status_code == 422
    assert _run(client, world, mode="EVERYTHING").status_code == 422
    missing = client.post(
        f"/api/v1/drive/connections/{uuid.uuid4()}/fetch-and-import",
        headers=world["author"],
        json={"folder_id": FOLDER},
    )
    assert missing.status_code == 404
    assert world["drive"].requests == []


def test_no_database_transaction_is_open_while_drive_or_the_ai_answers(client, world, monkeypatch):
    from app.modules.drive_sync.internal import auto_ingest
    from tests.ai_fakes import fake_solver, watch_transactions

    watch = watch_transactions(monkeypatch, auto_ingest)
    solver, gemini = fake_solver({"Dao ham cua ham so y": {"answer": "A"}})
    gemini.watch = watch
    drive = world["drive"]

    def handler(request):
        watch.probe()
        return drive(request)

    app.dependency_overrides[drive_router.get_drive_transport] = lambda: httpx.MockTransport(handler)
    app.dependency_overrides[drive_router.get_drive_solver] = lambda: solver
    done = _run(client, world)
    assert done.status_code == 200 and done.json()["created_question_count"] == 3, done.text
    assert gemini.calls and drive.requests
    assert watch.io_inside_transaction == 0, "Drive and the AI were called with no transaction open"
