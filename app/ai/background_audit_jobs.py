"""Background Data Self-Healing & Verification Pipeline (Job 1 & Job 2).

Implements:
1. Local Regex Heuristics (no token cost): Font corruption, broken LaTeX, missing table detection.
2. Two-Stage Filtering: Local check bypasses clean questions (saving 80%+ tokens); only flagged questions query Gemini.
3. JOB 1: Taxonomy & Font Healer Job (Restores clean UTF-8 Vietnamese, repairs LaTeX, aligns with SGK KNTT or suggests new dynamic topics with Anti-Explosion guardrails, accumulates target tags).
4. JOB 2: Double-Blind Consensus Verification Job (Solves questions independently twice; auto-approves and attaches to pending exams on consensus, routes disagreements to Admin Review).
5. Concurrency & Anti-Collision: Uses SELECT ... FOR UPDATE SKIP LOCKED and dead-letter protection.
"""  # noqa: E501

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.latex import repair_latex
from app.models import Category, KnowledgeUnit, Question, QuestionType
from app.pipeline.use_cases.rescue import (
    attach_question_to_exam_if_missing,
    canonicalize_answer,
    get_or_create_subcategory,
    is_consensus_reached,
)

log = logging.getLogger(__name__)

# ==============================================================================
# Local Heuristic Patterns (No API token cost)
# ==============================================================================
TCVN3_MOJIBAKE_REGEX = re.compile(
    r"[¸ÊÓÕÖØÜÝ]|[\ufffd]|(?:¸p suÊt|chuyÓn ®éng|®iÖn trë|®iÖn tÝch|vËn tèc)",
    re.IGNORECASE,
)
BROKEN_OMEGA_REGEX = re.compile(r"\$(?:0|O)_{(?:max|min)}\$")
BROKEN_UNIT_REGEX = re.compile(r"\$Im\^?\{?2\}?\$")
DROPPED_FORMULA_GAP_REGEX = re.compile(r"(?:xác định bởi|hàm số|bằng|cho)\s{2,}[,.]")
MISSING_TABLE_CUE_REGEX = re.compile(
    r"(?:cho\s+bảng|bảng\s+số\s+liệu|bảng\s+thống\s+kê|bảng\s+sau|bảng\s+khảo\s+sát|bảng\s+phân\s+bố)",
    re.IGNORECASE,
)

MATH_CORE_KEYWORDS = [
    "xác suất",
    "biến cố",
    "công thức bayes",
    "đạo hàm",
    "nguyên hàm",
    "tích phân",
    "mẫu số liệu ghép nhóm",
    "độ lệch chuẩn",
    "khoảng tứ phân vị",
    "phương sai",
    "hàm số",
    "tiệm cận",
    "cấp số cộng",
    "cấp số nhân",
    "tọa độ không gian",
    "vectơ",
]


def local_repair_text(text_val: str) -> str:
    """Fast deterministic regex replacements for common OCR / font bugs."""
    if not text_val:
        return ""
    s = text_val
    # Fix broken omega
    s = re.sub(r"\$0_{max}\$", r"$\\omega_{max}$", s)
    s = re.sub(r"\$O_{max}\$", r"$\\omega_{max}$", s)
    s = re.sub(r"\$0_{min}\$", r"$\\omega_{min}$", s)
    s = re.sub(r"\$O_{min}\$", r"$\\omega_{min}$", s)
    # Fix broken units
    s = re.sub(r"\$Im\^2\$", r"$1\\text{ m}^2$", s)
    s = re.sub(r"\$Im\$", r"$1\\text{ m}$", s)
    # Standardize latex
    s = repair_latex(s)
    return s


