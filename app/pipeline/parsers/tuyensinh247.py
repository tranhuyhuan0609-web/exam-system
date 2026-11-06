"""tuyensinh247.com question pages (bai-tap-<id>.html), reached from revision-topic pages (luyen-chuyen-de-…-tq<n>).

    <div class="extra-question-number">Câu hỏi số 89475:</div> <span class="level-question">Nhận biết</span>
    <div class="question-desc"> stem, formulas as images whose alt text is LaTeX </div>
    <div class="mab5 right-anwser"><label class="checkcontainer"><strong>A.</strong> 3,26MeV …</label></div> …
    <div class="answer-result"><span>Đáp án đúng là: A</span></div>
    <div id="qg_89475"> "Phương pháp giải" … "Giải chi tiết" … </div>

Pages that are a reading passage with sub-questions (literature, English) are skipped.
"""

import re

from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import Images, document, letter_choices, page_title, register, text_of

_ID = re.compile(r"/bai-tap-(\d+)\.html")
_ANSWER = re.compile(r"Đáp án đúng là\s*:?\s*([A-H])\b")
LEVELS = {
    "nhận biết": "RECOGNITION",
    "thông hiểu": "UNDERSTANDING",
    "vận dụng": "APPLICATION",
    "vận dụng cao": "ADVANCED_APPLICATION",
}


@register("tuyensinh247_question")
def parse(html: str, url: str) -> ParsedPage:
    doc = document(html)
    match = _ID.search(url)
    if not match:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Không phải trang câu hỏi."])
    qid = match.group(1)
    if len(doc.xpath('//div[contains(@class, "extra-question-item")]')) > 1:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Bài đọc nhiều câu hỏi con: bỏ qua."])
    descs = doc.xpath('//div[contains(@class, "question-desc")]')
    if not descs:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Không tìm thấy đề bài."])
    desc = descs[0]
    body = desc.getparent()
    images = Images(url)
    content = images.convert([desc])
    texts = []
    correct = None
    for i, box in enumerate(body.xpath('./div[contains(@class, "mab5")]')):
        label = box.xpath('.//label[contains(@class, "checkcontainer")]')
        node = label[0] if label else box
        for junk in node.xpath('.//input|.//span[contains(@class, "radiobtn")]'):
            junk.getparent().remove(junk)
        text = re.sub(r"^\s*[A-H]\s*[.)]\s*", "", images.convert([node])).strip()
        texts.append(text)
        if "right-anwser" in (box.get("class") or ""):
            correct = correct or "ABCDEFGH"[i]
    result = body.xpath('.//div[contains(@class, "answer-result")]')
    if result and (found := _ANSWER.search(text_of(result[0]))):
        correct = found.group(1)
    guide = doc.xpath(f'//div[@id="qg_{qid}"]')
    parts = []
    if guide:
        for box in guide[0].xpath('.//div[contains(@class, "extra-question-answer-vip-main")]'):
            title = (
                text_of(box.xpath('.//div[contains(@class, "extra-title-info-box")]')[0])
                if box.xpath('.//div[contains(@class, "extra-title-info-box")]')
                else ""
            )
            detail = box.xpath('.//div[contains(@class, "extra-answer-vip-detail")]')
            text = images.convert(detail) if detail else ""
            if text:
                parts.append((title, text))
    explanation = parts[0][1] if len(parts) == 1 else "\n".join(f"{t}: {x}" if t else x for t, x in parts)
    level = (
        text_of(doc.xpath('//span[contains(@class, "level-question")]')[0]).lower()
        if doc.xpath('//span[contains(@class, "level-question")]')
        else ""
    )
    q = ParsedQuestion(
        external_id=qid,
        question_type="MULTIPLE_CHOICE",
        content=content,
        explanation=explanation,
        difficulty=LEVELS.get(level),
    )
    if len(texts) >= 2:
        q.choices = letter_choices(texts, correct)
    else:
        q.issues.append("Không tách được các lựa chọn A–D (câu tự luận?).")
    if correct is None:
        q.issues.append("Không tìm thấy đáp án.")
    q.images = images.urls
    return ParsedPage(title=page_title(doc), questions=[q])
