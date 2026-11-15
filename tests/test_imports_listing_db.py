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
from tests.test_question_bank_api_db import _login, _make_tenant_with_teacher


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


@pytest.fixture
def world(client):
    seeded = asyncio.run(seed_demo())
    return {
        "teacher": _login(client, "teacher@h2edu.demo"),
        "learner": _login(client, "learner@h2edu.demo"),
        "tenant_admin": _login(client, "tenant_admin@h2edu.demo"),
        "exam_import_id": seeded["entities"]["exam_import_ids"][0],
    }


def _upload(client, headers, name: str, data: bytes, mime: str, **form):
    return client.post("/api/v1/imports", headers=headers, files={"file": (name, data, mime)}, data=form)


# --- POST /api/v1/imports (multipart) -------------------------------------------------------------------------------


def test_multipart_upload_of_a_pdf_creates_a_job_a_repeat_is_a_duplicate_and_word_is_refused_today(client, world):
    headers = world["teacher"]
    pdf = _upload(client, headers, "de-1.pdf", _pdf("Cau 1. Mot"), "application/pdf", title="Đề số 1")
    assert pdf.status_code == 201, pdf.text
    body = pdf.json()
    assert body["is_duplicate"] is False and body["idempotent_replay"] is False
    assert body["job"]["job_type"] == "EXAM_IMPORT" and body["job"]["job_status"] in ("QUEUED", "RUNNING")
    assert body["document_version"]["mime_type"] == "application/pdf" and body["document_version"]["title"] == "Đề số 1"
    assert body["job"]["document_version_id"] == body["document_version"]["id"]

    # Word files are refused today: the bronze asset store registers PDF, PNG, JPEG, WebP and MP3 only
    docx = _upload(
        client, headers, "de-2.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert docx.status_code == 422 and "unsupported mime type" in docx.json()["detail"], docx.text

    again = _upload(client, headers, "de-1-lai.pdf", _pdf("Cau 1. Mot"), "application/pdf")
    assert again.status_code == 200 and again.json()["is_duplicate"] is True, "the same bytes are not a new document"


def test_a_zip_a_forged_type_a_wrong_mime_or_no_file_are_refused_with_422(client, world):
    headers = world["teacher"]
    jobs_before = len(client.get("/api/v1/imports", headers=headers).json()["items"])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("de.pdf", _pdf("Cau 1."))
    zipped = _upload(client, headers, "de.zip", archive.getvalue(), "application/zip")
    assert zipped.status_code == 422, "the V11.3 import API takes a single PDF, Word or image file, not an archive"
    assert zipped.json()["error_code"] == "VALIDATION_ERROR"

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
    headers = world["teacher"]
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
    job = _upload(client, world["teacher"], "de.pdf", _pdf("Cau 1."), "application/pdf").json()["job"]["id"]
    for method, url in (("get", f"/api/v1/imports/{job}"), ("post", f"/api/v1/imports/{job}/retry")):
        assert getattr(client, method)(url, headers=learner).status_code == 403, url
    assert client.post(f"/api/v1/imports/{job}/cancel", headers=learner).status_code == 403
    assert client.post("/api/v1/imports", files={"file": ("de.pdf", b"%PDF", "application/pdf")}).status_code == 401


# --- GET /api/v1/imports ----------------------------------------------------------------------------------------------


def test_the_import_list_is_newest_first_filterable_and_paginated_by_cursor(client, world):
    headers = world["teacher"]
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
        "last_error_code", "title", "mime_type", "page_count", "rights_status", "created_at", "updated_at",
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
    seeded_jobs = [i["id"] for i in client.get("/api/v1/imports", headers=world["teacher"]).json()["items"]]
    mine = _upload(client, world["teacher"], "de.pdf", _pdf("Cau 1."), "application/pdf").json()["job"]["id"]
    asyncio.run(_make_tenant_with_teacher("other-school"))
    other = _login(client, "teacher@other-school.test", "other-school")
    assert client.get("/api/v1/imports", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/imports/{mine}", headers=other).status_code == 404
    theirs = _upload(client, other, "kia.pdf", _pdf("Cau khac."), "application/pdf").json()["job"]["id"]
    assert [i["id"] for i in client.get("/api/v1/imports", headers=other).json()["items"]] == [theirs]
    ours = [i["id"] for i in client.get("/api/v1/imports", headers=world["teacher"]).json()["items"]]
    assert ours == [mine, *seeded_jobs] and theirs not in ours
    assert client.get("/api/v1/imports", headers=world["learner"]).status_code == 403
    assert client.get("/api/v1/imports").status_code == 401
    assert client.get("/api/v1/imports", headers=world["tenant_admin"]).status_code == 200
    # a cursor minted for one tenant does not work for another
    for n in range(2):
        _upload(client, world["teacher"], f"x{n}.pdf", _pdf(f"Cau x{n}."), "application/pdf")
    cursor = client.get("/api/v1/imports", params={"limit": 1}, headers=world["teacher"]).json()["next_cursor"]
    assert client.get("/api/v1/imports", params={"cursor": cursor}, headers=other).status_code == 422


# --- GET /api/v1/exam-imports -----------------------------------------------------------------------------------------


def test_the_exam_import_list_opens_the_review_of_the_seeded_session(client, world):
    headers = world["teacher"]
    body = client.get("/api/v1/exam-imports", headers=headers).json()
    assert len(body["items"]) == 1 and body["has_more"] is False and body["next_cursor"] is None
    item = body["items"][0]
    assert item["id"] == world["exam_import_id"], "the id is the import_id of /studio/imports/{id}/review"
    assert set(item) == {
        "id", "extraction_run_id", "document_version_id", "title", "mime_type", "page_count", "status",
        "rights_status", "section_count", "question_count", "created_at",
    }  # fmt: skip
    assert item["status"] == "PENDING_REVIEW" and item["rights_status"] == "OWNED"
    assert (item["section_count"], item["question_count"]) == (1, 3)
    assert item["mime_type"] == "application/pdf" and item["page_count"] == 1 and item["document_version_id"]

    opened = client.get(f"/api/v1/exam-imports/{item['id']}/structure", headers=headers)
    assert opened.status_code == 200, opened.text
    assert opened.json()["id"] == item["id"] and opened.json()["document_version_id"] == item["document_version_id"]
    assert opened.json()["validation"]["question_count"] == 3


def test_the_exam_import_list_filters_by_status_and_pages_by_cursor(client, world):
    headers = world["teacher"]
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
    asyncio.run(_make_tenant_with_teacher("other-school"))
    other = _login(client, "teacher@other-school.test", "other-school")
    assert client.get("/api/v1/exam-imports", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/exam-imports/{world['exam_import_id']}/structure", headers=other).status_code == 404
    refused = client.get("/api/v1/exam-imports", headers=world["learner"])
    assert refused.status_code == 403 and refused.json()["error_code"] == "FORBIDDEN"
    assert client.get("/api/v1/exam-imports").status_code == 401
    assert len(client.get("/api/v1/exam-imports", headers=world["tenant_admin"]).json()["items"]) == 1
    assert uuid.UUID(world["exam_import_id"])
    assert DEFAULT_PASSWORD and _demo_exam_pdf().startswith(b"%PDF")