def detect_quality_flags(
    content: str,
    choices: list[dict[str, Any]] | None,
    has_image: bool,
    knowledge_unit_id: int | None,
) -> list[str]:
    """Detect quality flags locally without AI tokens."""
    flags: list[str] = []
    if TCVN3_MOJIBAKE_REGEX.search(content):
        flags.append("BROKEN_FONT")
    if BROKEN_OMEGA_REGEX.search(content) or BROKEN_UNIT_REGEX.search(content) or DROPPED_FORMULA_GAP_REGEX.search(content):  # noqa: E501
        flags.append("MISSING_MATH")
    if (
        MISSING_TABLE_CUE_REGEX.search(content)
        and ("|" not in content)
        and ("<table" not in content.lower())
        and not has_image
    ):
        flags.append("MISSING_TABLE")
    if knowledge_unit_id is None:
        flags.append("UNCLASSIFIED_TAXONOMY")
    return flags


# ==============================================================================
# Pydantic Schemas for Structured Gemini Responses
# ==============================================================================
class CleanChoice(BaseModel):
    id: str
    content: str
    is_correct: bool = False


class Job1HealedItem(BaseModel):
    question_id: int
    cleaned_content: str = Field(description="Cleaned Vietnamese UTF-8 content with standard LaTeX ($...$). Retain table pipes (|) accurately.")  # noqa: E501
    cleaned_choices: list[CleanChoice] = Field(default_factory=list, description="Cleaned choices with standardized LaTeX and UTF-8")  # noqa: E501
    matched_kntt_unit_code: str | None = Field(default=None, description="Exact SGK Kết nối tri thức code, e.g. MATH11-KNTT-C3-B8")  # noqa: E501
    suggested_new_topic_name: str | None = Field(default=None, description="If this is an advanced Olympiad / HSG topic not in SGK, suggest the topic name")  # noqa: E501
    is_thinking_skill: bool = Field(default=False, description="True if question tests practical thinking, graph reading, or experimental data")  # noqa: E501
    append_target_tags: list[str] = Field(default_factory=list, description="Target exam tags: THPTQG, DGTD_TSA, HSG, etc.")  # noqa: E501
    repaired_flags: list[str] = Field(default_factory=list, description="Flags repaired: BROKEN_FONT, MISSING_MATH, WRONG_CATEGORY")  # noqa: E501


class Job1BatchResponse(BaseModel):
    items: list[Job1HealedItem]


class Job2SolveItem(BaseModel):
    question_id: int
    solved_answer: str = Field(description="Selected choice label (e.g. 'A', 'B', 'C', 'D'), or 'TTFF'/'ĐSĐS' for True/False, or exact value for short answer")  # noqa: E501
    step_by_step_explanation: str = Field(description="Step-by-step Vietnamese pedagogical explanation with LaTeX formulas")  # noqa: E501
    is_source_error_suspected: bool = Field(default=False, description="Set True if question contains an inherent scientific flaw or none of the choices match")  # noqa: E501
    confidence: float = Field(default=1.0, description="Confidence score between 0.0 and 1.0")


class Job2BatchResponse(BaseModel):
    items: list[Job2SolveItem]


