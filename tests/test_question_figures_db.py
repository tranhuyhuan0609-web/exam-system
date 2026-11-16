"""Figures: cut from the PDF, stored with the question, shown to the model and to the student.

A teacher's PDF question that has a figure ends up as: `stem_json.media = [{type: image, asset_id, alt}]`, a
`question_assets` row and a tenant-private FIGURE_CROP asset. The student's paper carries the media (it is on the
sanitizer's allowlist) and the picture is read through the signed URL of the asset. The model is sent a picture of the
question (and its figures) next to the text, and what it reads from the picture replaces a garbled text layer without
changing the identity of the question.
"""

from __future__ import annotations

import time
import uuid

from app.modules.parsers.public import extract_pdf_exam_structure
from app.modules.questions.public import pictures_for
from tests.pdf_fixtures import A4, PdfSketch
from tests.test_teacher_pdf_to_exam_room_db import (  # noqa: F401  (world is a fixture)
    _q,
    _upload,
    world,
)

PNG = b"\x89PNG\r\n\x1a\n"


def _pdf_with_a_figure() -> bytes:
    sketch = PdfSketch()
    page = sketch.new_page(*A4)
    page.text(48, 36, "DE KIEM TRA", size=12)
    page.text(48, 82, "Cau 1. Dao ham cua ham so y = x^3 - 3x la:", size=11)
    # a small circuit drawn to the right of the stem: a frame, a circle and two wires
    page.rect(380, 70, 520, 170, color=(0, 0, 0))
    page.circle(450, 120, 28, color=(0, 0, 0))
    page.line((380, 120), (422, 120))
    page.line((478, 120), (520, 120))
    for n, option in enumerate(("A. 3x^2 - 3", "B. 3x^2 + 3", "C. x^2 - 3", "D. 3x")):
        page.text(48, 110 + 18 * n, option, size=11)
    page.text(48, 200, "Cau 2. Gia tri lon nhat cua f(x) = -x^2 + 4 tren R la:", size=11)
    for n, option in enumerate(("A. 2", "B. 3", "C. 4", "D. 5")):
        page.text(48, 220 + 18 * n, option, size=11)
    page.text(48, 300, "[Dap an: C]", size=10)
    return sketch.tobytes()


def test_the_figure_of_a_question_is_cut_out_of_the_pdf_and_the_question_is_pictured():
    pdf = _pdf_with_a_figure()
    drafts = [q for s in extract_pdf_exam_structure(pdf).sections for q in s.questions]
    assert [bool(d.figure_bboxes) for d in drafts] == [True, False], "only question 1 has a figure"
    (image1, figures1), (image2, figures2) = pictures_for(pdf, drafts)
    assert image1 and image1.startswith(PNG) and image2 and image2.startswith(PNG), "every question is pictured"
    assert len(figures1) >= 1 and all(f.startswith(PNG) for f in figures1)
    assert figures2 == (), "no figure, nothing is cut"
    assert pictures_for(b"not a pdf", drafts) == [(None, ()), (None, ())]


