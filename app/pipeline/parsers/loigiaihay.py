"""loigiaihay.com quiz articles ("Trắc nghiệm ... có đáp án").

Layout: #sub-question-1 "Đề bài" and #sub-question-2 "Lời giải và đáp án" hold the same .question blocks; the
second also has .loigiai with "Đáp án : D", "Phương pháp giải" and "Lời giải chi tiết". Each question links to
/bai-tap-<id>.html, which gives a stable external id.
"""

import re

from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import Images, document, letter_choices, page_title, register, text_of

_ANSWER = re.compile(r"Đáp án\s*:?\s*([A-H])\b")
_ID = re.compile(r"/bai-tap-(\d+)\.html")


def _without(el, xpath: str):
    for junk in el.xpath(xpath):
        junk.getparent().remove(junk)
    return el


@register("loigiaihay_quiz")
def parse(html: str, url: str) -> ParsedPage:
    doc = document(html)
    blocks = doc.xpath('//div[@id="sub-question-2"]//div[contains(concat(" ", @class, " "), " question ")]')
    issues = []
    if not blocks:  # some pages only have the statement part
        blocks = doc.xpath('//div[@id="sub-question-1"]//div[contains(concat(" ", @class, " "), " question ")]')
        issues.append("Trang không có phần “Lời giải và đáp án”.")
    questions = []
    for number, block in enumerate(blocks, start=1):
        link = block.xpath('.//strong[contains(@class, "question-index-number")]//a/@href')
        match = _ID.search(link[0]) if link else None
        external_id = match.group(1) if match else f"{url}#{number}"
        images = Images(url)
        inner = block.xpath('.//div[contains(@class, "question-content-inner")]')
        content = (
            images.convert([_without(inner[0], './/strong[contains(@class, "question-index-number")]')])
            if inner
            else ""
        )
        content = re.sub(r"^[\s.:]+", "", content)  # leftover of "Câu 1 :"
        options = block.xpath('.//ul[contains(@class, "dapan")]/li')
        texts = []
        for li in options:
            body = li.xpath("./div")
            texts.append(images.convert(body if body else [_without(li, './/span[contains(@class, "span-answer")]')]))
        q = ParsedQuestion(external_id=external_id, question_type="MULTIPLE_CHOICE", content=content)
        solution = block.xpath('.//div[contains(@class, "loigiai")]')
        correct = None
        if solution:
            found = _ANSWER.search(text_of(solution[0]))
            correct = found.group(1) if found else None
            parts = []
            for cls, label in (("solution-suggesstion", "Phương pháp giải"), ("solution-detail", "Lời giải chi tiết")):
                box = solution[0].xpath(f'.//div[contains(@class, "{cls}")]/div[not(contains(@class, "title"))]')
                text = images.convert(box) if box else ""
                if text and not re.fullmatch(r"Đáp án\s*:?\s*[A-H]\.?", text.strip()):  # "Đáp án: D" alone adds nothing
                    parts.append((label, text))
            q.explanation = parts[0][1] if len(parts) == 1 else "\n".join(f"{label}: {text}" for label, text in parts)
        if len(texts) >= 2:
            q.choices = letter_choices(texts, correct)
        else:
            q.issues.append("Không nhận ra dạng câu (không có 4 lựa chọn A–D).")
        if correct is None:
            q.issues.append("Không tìm thấy đáp án.")
        q.images = images.urls
        questions.append(q)
    return ParsedPage(title=page_title(doc), questions=questions, issues=issues)
