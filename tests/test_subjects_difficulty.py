"""Crawled pages file under the subject they name, and AI grades the levels sources label "Vận dụng (cao)"."""

import json

from sqlalchemy import select

from app.application.categories import create_category
from app.models import CrawledQuestion, CrawlerSite, CrawlItemStatus, Difficulty, Question, QuestionType
from app.pipeline.domain.subject_of import corrected_context, subject_of
from app.pipeline.use_cases.difficulty import check_difficulty


def test_the_page_names_its_subject_grade_and_series():
    url = "https://loigiaihay.com/trac-nghiem-toan-8-bai-4-chuong-8-chan-troi-sang-tao-co-dap-an-a153641.html"
    assert subject_of(url) == ("Toán 8", "Chân trời sáng tạo")
    assert subject_of("https://loigiaihay.com/x-a1.html", "Trắc nghiệm Bài 35. Đồng và hợp chất Hóa 12") == (
        "Hóa học 12",
        "",
    )
    assert subject_of("https://loigiaihay.com/x-a2.html", "Trắc nghiệm hóa 11 bài 5 cánh diều") == (
        "Hóa học 11",
        "Cánh diều",
    )
    assert subject_of("https://loigiaihay.com/trac-nghiem-khtn-7-bai-28-a3.html") == ("Khoa học tự nhiên 7", "")
    assert subject_of("https://loigiaihay.com/on-tap-a4.html", "Ôn tập chương") is None

    seed = {"root": "Toán 1", "series": "Kết nối tri thức", "chapter": "Chương 9. Tam giác đồng dạng"}
    assert corrected_context(seed, url) == {
        "root": "Toán 8",
        "series": "Chân trời sáng tạo",
        "chapter": seed["chapter"],
    }
    same = {"root": "Hóa học 10", "series": "Kết nối tri thức", "chapter": "Chương 1"}
    assert corrected_context(same, "https://loigiaihay.com/trac-nghiem-hoa-10-bai-1-a5.html") == same


class Grader:
    name, model = "cerebras", "gpt-oss-120b"

    def __init__(self, levels):
        self.levels, self.asked = levels, []

    async def available(self):
        return True

    async def ask_json(self, system, user, items=1):
        sent = json.loads(user)
        self.asked.append([q["ref"] for q in sent])
        return json.dumps({"items": [{"ref": q["ref"], "level": self.levels[q["content"]]} for q in sent]})


def test_levels_labelled_by_the_source_are_graded_once(db):
    async def seed(s):
        s.add(CrawlerSite(site_id="demo", name="Demo", config={}))
        root = await create_category(s, "Vật lí 10", slug="vat-li-10")
        await s.flush()
        for n, (content, level) in enumerate(
            [
                ("Nâng ô tô 1,5 tấn lên 160 cm. Công tối thiểu?", Difficulty.ADVANCED_APPLICATION),
                ("Biện luận theo m số nghiệm của phương trình chứa tham số", Difficulty.ADVANCED_APPLICATION),
                ("Đơn vị của công là gì?", Difficulty.RECOGNITION),
            ]
        ):
            q = Question(
                root_id=root.id,
                category_id=root.id,
                difficulty=level,
                question_type=QuestionType.ESSAY,
                content=content,
                choices=[],
            )
            s.add(q)
            await s.flush()
            s.add(
                CrawledQuestion(
                    site_id="demo",
                    external_id=f"q{n}",
                    doc_id="d",
                    source_url="https://example.com/x",
                    question_type=QuestionType.ESSAY,
                    content=content,
                    choices=[],
                    difficulty=level,
                    source_hash=f"h{n}",
                    content_hash=f"c{n}",
                    status=CrawlItemStatus.PUBLISHED,
                    question_id=q.id,
                )
            )

    db(seed)
    grader = Grader(
        {
            "Nâng ô tô 1,5 tấn lên 160 cm. Công tối thiểu?": "UNDERSTANDING",
            "Biện luận theo m số nghiệm của phương trình chứa tham số": "ADVANCED_APPLICATION",
        }
    )
    assert db(lambda s: check_difficulty(s, [grader])) == {"checked": 2, "changed": 1, "unanswered": 0}
    levels = {q.content[:4]: q.difficulty for q in db(lambda s: s.scalars(select(Question)))}
    assert levels == {
        "Nâng": Difficulty.UNDERSTANDING,
        "Biện": Difficulty.ADVANCED_APPLICATION,
        "Đơn ": Difficulty.RECOGNITION,
    }
    assert len(grader.asked) == 1  # recognition questions are not sent
    assert db(lambda s: check_difficulty(s, [grader]))["checked"] == 0  # each question once