def test_a_teachers_pdf_stores_the_figure_with_the_question_and_the_student_gets_the_picture(client, world):  # noqa: F811
    world["state"]["solver"], gemini = _vision_solver()
    made = _upload(client, world, _pdf_with_a_figure(), class_id=world["class_id"], title="Đề có hình")
    assert made.status_code == 201, made.text
    paper = made.json()
    assert paper["question_count"] == 2

    rows = _q(
        "SELECT q.stable_code, v.stem_json, "
        "       (SELECT count(*) FROM question_assets qa WHERE qa.question_version_id = v.id) AS linked, "
        "       v.content_hash "
        "FROM questions q JOIN question_versions v ON v.id = q.current_version_id "
        "WHERE q.tenant_id = :t AND q.stable_code LIKE 'PDF-%' ORDER BY q.stable_code",
        t=world["tenant_id"],
    )
    with_figure, without = rows
    media = with_figure["stem_json"]["media"]
    assert len(media) >= 1 and media[0]["type"] == "image" and media[0]["alt"] == "Hình 1"
    assert with_figure["linked"] == len(media) and "media" not in without["stem_json"] and without["linked"] == 0
    asset = _q("SELECT asset_role, mime_type, size_bytes FROM assets WHERE id = :a", a=uuid.UUID(media[0]["asset_id"]))[
        0
    ]
    assert (asset["asset_role"], asset["mime_type"]) == ("FIGURE_CROP", "image/png") and asset["size_bytes"] > 100

    # the model was shown the question as a picture (and the figure) next to its text
    assert gemini.image_counts and min(gemini.image_counts) >= 1 and max(gemini.image_counts) >= 2
    # the student's paper carries the media, and the picture comes from the signed URL
    attempt = client.post(
        "/api/v1/exam-sessions/start",
        headers=world["learner"],
        json={"assessment_id": paper["assessment_id"], "assignment_id": paper["assignment_id"]},
    ).json()["attempt_id"]
    sheet = client.get(f"/api/v1/attempts/{attempt}/paper", headers=world["learner"]).json()
    figure_questions = [q for q in sheet["questions"] if q["stem_json"].get("media")]
    assert len(figure_questions) == 1
    sent = figure_questions[0]["stem_json"]["media"][0]
    assert set(sent) == {"type", "asset_id", "alt"} and sent["asset_id"] == media[0]["asset_id"]

    signed = client.get(f"/api/v1/assets/{sent['asset_id']}/signed-url", headers=world["learner"])
    assert signed.status_code == 200, signed.text
    url = signed.json()["signed_url"]
    assert url.startswith(f"/api/v1/assets/{sent['asset_id']}/download?")
    picture = client.get(url)  # no token: the signature is the credential
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    assert picture.content.startswith(PNG) and picture.headers["x-content-type-options"] == "nosniff"

    # a forged, moved or expired signature is the same 404
    assert client.get(url.replace("sig=", "sig=0")).status_code == 404
    other_asset = _q(
        "SELECT id FROM assets WHERE id <> :a AND tenant_id = :t LIMIT 1",
        a=uuid.UUID(sent["asset_id"]),
        t=world["tenant_id"],
    )
    assert client.get(url.replace(sent["asset_id"], str(uuid.uuid4()))).status_code == 404
    if other_asset:
        assert client.get(url.replace(sent["asset_id"], str(other_asset[0]["id"]))).status_code == 404
    expired = url.replace("exp=", "exp=1").replace(f"&exp=1{int(time.time())}", "&exp=1")
    assert client.get(expired).status_code == 404


def _vision_solver():
    from tests.ai_fakes import fake_solver

    return fake_solver(
        {
            "Dao ham cua ham so": {
                "answer": "A",
                "solution_steps": "y' = 3x^2 - 3",
                "stem_latex": "Đạo hàm của hàm số $y = x^3 - 3x$ là",
                "options_latex": ["$3x^2 - 3$", "$3x^2 + 3$", "$x^2 - 3$", "$3x$"],
            },
        }
    )


def test_what_the_model_reads_from_the_picture_is_stored_and_the_same_file_is_still_the_same_question(client, world):  # noqa: F811
    world["state"]["solver"], gemini = _vision_solver()
    first = _upload(client, world, _pdf_with_a_figure(), title="Lần 1")
    assert first.status_code == 201, first.text
    stored = _q(
        "SELECT v.stem_json->>'text' AS stem, v.options_json, a.rationale_json "
        "FROM questions q JOIN question_versions v ON v.id = q.current_version_id "
        "JOIN question_version_answers a ON a.question_version_id = v.id "
        "WHERE q.tenant_id = :t AND q.stable_code LIKE 'PDF-%' ORDER BY q.stable_code",
        t=world["tenant_id"],
    )
    assert stored[0]["stem"] == "Đạo hàm của hàm số $y = x^3 - 3x$ là", "the transcription from the picture is stored"
    assert [o["text"] for o in stored[0]["options_json"]] == ["$3x^2 - 3$", "$3x^2 + 3$", "$x^2 - 3$", "$3x$"]
    assert stored[0]["rationale_json"]["read_from_image"] is True
    assert stored[0]["rationale_json"]["source_text"].startswith("Dao ham cua ham so")
    assert stored[1]["rationale_json"]["read_from_image"] is False, "no stem_latex came back for question 2"

    calls_after_first = len(gemini.calls)
    second = _upload(client, world, _pdf_with_a_figure(), title="Lần 2")
    assert second.status_code == 201, second.text
    count = _q(
        "SELECT count(*) AS n FROM questions WHERE tenant_id = :t AND stable_code LIKE 'PDF-%'", t=world["tenant_id"]
    )
    assert count[0]["n"] == 2, "the same PDF read again adds no question: the identity is the source text"
    assert len(gemini.calls) == calls_after_first, "a paper whose questions are known costs no AI call"
    assert second.json()["question_count"] == 2
