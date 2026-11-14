"""Gemini adapters for Silver: formula/solution enrichment (Flash, structured output) and embeddings."""

import json
import math

from pydantic import BaseModel, Field

from app.ai.provider import GeminiProvider
from app.config import get_settings
from app.domain.latex import repair_latex
from app.models import EMBEDDING_DIM
from app.pipeline.ports import EnrichItem, EnrichResult

ENRICH_PROMPT = """Bạn là biên tập viên ngân hàng đề Vật lí/Toán THPT. Dưới đây là các câu hỏi thu thập từ web (JSON),
kèm ảnh công thức/hình được đánh dấu [[img:N]] trong văn bản (ảnh gửi kèm theo nhãn "CÂU <ref> ẢNH <N>").

Với MỖI câu:
1. Chuẩn hoá mọi công thức, kí hiệu, đơn vị, chỉ số trên/dưới về LaTeX đặt trong $...$ (ví dụ "x = 4cos(3πt + 0,5π)"
   -> "$x = 4\\cos(3\\pi t + 0{,}5\\pi)$", "v$_{max}$" -> "$v_{\\max}$").
   Giữ nguyên câu chữ tiếng Việt, KHÔNG thêm bớt ý.
2. Ảnh [[img:N]] chỉ chứa công thức: thay bằng LaTeX tương ứng.
   Ảnh là hình vẽ/đồ thị: GIỮ NGUYÊN chỗ đánh dấu [[img:N]].
3. Nếu "explanation" rỗng: viết lời giải ngắn gọn từng bước, khớp với "answer" (đáp án đúng do nguồn cung cấp), và đặt
   explanation_written = true. Nếu đã có lời giải: chỉ chuẩn hoá công thức, explanation_written = false.
4. Ước lượng mức độ: RECOGNITION (nhận biết), UNDERSTANDING (thông hiểu), APPLICATION (vận dụng),
   ADVANCED_APPLICATION (vận dụng cao).
5. Tự giải và kiểm tra "answer": nếu chắc chắn đáp án nguồn đưa ra là SAI, đặt answer_ok = false và nêu đáp án đúng
   trong explanation; ngược lại answer_ok = true. Không sửa "answer".
Trả về đúng số câu, cùng "ref", "choices" cùng thứ tự và cùng số lượng như đầu vào."""


class _Enriched(BaseModel):
    ref: int
    content: str
    choices: list[str] = Field(default_factory=list)
    explanation: str
    explanation_written: bool
    difficulty: str
    answer_ok: bool = True  # false when the given answer is wrong


class _EnrichResult(BaseModel):
    questions: list[_Enriched]


class GeminiEnricher:
    def __init__(self, provider: GeminiProvider | None = None):
        settings = get_settings()
        self._provider = provider or GeminiProvider(settings.gemini_api_key, settings.gemini_model_list)

    async def enrich(self, items: list[EnrichItem]) -> list[EnrichResult]:
        from google.genai import types

        payload = [
            {
                "ref": i.ref,
                "type": i.question_type,
                "content": i.content,
                "choices": i.choices,
                "answer": i.answer,
                "explanation": i.explanation,
            }
            for i in items
        ]
        contents: list = [ENRICH_PROMPT, json.dumps(payload, ensure_ascii=False)]
        for item in items:
            for number, data, mime in item.images:
                contents += [f"CÂU {item.ref} ẢNH {number}", types.Part.from_bytes(data=data, mime_type=mime)]
        result = await self._provider._generate(contents, _EnrichResult)
        by_ref = {q.ref: q for q in result.questions}
        out = []
        for item in items:
            q = by_ref.get(item.ref)
            if q is None or len(q.choices) != len(item.choices):
                continue  # incomplete answer for this item: it stays for a retry
            out.append(
                EnrichResult(
                    ref=item.ref,
                    content=repair_latex(q.content),
                    choices=[repair_latex(c) for c in q.choices],
                    explanation=repair_latex(q.explanation),
                    explanation_written=q.explanation_written and not item.explanation,
                    difficulty=q.difficulty,
                    answer_ok=q.answer_ok,
                )
            )
        return out


class GeminiEmbedder:
    """Computes embeddings using a rotating pool of Gemini API keys."""

    def __init__(self, api_keys: list[str] | str | None = None, model: str | None = None):
        from app.ai.key_rotator import GeminiKeyPool

        settings = get_settings()
        keys = api_keys or settings.gemini_api_key_list
        self._pool = GeminiKeyPool(keys)
        self._model = model or settings.gemini_embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from google.genai import errors, types

        out: list[list[float]] = []
        for start in range(0, len(texts), 50):
            chunk = texts[start : start + 50]
            success = False
            last_err = None

            available_keys = await self._pool.get_all_available_keys() or self._pool.keys
            for current_key in available_keys:
                client = self._pool.get_client(current_key)
                try:
                    response = await client.aio.models.embed_content(
                        model=self._model,
                        contents=chunk,
                        config=types.EmbedContentConfig(
                            output_dimensionality=EMBEDDING_DIM, task_type="SEMANTIC_SIMILARITY"
                        ),
                    )
                    for emb in response.embeddings:
                        norm = math.sqrt(sum(v * v for v in emb.values)) or 1.0
                        out.append([v / norm for v in emb.values])  # truncated outputs are not unit length
                    await self._pool.mark_recovered(current_key)
                    success = True
                    break
                except errors.APIError as exc:
                    last_err = exc
                    if exc.code == 429:
                        await self._pool.mark_exhausted(current_key, cooldown_minutes=60, reason=str(exc.message)[:100])
                        continue
                    raise

            if not success:
                raise last_err or RuntimeError("Không có Gemini API key nào khả dụng để tạo embedding.")
        return out
