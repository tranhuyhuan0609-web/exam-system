"""Questions waiting for review that a machine can finish: solution pictures dropped, stem pictures back to the AI,
missing options / answers completed by a free text model (short answer, multiple choice with distractors, essay)."""

import json

from sqlalchemy import select

from app.models import CrawledQuestion, CrawlerSite, CrawlItemStatus, Difficulty, QuestionType
from app.pipeline.use_cases.rescue import ANSWER_ISSUES, IMAGES_LEFT, SOLUTION_FIGURE, rescue_waiting


class FakeModel:
    name, model = "groq", "gpt-oss"

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    async def available(self):
        return True

    async def ask_json(self, system, user, items=1):
        sent = json.loads(user)
        self.asked.append([q["ref"] for q in sent])
        return json.dumps({"questions": [{**self.answers[q["content"]], "ref": q["ref"]} for q in sent]})


def _item(n: int, content: str, issues: list[str], **fields) -> CrawledQuestion:
    return CrawledQuestion(
        site_id="demo",
        external_id=f"q{n}",
        doc_id="d",
        source_url="https://example.com/x",
        question_type=fields.pop("question_type", QuestionType.MULTIPLE_CHOICE),
        content=content,
        choices=fields.pop("choices", []),
        explanation=fields.pop("explanation", None),
        difficulty=Difficulty.UNDERSTANDING,
        source_hash=f"h{n}",
        content_hash=f"c{n}",
        status=CrawlItemStatus.NEEDS_REVIEW,
        issues=issues,
        **fields,
    )


def test_waiting_questions_are_finished_by_machine(db):
    async def seed(s):
        s.add(CrawlerSite(site_id="demo", name="Demo", config={}))
        await s.flush()
        s.add_all(
            [
                _item(
                    1,
                    "Phương trình quỹ đạo là",
                    [IMAGES_LEFT],
                    explanation="Ta có $y=0,05x^2$.\n[[img:0]]\nVậy chọn C, vì thay số vào.",
                ),
                _item(2, "Hình [[img:0]] cho biết gì?", [IMAGES_LEFT], explanation="Xem hình"),
                _item(
                    3,
                    "Một vật rơi 2 s, lấy g = 10. Quãng đường?",
                    ["Không tách được các lựa chọn A–D.", "Không tìm thấy đáp án."],
                ),
                _item(4, "Thủ đô của Việt Nam là thành phố nào?", ["Không tìm thấy phần đáp án."]),
                _item(5, "Chứng minh rằng $\\sqrt2$ là số vô tỉ.", ["Không tìm thấy phần đáp án."]),
                _item(6, "Câu AI trả lời hỏng", ["Không tìm thấy đáp án."]),
            ]
        )

    db(seed)
    model = FakeModel(
        {
            "Một vật rơi 2 s, lấy g = 10. Quãng đường?": {
                "type": "SHORT_ANSWER",
                "short_answer": "20",
                "explanation": "$s=\\dfrac{1}{2}gt^2=20$ m.",
            },
            "Thủ đô của Việt Nam là thành phố nào?": {
                "type": "MULTIPLE_CHOICE",
                "choices": ["Hà Nội", "Huế", "Đà Nẵng", "Cần Thơ"],
                "correct": 0,
                "explanation": "Thủ đô là Hà Nội.",
            },
            "Chứng minh rằng $\\sqrt2$ là số vô tỉ.": {
                "type": "ESSAY",
                "explanation": "Giả sử $\\sqrt2=\\dfrac{p}{q}$ tối giản…",
            },
            "Câu AI trả lời hỏng": {"type": "MULTIPLE_CHOICE", "choices": ["A"], "correct": 3},
        }
    )
    stats = db(lambda s: rescue_waiting(s, [model]))
    assert stats == {"pictures_dropped": 1, "back_to_ai": 1, "completed": 3, "unchanged": 1}
    rows = {r.external_id: r for r in db(lambda s: s.scalars(select(CrawledQuestion)))}

    assert rows["q1"].status == CrawlItemStatus.READY and "[[img" not in rows["q1"].explanation
    assert rows["q1"].issues == [SOLUTION_FIGURE]  # a note, not a reason to wait
    assert rows["q2"].status == CrawlItemStatus.PENDING_AI  # the picture is the question: a vision model again
    short = rows["q3"]
    assert (short.status, short.question_type, short.accepted_answers) == (
        CrawlItemStatus.READY,
        QuestionType.SHORT_ANSWER,
        ["20"],
    )
    assert short.explanation_by_ai and not ANSWER_ISSUES & set(short.issues)
    mc = rows["q4"]
    assert mc.question_type == QuestionType.MULTIPLE_CHOICE and [c["is_correct"] for c in mc.choices] == [
        True,
        False,
        False,
        False,
    ]
    assert rows["q5"].question_type == QuestionType.ESSAY and rows["q5"].status == CrawlItemStatus.READY
    assert rows["q6"].status == CrawlItemStatus.NEEDS_REVIEW and rows["q6"].issues == ["Không tìm thấy đáp án."]
    ids = [r.id for r in (rows["q3"], rows["q4"], rows["q5"], rows["q6"])]
    assert model.asked == [ids[:3], ids[3:]]  # three questions a call (Groq's free tier limits tokens a minute)