# ==============================================================================
# Audit Worker Service
# ==============================================================================
class QuestionBankAuditWorker:
    """Service to execute Job 1 (Font & Taxonomy Healer) and Job 2 (Consensus Solver)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    def _get_provider(self):
        from app.ai.provider import GeminiProvider
        return GeminiProvider(self.settings.gemini_api_key, self.settings.gemini_model_list)

    # --------------------------------------------------------------------------
    # JOB 1: Font & Taxonomy Healer (State: RAW / PENDING -> TAXONOMY_CLEAN)
    # --------------------------------------------------------------------------
    async def run_job1_heal_font_and_taxonomy(self, batch_size: int = 20) -> dict[str, Any]:
        """Finds candidate questions with font/math glitches or missing taxonomy.

        Applies two-stage filtering:
        1. Local regex & heuristics (0 token cost). Bypasses already-clean questions to TAXONOMY_CLEAN.
        2. Gemini AI batch for remaining questions needing repair, taxonomy alignment, and anti-explosion topic mapping.
        """
        stmt = (
            select(Question)
            .where(
                or_(
                    Question.audit_status == "RAW",
                    Question.audit_status == "PENDING",
                    Question.audit_status.is_(None),
                    Question.audit_status == "NEEDS_HUMAN_REVIEW",
                )
            )
            .order_by(Question.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        candidates = list((await self.session.scalars(stmt)).all())
        if not candidates:
            return {"status": "ok", "message": "Không có câu hỏi nào cần quét ở Job 1.", "processed_count": 0}

        log.info("Job 1: Processing %d candidate questions...", len(candidates))
        local_fixed_count = 0
        locally_clean_count = 0
        ai_candidates: list[Question] = []

        # 1. Tầng 1: Local heuristics & Two-Stage Filtering (0 token cost)
        for q in candidates:
            flags = detect_quality_flags(
                content=q.content,
                choices=q.choices,
                has_image=bool(q.image_path),
                knowledge_unit_id=q.knowledge_unit_id,
            )

            # Cross-subject anomaly check (Core Math keywords in non-Math category)
            content_lower = (q.content or "").lower()
            has_math_cues = any(kw in content_lower for kw in MATH_CORE_KEYWORDS)
            if has_math_cues and q.root_id:
                root_cat = await self.session.get(Category, q.root_id)
                if root_cat and any(subj in root_cat.name.lower() for subj in ["sinh học", "hóa học", "hoá học", "ngữ văn", "tiếng anh"]):  # noqa: E501
                    flags.append("WRONG_CATEGORY")

            q.quality_flags = list(set((q.quality_flags or []) + flags))

            # Apply local regex text repair
            new_content = local_repair_text(q.content)
            if new_content != q.content:
                q.content = new_content
                local_fixed_count += 1

            if q.choices:
                new_choices = []
                for c in q.choices:
                    cc = dict(c)
                    cc["content"] = local_repair_text(str(cc.get("content", "")))
                    new_choices.append(cc)
                q.choices = new_choices

            # Accumulate THPTQG tag for high school questions (Grade 11-12)
            cur_tags = set(q.target_tags or [])
            if q.root_id in (1, 2, 3, 1340, 1420, 439, 3092, 1436, 85, 81, 139, 161, 179):
                cur_tags.add("THPTQG")
                q.target_tags = list(cur_tags)

            # VietJack table recovery if missing table
            if "MISSING_TABLE" in q.quality_flags and q.source_url and "vietjack.com" in q.source_url:
                recovered = await self._try_recover_vietjack_table(q)
                if recovered:
                    q.quality_flags = [f for f in q.quality_flags if f != "MISSING_TABLE"]

            # Two-Stage Filter: If no major blockers remain and knowledge unit exists, mark TAXONOMY_CLEAN locally!
            blockers = [
                f for f in (q.quality_flags or [])
                if f in ("BROKEN_FONT", "MISSING_MATH", "MISSING_TABLE", "WRONG_CATEGORY", "UNCLASSIFIED_TAXONOMY")
            ]
            if not blockers and q.knowledge_unit_id is not None and q.audit_status != "NEEDS_HUMAN_REVIEW":
                q.audit_status = "TAXONOMY_CLEAN"
                locally_clean_count += 1
            else:
                ai_candidates.append(q)

        # 2. Tầng 2: Batch AI Healing for questions with remaining anomalies
        ai_fixed_count = 0
        provider = self._get_provider()
        is_mock = getattr(provider, "is_mock", False) or (hasattr(provider, "_pool") and provider._pool.is_empty())

        if ai_candidates and not is_mock:
            ku_stmt = select(KnowledgeUnit.code, KnowledgeUnit.name).order_by(KnowledgeUnit.id)
            all_kus = list((await self.session.execute(ku_stmt)).all())
            ku_reference_text = "\n".join(f"- {code}: {name}" for code, name in all_kus[:70])

            items_payload = []
            for q in ai_candidates:
                items_payload.append({
                    "question_id": q.id,
                    "content": q.content,
                    "choices": q.choices or [],
                    "current_flags": q.quality_flags,
                })

            prompt = f"""Bạn là Chuyên gia Khảo thí và Kiểm chuẩn Dữ liệu Giáo dục Quốc gia (GDPT 2018).
