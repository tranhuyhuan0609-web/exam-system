"""hoc247.net question pages (…-qid<id>.html), reached from exam pages (…-ktdt<id>.html).

The question sits in #cauhoi > li.lch: "Câu hỏi:" then the stem, options in ul#dstl<id> (A–D, LaTeX in \\( \\)),
and a hidden answer block div#answer_<id> with "Đáp án đúng: C" and div.loigiai. Related questions further down
the page are other questions and are ignored. Some answer keys on this site are wrong: the site config turns on
verify_answers so the AI re-checks them.
"""

import re

from app.pipeline.domain.models import ParsedPage, ParsedQuestion
from app.pipeline.parsers.base import Images, document, letter_choices, page_title, register, text_of

_QID = re.compile(r"-qid(\d+)\.html")
_ANSWER = re.compile(r"Đáp án đúng\s*:?\s*([A-H])\b")


def _clean_explanation(text: str) -> str:
    lines = [line for line in text.split("\n") if not re.fullmatch(r"\s*Đáp án\s*:?\s*[A-H]\.?\s*", line)]
    return "\n".join(lines).strip()


@register("hoc247_question")
def parse(html: str, url: str) -> ParsedPage:
    doc = document(html)
    match = _QID.search(url)
    if not match:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Không phải trang câu hỏi."])
    qid = match.group(1)
    options = doc.xpath(f'//ul[@id="dstl{qid}"]/li')
    block = options[0].getparent().getparent() if options else None
    if block is None:
        return ParsedPage(title=page_title(doc), questions=[], issues=["Không tìm thấy câu hỏi trên trang."])
    images = Images(url)
    stem_nodes = [
        el
        for el in block
        if isinstance(el.tag, str)
        and el.tag == "p"
        and not el.xpath('ancestor-or-self::*[contains(@class, "_traloi")]')
    ]
    content = re.sub(r"^\s*Câu hỏi\s*:?\s*", "", images.convert(stem_nodes)).strip()
    texts = []
    for li in options:
        spans = li.xpath("./span")
        texts.append(images.convert([spans[-1]]) if spans else text_of(li))
    answer_box = doc.xpath(f'//div[@id="answer_{qid}"]')
    correct, explanation = None, ""
    if answer_box:
        for node in answer_box[0].xpath(".//strong|.//p"):  # "Đáp án đúng: C" sits in its own tag
            found = _ANSWER.fullmatch(text_of(node))
            if found:
                correct = found.group(1)
                break
        solution = answer_box[0].xpath('.//div[contains(@class, "loigiai")]')
        explanation = _clean_explanation(images.convert(solution)) if solution else ""
    q = ParsedQuestion(external_id=qid, question_type="MULTIPLE_CHOICE", content=content, explanation=explanation)
    q.choices = letter_choices(texts, correct)
    if len(texts) < 2:
        q.issues.append("Không tách được các lựa chọn A–D.")
    if correct is None:
        q.issues.append("Không tìm thấy đáp án.")
    if not content:
        q.issues.append("Không đọc được đề bài.")
    q.images = images.urls
    return ParsedPage(title=page_title(doc), questions=[q])
