"""GET /api/v1/imports, GET /api/v1/exam-imports and the multipart POST /api/v1/imports, on real rows with RLS.

The studio screens list the tenant's import jobs and its exam-import review sessions; a review session opens at
`/studio/imports/{id}/review`. Everything is per tenant (another school sees none of it and cannot open it), and
only authors, reviewers and administrators may use it: a learner is refused on every route.
"""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile

import pytest

from app.ops.seed_demo import DEFAULT_PASSWORD, _demo_exam_pdf, seed_demo
from tests.test_question_bank_api_db import _login, _make_tenant_with_author


def _pdf(text_line: str) -> bytes:
    """A different one-page PDF per text (the upload deduplicates by checksum)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    page.drawString(50, A4[1] - 200, text_line)
    page.save()
    return buffer.getvalue()


def _docx() -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph("Cau 1. Cho P(A) = 0,3. Tinh P(A|B).")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return archive.getvalue()


@pytest.fixture
def world(client):
    seeded = asyncio.run(seed_demo())
    return {
        "author": _login(client, "content_author@h2edu.demo"),
        "teacher": _login(client, "teacher@h2edu.demo"),
        "learner": _login(client, "learner@h2edu.demo"),
        "tenant_admin": _login(client, "tenant_admin@h2edu.demo"),
        "exam_import_id": seeded["entities"]["exam_import_ids"][0],
    }


def _upload(client, headers, name: str, data: bytes, mime: str, **form):
    return client.post("/api/v1/imports", headers=headers, files={"file": (name, data, mime)}, data=form)


# --- POST /api/v1/imports (multipart) -------------------------------------------------------------------------------


def test_multipart_upload_of_a_pdf_and_of_a_docx_creates_jobs_and_a_repeat_is_a_duplicate(client, world):
    headers = world["author"]
    pdf = _upload(client, headers, "de-1.pdf", _pdf("Cau 1. Mot"), "application/pdf", title="Đề số 1")
    assert pdf.status_code == 201, pdf.text
    body = pdf.json()
    assert body["is_duplicate"] is False and body["idempotent_replay"] is False
    assert body["job"]["job_type"] == "EXAM_IMPORT" and body["job"]["job_status"] in ("QUEUED", "RUNNING")
    assert body["document_version"]["mime_type"] == "application/pdf" and body["document_version"]["title"] == "Đề số 1"
    assert body["job"]["document_version_id"] == body["document_version"]["id"]

    docx = _upload(
        client,
        headers,
        "de-2.docx",
        _docx(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        title="Đề Word",
    )
    assert docx.status_code == 201, docx.text
    word = docx.json()
    assert (
        word["document_version"]["mime_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert word["job"]["job_type"] == "EXAM_IMPORT" and word["job"]["job_status"] in ("QUEUED", "RUNNING")
    assert word["document_version"]["page_count"] is None, "a Word file has no page count until it is rendered"
    listed = {i["id"]: i for i in client.get("/api/v1/imports", headers=headers).json()["items"]}
    assert (
        listed[word["job"]["id"]]["mime_type"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    again = _upload(client, headers, "de-1-lai.pdf", _pdf("Cau 1. Mot"), "application/pdf")
    assert again.status_code == 200 and again.json()["is_duplicate"] is True, "the same bytes are not a new document"


def test_a_forged_type_a_wrong_mime_a_rar_or_no_file_are_refused_with_422(client, world):
    headers = world["author"]
    jobs_before = len(client.get("/api/v1/imports", headers=headers).json()["items"])
    rar = _upload(client, headers, "de.rar", b"Rar!\x1a\x07\x00 not supported", "application/vnd.rar")
    assert rar.status_code == 422 and rar.json()["error_code"] == "VALIDATION_ERROR", "RAR fails closed (ADR-010)"
    fake_docx = _upload(client, headers, "de.docx", _pdf("Cau 1."), "application/octet-stream")
    assert fake_docx.status_code == 422, "a PDF renamed .docx is refused by its magic bytes"
    fake_zip = _upload(client, headers, "de.zip", _pdf("Cau 1."), "application/zip")
    assert fake_zip.status_code == 422, "a PDF renamed .zip is refused by its magic bytes"

    fake_pdf = _upload(client, headers, "malware.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")
    assert fake_pdf.status_code == 422
    wrong_mime = _upload(client, headers, "de.pdf", _pdf("Cau 2."), "application/pdf", mime_type="image/png")
    assert wrong_mime.status_code == 422
    assert client.post("/api/v1/imports", headers=headers, data={"title": "x"}).status_code in (415, 422)
    no_file = client.post("/api/v1/imports", headers=headers, files={"other": ("a.txt", b"x", "text/plain")})
    assert no_file.status_code == 422
    jobs_after = len(client.get("/api/v1/imports", headers=headers).json()["items"])
    assert jobs_after == jobs_before, "refused uploads leave no job"


def test_the_idempotency_key_replays_the_same_upload_and_refuses_another_payload(client, world):
    headers = world["author"]
    first = _upload(client, headers, "de.pdf", _pdf("Cau 1. A"), "application/pdf", idempotency_key="idem-key-0001")
    replay = _upload(client, headers, "de.pdf", _pdf("Cau 1. A"), "application/pdf", idempotency_key="idem-key-0001")
    assert first.status_code == 201 and replay.status_code == 200 and replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["id"] == first.json()["job"]["id"]
    other = _upload(client, headers, "de.pdf", _pdf("Cau 9. B"), "application/pdf", idempotency_key="idem-key-0001")
    assert other.status_code == 422 and other.json()["error_code"] == "PAYLOAD_HASH_MISMATCH", other.text


def test_a_learner_cannot_upload_or_touch_an_import(client, world):
    learner = world["learner"]
    refused = _upload(client, learner, "de.pdf", _pdf("Cau 1."), "application/pdf")
    assert refused.status_code == 403 and refused.json()["error_code"] == "FORBIDDEN"
    job = _upload(client, world["author"], "de.pdf", _pdf("Cau 1."), "application/pdf").json()["job"]["id"]
    for method, url in (("get", f"/api/v1/imports/{job}"), ("post", f"/api/v1/imports/{job}/retry")):
        assert getattr(client, method)(url, headers=learner).status_code == 403, url
    assert client.post(f"/api/v1/imports/{job}/cancel", headers=learner).status_code == 403
    assert client.post("/api/v1/imports", files={"file": ("de.pdf", b"%PDF", "application/pdf")}).status_code == 401


# --- .zip archives --------------------------------------------------------------------------------------------------


def test_a_zip_of_exam_files_becomes_one_job_per_file_and_reports_what_it_skipped(client, world):
    headers = world["author"]
    archive = _zip(
        {
            "de/de-1.pdf": _pdf("Cau 1. Mot"),
            "de/de-2.docx": _docx(),
            "de/huong-dan.txt": b"not an exam",
            "__MACOSX/de/._de-1.pdf": b"junk",
            "de/hong.docx": b"PK\x03\x04 this is not really a docx",
        }
    )
    response = _upload(client, headers, "goi-de.zip", archive, "application/zip", idempotency_key="zip-key-0001")
    assert response.status_code == 201, response.text
    body = response.json()
    summary = body["archive"]
    assert [m["archive_path"] for m in summary["members"]] == ["de/de-1.pdf", "de/de-2.docx"]
    assert {m["mime_type"] for m in summary["members"]} == {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    assert [k["archive_path"] for k in summary["skipped"]] == ["de/hong.docx"], summary
    assert summary["ignored_entries"] == 2, "the .txt and the macOS resource fork are not importable"
    assert body["job"]["id"] == summary["members"][0]["job_id"], "`job` is the first file's job"
    assert len(summary["archive_sha256"]) == 64

    jobs = {i["id"]: i for i in client.get("/api/v1/imports", headers=headers).json()["items"]}
    for member in summary["members"]:
        assert jobs[member["job_id"]]["job_type"] == "EXAM_IMPORT"
        assert jobs[member["job_id"]]["document_version_id"] == member["document_version_id"]
    assert len(jobs) == 1 + 2, "the seed's job plus one job per good file; the archive itself is not a job"

    replay = _upload(client, headers, "goi-de.zip", archive, "application/zip", idempotency_key="zip-key-0001")
    assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True, replay.text
    assert [m["job_id"] for m in replay.json()["archive"]["members"]] == [m["job_id"] for m in summary["members"]]
    again = _upload(client, headers, "goi-de-lai.zip", archive, "application/zip")
    assert again.status_code == 200 and again.json()["is_duplicate"] is True, "the same files are not new documents"
    assert len(client.get("/api/v1/imports", headers=headers).json()["items"]) == 3


def test_unsafe_or_useless_archives_are_refused_and_leave_no_job(client, world):
    headers = world["author"]
    jobs_before = len(client.get("/api/v1/imports", headers=headers).json()["items"])

    slip = io.BytesIO()
    with zipfile.ZipFile(slip, "w") as z:
        z.writestr("../evil.pdf", _pdf("Cau 1."))
    link = io.BytesIO()
    with zipfile.ZipFile(link, "w") as z:
        info = zipfile.ZipInfo("de.pdf")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    bomb = _zip({"de.pdf": b"%PDF-1.4\n" + b"\x00" * (20 * 1024 * 1024)})
    cases = {
        "zip-slip": slip.getvalue(),
        "symlink": link.getvalue(),
        "zip bomb": bomb,
        "no exam file": _zip({"readme.txt": b"hello", "anh.png": b"\x89PNG\r\n\x1a\n"}),
        "only corrupt files": _zip({"de.pdf": b"%PDF- but truncated"}),
        "empty archive": _zip({}),
        "a Word file named .zip": None,
    }
    for label, payload in cases.items():
        if payload is None:
            payload = _docx()
        refused = _upload(client, headers, "goi.zip", payload, "application/zip")
        assert refused.status_code == 422, (label, refused.text)
        assert refused.json()["error_code"] == "VALIDATION_ERROR", label
    mime = _upload(
        client, headers, "goi.zip", _zip({"de.pdf": _pdf("Cau 1.")}), "application/zip", mime_type="application/pdf"
    )
    assert mime.status_code == 422, "a declared type that is not application/zip is refused"
    assert len(client.get("/api/v1/imports", headers=headers).json()["items"]) == jobs_before


def test_a_zip_is_per_tenant_and_closed_to_learners(client, world):
    archive = _zip({"de.pdf": _pdf("Cau rieng cua truong.")})
    assert _upload(client, world["learner"], "goi.zip", archive, "application/zip").status_code == 403
    mine = _upload(client, world["author"], "goi.zip", archive, "application/zip")
    assert mine.status_code == 201, mine.text
    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    job = mine.json()["archive"]["members"][0]["job_id"]
    assert client.get(f"/api/v1/imports/{job}", headers=other).status_code == 404
    theirs = _upload(client, other, "goi.zip", archive, "application/zip")
    assert theirs.status_code == 201, "the same files in another school are not a duplicate of ours"
    assert theirs.json()["archive"]["members"][0]["job_id"] != job


# --- from an import job to its review session ------------------------------------------------------------------------


def _open(client, headers, job_id: str):
    return client.post(f"/api/v1/exam-imports/from-job/{job_id}", headers=headers)


def test_an_import_job_opens_its_review_session_for_a_pdf_a_word_file_and_a_zip_member(client, world):
    headers = world["author"]
    pdf = _upload(client, headers, "de-pdf.pdf", _pdf("Cau 1. Pdf"), "application/pdf").json()["job"]["id"]
    docx = _upload(
        client,
        headers,
        "de-word.docx",
        _docx(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ).json()["job"]["id"]
    zipped = _upload(client, headers, "goi.zip", _zip({"de.pdf": _pdf("Cau 1. Zip")}), "application/zip").json()
    member = zipped["archive"]["members"][0]["job_id"]

    opened: dict[str, str] = {}
    for label, job_id in (("pdf", pdf), ("docx", docx), ("zip member", member)):
        listed = {i["id"]: i for i in client.get("/api/v1/imports", headers=headers).json()["items"]}
        assert listed[job_id]["exam_import_id"] is None, f"{label}: nothing is open before the first call"
        first = _open(client, headers, job_id)
        assert first.status_code == 201, (label, first.text)
        review = first.json()
        assert review["status"] == "PENDING_REVIEW" and review["rights_status"] in ("OWNED", "PENDING_REVIEW")
        assert review["proposed_structure_json"]["sections"] == [], "an empty structure: the reviewer draws it"
        assert review["validation"]["question_count"] == 0
        opened[job_id] = review["id"]

        again = _open(client, headers, job_id)
        assert again.status_code == 200 and again.json()["id"] == review["id"], f"{label}: idempotent"

        listed = {i["id"]: i for i in client.get("/api/v1/imports", headers=headers).json()["items"]}
        assert listed[job_id]["exam_import_id"] == review["id"], f"{label}: the import list points at the review"
        structure = client.get(f"/api/v1/exam-imports/{review['id']}/structure", headers=headers)
        assert structure.status_code == 200 and structure.json()["id"] == review["id"], label

    reviews = {i["id"]: i for i in client.get("/api/v1/exam-imports", headers=headers).json()["items"]}
    for job_id, review_id in opened.items():
        assert reviews[review_id]["acquisition_job_id"] == job_id
        assert reviews[review_id]["status"] == "PENDING_REVIEW" and reviews[review_id]["question_count"] == 0
    assert (
        reviews[opened[docx]]["mime_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # the reviewer fills the empty session the usual way (PATCH structure), still on the same id
    patched = client.patch(
        f"/api/v1/exam-imports/{opened[pdf]}/structure",
        headers=headers,
        json={
            "sections": [
                {
                    "section_code": "I",
                    "title": "Phan I",
                    "questions": [{"question_no": 1, "points": 10.0, "page_number": 1}],
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["validation"]["question_count"] == 1


def test_opening_a_review_is_per_tenant_role_gated_and_refused_for_a_dead_job(client, world):
    headers = world["author"]
    job = _upload(client, headers, "de.pdf", _pdf("Cau 1. A"), "application/pdf").json()["job"]["id"]
    assert _open(client, world["learner"], job).status_code == 403
    assert client.post(f"/api/v1/exam-imports/from-job/{job}").status_code == 401
    assert _open(client, headers, str(uuid.uuid4())).status_code == 404

    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    assert _open(client, other, job).status_code == 404, "another school cannot open our job"

    cancelled = client.post(f"/api/v1/imports/{job}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    dead = _open(client, headers, job)
    assert dead.status_code == 409 and "nothing to review" in dead.json()["detail"], dead.text


# --- GET /api/v1/imports ----------------------------------------------------------------------------------------------


def test_the_import_list_is_newest_first_filterable_and_paginated_by_cursor(client, world):
    headers = world["author"]
    baseline = [i["id"] for i in client.get("/api/v1/imports", headers=headers).json()["items"]]
    assert len(baseline) == 1, "the seed's own document upload is a job of the tenant"
    ids = [
        _upload(client, headers, f"de-{n}.pdf", _pdf(f"Cau {n}."), "application/pdf", title=f"Đề {n}").json()["job"][
            "id"
        ]
        for n in range(1, 4)
    ]
    body = client.get("/api/v1/imports", headers=headers).json()
    expected = ids[::-1] + baseline
    assert [i["id"] for i in body["items"]] == expected
    assert body["has_more"] is False and body["next_cursor"] is None
    newest = body["items"][0]
    assert set(newest) == {
        "id", "source_id", "document_version_id", "job_type", "job_status", "current_stage", "attempt_count",
        "last_error_code", "title", "mime_type", "page_count", "rights_status", "exam_import_id", "created_at",
        "updated_at",
    }  # fmt: skip
    assert newest["title"] == "Đề 3" and newest["mime_type"] == "application/pdf" and newest["document_version_id"]
    assert newest["job_type"] == "EXAM_IMPORT" and newest["attempt_count"] >= 0

    first = client.get("/api/v1/imports", params={"limit": 2}, headers=headers).json()
    assert [i["id"] for i in first["items"]] == expected[:2] and first["has_more"] is True
    second = client.get("/api/v1/imports", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers).json()
    assert [i["id"] for i in second["items"]] == expected[2:] and second["has_more"] is False

    status_of_newest = newest["job_status"]
    same = client.get("/api/v1/imports", params={"job_status": status_of_newest}, headers=headers).json()
    assert {i["id"] for i in same["items"]} >= {newest["id"]}
    assert client.get("/api/v1/imports", params={"job_status": "COMPLETED"}, headers=headers).json()["items"] == []
    assert client.get("/api/v1/imports", params={"job_type": "WEB_CRAWL"}, headers=headers).json()["items"] == []
    for bad in ({"job_status": "DONE"}, {"job_type": "X"}, {"limit": 0}, {"limit": 101}, {"cursor": "garbage"}):
        assert client.get("/api/v1/imports", params=bad, headers=headers).status_code == 422, bad

    detail = client.get(f"/api/v1/imports/{newest['id']}", headers=headers).json()
    assert detail["id"] == newest["id"] and detail["job_status"] == newest["job_status"], "the list opens the detail"


def test_the_import_list_is_per_tenant_and_closed_to_learners_and_visitors(client, world):
    seeded_jobs = [i["id"] for i in client.get("/api/v1/imports", headers=world["author"]).json()["items"]]
    mine = _upload(client, world["author"], "de.pdf", _pdf("Cau 1."), "application/pdf").json()["job"]["id"]
    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    assert client.get("/api/v1/imports", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/imports/{mine}", headers=other).status_code == 404
    theirs = _upload(client, other, "kia.pdf", _pdf("Cau khac."), "application/pdf").json()["job"]["id"]
    assert [i["id"] for i in client.get("/api/v1/imports", headers=other).json()["items"]] == [theirs]
    ours = [i["id"] for i in client.get("/api/v1/imports", headers=world["author"]).json()["items"]]
    assert ours == [mine, *seeded_jobs] and theirs not in ours
    assert client.get("/api/v1/imports", headers=world["learner"]).status_code == 403
    assert client.get("/api/v1/imports").status_code == 401
    assert client.get("/api/v1/imports", headers=world["tenant_admin"]).status_code == 200
    # a cursor minted for one tenant does not work for another
    for n in range(2):
        _upload(client, world["author"], f"x{n}.pdf", _pdf(f"Cau x{n}."), "application/pdf")
    cursor = client.get("/api/v1/imports", params={"limit": 1}, headers=world["author"]).json()["next_cursor"]
    assert client.get("/api/v1/imports", params={"cursor": cursor}, headers=other).status_code == 422


# --- GET /api/v1/exam-imports -----------------------------------------------------------------------------------------


def test_the_exam_import_list_opens_the_review_of_the_seeded_session(client, world):
    headers = world["author"]
    body = client.get("/api/v1/exam-imports", headers=headers).json()
    assert len(body["items"]) == 1 and body["has_more"] is False and body["next_cursor"] is None
    item = body["items"][0]
    assert item["id"] == world["exam_import_id"], "the id is the import_id of /studio/imports/{id}/review"
    assert set(item) == {
        "id", "extraction_run_id", "document_version_id", "title", "mime_type", "page_count", "status",
        "rights_status", "section_count", "question_count", "acquisition_job_id", "created_at",
    }  # fmt: skip
    assert item["status"] == "PENDING_REVIEW" and item["rights_status"] == "OWNED"
    assert (item["section_count"], item["question_count"]) == (1, 3)
    assert item["mime_type"] == "application/pdf" and item["page_count"] == 1 and item["document_version_id"]

    opened = client.get(f"/api/v1/exam-imports/{item['id']}/structure", headers=headers)
    assert opened.status_code == 200, opened.text
    assert opened.json()["id"] == item["id"] and opened.json()["document_version_id"] == item["document_version_id"]
    assert opened.json()["validation"]["question_count"] == 3


def test_the_exam_import_list_filters_by_status_and_pages_by_cursor(client, world):
    headers = world["author"]
    # a second session on another document, created like the seed does
    uploaded = client.post(
        "/api/v1/documents", headers=headers, files={"file": ("de-2.pdf", _pdf("Cau 1. Hai"), "application/pdf")}
    )
    assert uploaded.status_code == 201, uploaded.text
    run = client.post(f"/api/v1/documents/{uploaded.json()['source_document_id']}/extract", headers=headers).json()[
        "id"
    ]
    second = client.post(
        "/api/v1/exam-imports",
        headers=headers,
        json={
            "extraction_run_id": run,
            "expected_total_points": 10.0,
            "create_question_candidates": False,
            "sections": [
                {
                    "section_code": "PART_I_MCQ",
                    "title": "Phan I (2)",
                    "questions": [
                        {
                            "question_no": 1,
                            "points": 10.0,
                            "page_number": 1,
                            "bbox_json": {"x0": 50, "y0": 190, "x1": 540, "y1": 240},
                        }
                    ],
                }
            ],
        },
    )  # fmt: skip
    assert second.status_code == 201, second.text

    page_one = client.get("/api/v1/exam-imports", params={"limit": 1}, headers=headers).json()
    assert [i["id"] for i in page_one["items"]] == [second.json()["id"]] and page_one["has_more"] is True
    page_two = client.get(
        "/api/v1/exam-imports", params={"limit": 1, "cursor": page_one["next_cursor"]}, headers=headers
    ).json()
    assert [i["id"] for i in page_two["items"]] == [world["exam_import_id"]] and page_two["has_more"] is False
    assert page_two["items"][0]["rights_status"] == "OWNED" and page_one["items"][0]["rights_status"] != ""

    pending = client.get("/api/v1/exam-imports", params={"status": "PENDING_REVIEW"}, headers=headers).json()
    assert len(pending["items"]) == 2
    assert client.get("/api/v1/exam-imports", params={"status": "APPROVED"}, headers=headers).json()["items"] == []
    for bad in ({"status": "DONE"}, {"limit": 0}, {"cursor": "garbage"}):
        assert client.get("/api/v1/exam-imports", params=bad, headers=headers).status_code == 422, bad


def test_the_exam_import_list_is_per_tenant_and_closed_to_learners_and_visitors(client, world):
    asyncio.run(_make_tenant_with_author("other-school"))
    other = _login(client, "author@other-school.test", "other-school")
    assert client.get("/api/v1/exam-imports", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/exam-imports/{world['exam_import_id']}/structure", headers=other).status_code == 404
    refused = client.get("/api/v1/exam-imports", headers=world["learner"])
    assert refused.status_code == 403 and refused.json()["error_code"] == "FORBIDDEN"
    assert client.get("/api/v1/exam-imports").status_code == 401
    assert len(client.get("/api/v1/exam-imports", headers=world["tenant_admin"]).json()["items"]) == 1
    assert uuid.UUID(world["exam_import_id"])
    assert DEFAULT_PASSWORD and _demo_exam_pdf().startswith(b"%PDF")