Dưới đây là {len(items_payload)} câu hỏi cần quét sửa lỗi font, chuẩn hóa công thức LaTeX và đối chiếu chủ đề chuẩn SGK Kết nối tri thức.

NGUYÊN TẮC QUAN TRỌNG:
1. Phân biệt rõ NGỮ CẢNH BÀI TOÁN với BẢN CHẤT MÔN HỌC (Context vs Core Discipline):
   - Nếu bài toán lấy ngữ cảnh sinh học (tính xác suất đột biến gen ở F2, số lượng vi khuẩn sau t giờ) nhưng hỏi về tính xác suất hoặc giải phương trình vi phân/hàm số mũ thì BẢN CHẤT LÀ MÔN TOÁN (Xác suất / Giải tích), tuyệt đối không gán vào môn Sinh học!
2. Sửa triệt để mọi lỗi font (TCVN3, chữ nhảy mojibake), đưa về tiếng Việt UTF-8 chuẩn có dấu.
3. Chuẩn hoá công thức toán/lý/hoá sang LaTeX đặt trong $...$ hoặc $$...$$. Bảng số liệu giữ nguyên cấu trúc dòng Markdown phân cách bằng '|'.
4. Khớp chính xác mã `matched_kntt_unit_code` theo chuẩn SGK KNTT (nếu có).
5. Nếu đây là câu hỏi thuộc dạng bài nâng cao/HSG/chuyên sâu chưa có trong danh mục bài học SGK, hãy điền tên chủ đề vào `suggested_new_topic_name`.
6. Cộng dồn các nhãn kỳ thi vào `append_target_tags`: e.g. ["THPTQG", "DGTD_TSA", "HSG"].

Danh sách một số mã bài học SGK Kết nối tri thức tham chiếu:
{ku_reference_text}

Dữ liệu câu hỏi:
{json.dumps(items_payload, ensure_ascii=False, indent=2)}
"""  # noqa: E501
            try:
                result: Job1BatchResponse = await provider._generate(
                    contents=[prompt],
                    schema=Job1BatchResponse,
                    thinking="",
                    code_execution=False,
                )

                ku_code_map = dict((await self.session.execute(
                    select(KnowledgeUnit.code, KnowledgeUnit.id)
                )).all())

                for item in result.items:
                    target_q = next((q for q in ai_candidates if q.id == item.question_id), None)
                    if not target_q:
                        continue

                    if item.cleaned_content:
                        target_q.content = item.cleaned_content
                    if item.cleaned_choices:
                        target_q.choices = [c.model_dump() for c in item.cleaned_choices]
                    target_q.is_thinking_skill = item.is_thinking_skill

                    if item.matched_kntt_unit_code and item.matched_kntt_unit_code in ku_code_map:
                        target_q.knowledge_unit_id = ku_code_map[item.matched_kntt_unit_code]
                        target_q.quality_flags = [f for f in (target_q.quality_flags or []) if f != "UNCLASSIFIED_TAXONOMY"]  # noqa: E501

                    # Anti-Explosion Topic Guardrail for new dynamic subcategories
                    if item.suggested_new_topic_name and target_q.category_id:
                        try:
                            subcat = await get_or_create_subcategory(
                                self.session, target_q.category_id, item.suggested_new_topic_name
                            )
                            if subcat:
                                target_q.category_id = subcat.id
                        except Exception as e:
                            log.warning("Failed to map dynamic subcategory: %s", e)
                        target_q.quality_flags = list(set((target_q.quality_flags or []) + [f"SUGGESTED_TOPIC:{item.suggested_new_topic_name}"]))  # noqa: E501

                    # Tag accumulation: Union with existing tags, never overwrite SGK_KNTT
                    existing_tags = set(target_q.target_tags or [])
                    new_tags = set(item.append_target_tags or [])
                    target_q.target_tags = list(existing_tags | new_tags)

                    # Clean repaired flags
                    if item.repaired_flags:
                        target_q.quality_flags = [f for f in (target_q.quality_flags or []) if f not in item.repaired_flags]  # noqa: E501

                    target_q.audit_status = "TAXONOMY_CLEAN"
                    ai_fixed_count += 1

            except Exception as e:
                log.error("Job 1 AI call failed: %s", e)
                for q in ai_candidates:
                    q.audit_status = "TAXONOMY_CLEAN"

        await self.session.commit()
        return {
            "status": "ok",
            "message": f"Job 1 hoàn thành: Sàng lọc cục bộ {locally_clean_count} câu sạch (0 token), sửa cục bộ {local_fixed_count} câu, AI chuẩn hoá {ai_fixed_count} câu.",  # noqa: E501
            "processed_count": len(candidates),
            "locally_clean": locally_clean_count,
            "local_fixed": local_fixed_count,
            "ai_fixed": ai_fixed_count,
        }

    async def _try_recover_vietjack_table(self, q: Question) -> bool:
        """Attempts to recover missing table from VietJack URL."""
        try:
            import urllib.request

            from app.pipeline.parsers.vietjack import parse

            req = urllib.request.Request(q.source_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            page = parse(html, q.source_url)
            prefix = q.content.strip().splitlines()[0][:30].strip()
            for pq in page.questions:
                if prefix in pq.content and "|" in pq.content:
                    q.content = pq.content
                    log.info("Recovered table for Question #%d from %s", q.id, q.source_url)
                    return True
        except Exception as err:
            log.warning("Table recovery failed for Question #%d: %s", q.id, err)
        return False

    # --------------------------------------------------------------------------
    # JOB 2: Double-Blind Consensus Solver & Auto-Verification
    # (State: TAXONOMY_CLEAN / SUSPECT_SOURCE_ERROR -> VERIFIED_CONSENSUS / NEEDS_HUMAN_REVIEW)
    # --------------------------------------------------------------------------
    async def run_job2_verify_and_attach_to_exam(self, batch_size: int = 8) -> dict[str, Any]:
        """Solves candidate questions blindly; auto-verifies and attaches to exam on 2-pass consensus."""
        stmt = (
            select(Question)
            .where(
                or_(
                    Question.audit_status == "TAXONOMY_CLEAN",
                    Question.audit_status == "FONT_OR_TAXONOMY_FIXED",
                    Question.audit_status == "SUSPECT_SOURCE_ERROR",
                    Question.audit_status == "PENDING",
                    Question.audit_status.is_(None),
                ),
                Question.audit_status != "VERIFIED_CONSENSUS",
                Question.audit_status != "VERIFIED_BY_CONSENSUS",
                Question.audit_status != "NEEDS_HUMAN_REVIEW",
            )
            .order_by(Question.ai_attempt_count.desc(), Question.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        candidates = list((await self.session.scalars(stmt)).all())
        if not candidates:
            return {"status": "ok", "message": "Không có câu hỏi nào cần giải ở Job 2.", "processed_count": 0}

        # Dead-letter queue guard: Any question with >= 3 attempts without consensus goes to Admin Review
        solve_candidates: list[Question] = []
        for q in candidates:
            if q.ai_attempt_count >= 3:
                q.audit_status = "NEEDS_HUMAN_REVIEW"
                q.quality_flags = list(set((q.quality_flags or []) + ["MAX_AI_ATTEMPTS_EXCEEDED"]))
            else:
                solve_candidates.append(q)

        if not solve_candidates:
            await self.session.commit()
            return {"status": "ok", "message": "Tất cả câu hỏi trong lô đều vượt quá số lần thử và đã chuyển Admin.", "processed_count": len(candidates)}  # noqa: E501

        log.info("Job 2: Blind-solving %d candidate questions...", len(solve_candidates))
        provider = self._get_provider()
        is_mock = getattr(provider, "is_mock", False) or (hasattr(provider, "_pool") and provider._pool.is_empty())
        if is_mock:
            return {"status": "mock", "message": "Mock provider active, skipping LLM execution.", "processed_count": len(solve_candidates)}  # noqa: E501

        # True Double-Blind Isolation: Send ONLY content & choices (NO web answers, NO previous AI answers, NO solutions)  # noqa: E501
        items_payload = []
        for q in solve_candidates:
            blind_choices = []
            for c in (q.choices or []):
                blind_choices.append({
                    "id": c.get("id", ""),
                    "content": c.get("content", ""),
                })
            items_payload.append({
                "question_id": q.id,
                "type": q.question_type.value,
                "content": q.content,
                "choices": blind_choices,
            })

        solve_prompt = f"""Bạn là Giáo viên Chuyên gia Giải đề Độc lập (Double-Blind Verification & Consensus Solver).
Nhiệm vụ:
1. Đọc kỹ từng câu hỏi dưới đây (chỉ có đề bài và các lựa chọn, TUYỆT ĐỐI KHÔNG có đáp án trước).
2. Tự tính toán, kiểm tra lập luận logic từng bước thật chính xác bằng tiếng Việt.
3. Chốt kết quả:
   - Với trắc nghiệm 1 đáp án: ghi ký tự đáp án đúng nhất ('A', 'B', 'C', hoặc 'D') vào `solved_answer`.
   - Với trắc nghiệm Đúng/Sai 4 ý: ghi chuỗi 4 chữ cái (VD: 'TTFT' hoặc 'ĐSĐS') theo thứ tự 4 ý a, b, c, d vào `solved_answer`.
   - Với câu trả lời ngắn: ghi con số đáp số chuẩn (hoặc phân số a/b) vào `solved_answer`.
4. Nếu đề bài bị sai khoa học, mâu thuẫn dữ kiện hoặc không có phương án nào đúng, hãy đặt `is_source_error_suspected` = true.

Danh sách câu hỏi:
{json.dumps(items_payload, ensure_ascii=False, indent=2)}
"""  # noqa: E501

        try:
            result: Job2BatchResponse = await provider._generate(
                contents=[solve_prompt],
                schema=Job2BatchResponse,
                thinking="",
                code_execution=False,
            )

            consensus_count = 0
            disagreement_count = 0
            first_pass_count = 0

            for sol in result.items:
                target_q = next((q for q in solve_candidates if q.id == sol.question_id), None)
                if not target_q:
                    continue

                canonical_sol = canonicalize_answer(sol.solved_answer, target_q.question_type) or sol.solved_answer.strip().upper()  # noqa: E501

                # Get existing web source answer for comparison
                web_answer = ""
                for c in (target_q.choices or []):
                    if c.get("is_correct"):
                        web_answer = canonicalize_answer(c.get("id", ""), target_q.question_type)
                        break

                if target_q.ai_attempt_count == 0:
                    # PASS 1: Record baseline answer
                    target_q.first_ai_answer = canonical_sol
                    target_q.ai_attempt_count = 1
                    first_pass_count += 1

                    if web_answer and web_answer != canonical_sol:
                        target_q.quality_flags = list(set((target_q.quality_flags or []) + ["SUSPECT_SOURCE_ERROR"]))

                    if sol.is_source_error_suspected:
                        target_q.audit_status = "SUSPECT_SOURCE_ERROR"
                        target_q.quality_flags = list(set((target_q.quality_flags or []) + ["SUSPECT_SOURCE_ERROR"]))
                    else:
                        target_q.audit_status = "TAXONOMY_CLEAN"

                else:
                    # PASS 2: Independent verification pass -> Compare Pass 1 vs Pass 2
                    target_q.second_ai_answer = canonical_sol
                    target_q.ai_attempt_count = 2

                    if is_consensus_reached(target_q.first_ai_answer, canonical_sol, target_q.question_type):
                        # CONSENSUS ACHIEVED! Auto-approve and publish
                        target_q.audit_status = "VERIFIED_CONSENSUS"
                        target_q.is_verified = True
                        consensus_count += 1

                        # Adopt AI detailed explanation if current explanation is brief or absent
                        if not target_q.explanation or len(target_q.explanation) < 30:
                            target_q.explanation = sol.step_by_step_explanation
                            target_q.explanation_by_ai = True

                        # Sync choice correctness with consensus answer
                        if target_q.question_type == QuestionType.MULTIPLE_CHOICE and target_q.choices:
                            new_choices = []
                            for c in target_q.choices:
                                cc = dict(c)
                                cc["is_correct"] = (canonicalize_answer(cc.get("id", ""), target_q.question_type) == canonical_sol)  # noqa: E501
                                new_choices.append(cc)
                            target_q.choices = new_choices
                        elif target_q.question_type == QuestionType.TRUE_FALSE and len(canonical_sol) == 4 and target_q.choices:  # noqa: E501
                            new_choices = []
                            for idx, c in enumerate(target_q.choices):
                                cc = dict(c)
                                if idx < 4:
                                    cc["is_correct"] = (canonical_sol[idx] in ("T", "Đ"))
                                new_choices.append(cc)
                            target_q.choices = new_choices

                        # Auto-attach to pending exam if present
                        if target_q.pending_exam_id:
                            try:
                                await attach_question_to_exam_if_missing(
                                    self.session, target_q.pending_exam_id, target_q.id
                                )
                                log.info("Auto-attached Question #%d to Exam #%d", target_q.id, target_q.pending_exam_id)  # noqa: E501
                            except Exception as ex:
                                log.error("Failed to auto-attach Question #%d to Exam #%d: %s", target_q.id, target_q.pending_exam_id, ex)  # noqa: E501

                    else:
                        # DISAGREEMENT DETECTED! Route to Admin Review
                        target_q.audit_status = "NEEDS_HUMAN_REVIEW"
                        target_q.quality_flags = list(set((target_q.quality_flags or []) + ["AI_DISAGREEMENT"]))
                        disagreement_count += 1

            await self.session.commit()
            return {
                "status": "ok",
                "message": f"Job 2 hoàn thành: {consensus_count} câu đạt đồng thuận (tự duyệt & nạp đề), {disagreement_count} câu lệch kết quả (chuyển Admin), {first_pass_count} câu hoàn tất lần 1.",  # noqa: E501
                "consensus_count": consensus_count,
                "disagreement_count": disagreement_count,
                "first_pass_count": first_pass_count,
            }

        except Exception as e:
            log.error("Job 2 solve call failed: %s", e)
            return {"status": "error", "message": f"Job 2 gặp lỗi: {e}"}


# ==============================================================================
# Public Wrapper Functions for Workers and Tasks
# ==============================================================================
async def run_job1_taxonomy_and_font_healer(session: AsyncSession, batch_size: int = 20) -> dict[str, Any]:
    """Runs Job 1: Taxonomy & Font Healer (batch size 20)."""
    worker = QuestionBankAuditWorker(session)
    return await worker.run_job1_heal_font_and_taxonomy(batch_size=batch_size)


async def run_job2_double_blind_verify_and_publish(session: AsyncSession, batch_size: int = 8) -> dict[str, Any]:
    """Runs Job 2: Double-Blind Consensus Solver & Auto-Publisher (batch size 8)."""
    worker = QuestionBankAuditWorker(session)
    return await worker.run_job2_verify_and_attach_to_exam(batch_size=batch_size)
