import {
  type AnswerMap,
  type ChoiceLetter,
  type ClusterStatementItem,
  type ClusterSubKey,
  createEmptyAnswerState,
  type ExamAttemptData,
  type ExamFigure,
  type ExamOptionItem,
  type ExamQuestion,
  type QuestionAnswerState,
} from "./types";

function extractRichText(raw: unknown): string {
  if (typeof raw === "string") return raw.trim();
  if (!raw || typeof raw !== "object") return "";
  const obj = raw as Record<string, unknown>;
  for (const key of ["markdown", "stem_md", "text_md", "text", "content", "stem", "label", "body", "title"]) {
    const candidate = obj[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return "";
}

function normalizeChoiceKey(rawLabel: unknown, fallbackIndex: number): ChoiceLetter {
  const letters: readonly ChoiceLetter[] = ["A", "B", "C", "D"];
  const normalized = String(rawLabel ?? "").trim().toUpperCase();
  if (normalized === "A" || normalized === "B" || normalized === "C" || normalized === "D") {
    return normalized;
  }
  return letters[fallbackIndex] ?? "A";
}

function normalizeClusterKey(rawLabel: unknown, fallbackIndex: number): ClusterSubKey {
  const keys: readonly ClusterSubKey[] = ["a", "b", "c", "d"];
  const normalized = String(rawLabel ?? "").trim().toLowerCase();
  if (normalized === "a" || normalized === "b" || normalized === "c" || normalized === "d") {
    return normalized;
  }
  return keys[fallbackIndex] ?? "a";
}

/** The images a stem carries (`stem_json.media`): entries `{type: "image", asset_id, alt}`. */
export function extractFigures(raw: unknown): readonly ExamFigure[] {
  if (!raw || typeof raw !== "object") return [];
  const media = (raw as Record<string, unknown>).media;
  if (!Array.isArray(media)) return [];
  const figures: ExamFigure[] = [];
  for (const entry of media) {
    const item = (entry ?? {}) as Record<string, unknown>;
    if (item.type === "image" && typeof item.asset_id === "string" && item.asset_id) {
      const alt = typeof item.alt === "string" && item.alt ? item.alt : `Hình ${figures.length + 1}`;
      figures.push({ assetId: item.asset_id, alt });
    }
  }
  return figures;
}

export function mapPaperPayloadToExamAttempt(
  raw: unknown,
  attemptIdFallback: string,
): ExamAttemptData {
  if (!raw || typeof raw !== "object") {
    throw new Error("Dữ liệu đề thi từ máy chủ không hợp lệ.");
  }

  const obj = raw as Record<string, unknown>;
  const rawQuestions = Array.isArray(obj.questions) ? obj.questions : [];

  // Pass-through if backend or mock already returns ExamAttemptData camelCase shape
  const firstQ = rawQuestions[0] as Record<string, unknown> | undefined;
  if (typeof obj.deadlineAtIso === "string" && (!firstQ || typeof firstQ.part === "string")) {
    return raw as ExamAttemptData;
  }

  const sortedRaw = [...rawQuestions].sort((a, b) => {
    const posA = typeof (a as Record<string, unknown>).display_position === "number"
      ? ((a as Record<string, unknown>).display_position as number)
      : 0;
    const posB = typeof (b as Record<string, unknown>).display_position === "number"
      ? ((b as Record<string, unknown>).display_position as number)
      : 0;
    return posA - posB;
  });

  const questions: ExamQuestion[] = sortedRaw.map((entry, idx) => {
    const q = entry as Record<string, unknown>;
    const qId = String(q.question_version_id ?? q.questionVersionId ?? `q-${idx + 1}`);
    const num = typeof q.display_position === "number" ? q.display_position : idx + 1;
    const section = String(q.section_code ?? "").toUpperCase();
    const itemType = String(q.item_type ?? "").toUpperCase();

    const stimulusText = extractRichText(q.stimulus_json);
    const stemBody = extractRichText(q.stem_json) || extractRichText(q.stem) || `Câu ${num}`;
    const stem = stimulusText ? `${stimulusText}\n\n${stemBody}` : stemBody;
    const figures = extractFigures(q.stem_json);
    const withFigures = figures.length > 0 ? { figures } : {};

    const rawOptions = Array.isArray(q.options) ? q.options : [];

    if (section === "PART_2" || itemType === "CLUSTER_TRUE_FALSE") {
      const statements: ClusterStatementItem[] = rawOptions.slice(0, 4).map((opt, optIdx) => {
        const optObj = (opt ?? {}) as Record<string, unknown>;
        return {
          key: normalizeClusterKey(optObj.display_label ?? optObj.key, optIdx),
          text: extractRichText(optObj.content_json) || extractRichText(optObj.text) || `Mệnh đề ${optIdx + 1}`,
        };
      });
      return {
        questionVersionId: qId,
        number: num,
        part: "PART_2",
        stem,
        ...withFigures,
        statements,
      };
    }

    if (section === "PART_3" || itemType === "SHORT_NUMERIC" || itemType === "NUMERIC") {
      return {
        questionVersionId: qId,
        number: num,
        part: "PART_3",
        stem,
        ...withFigures,
      };
    }

    const options: ExamOptionItem[] = rawOptions.slice(0, 4).map((opt, optIdx) => {
      const optObj = (opt ?? {}) as Record<string, unknown>;
      return {
        key: normalizeChoiceKey(optObj.display_label ?? optObj.key, optIdx),
        text: extractRichText(optObj.content_json) || extractRichText(optObj.text) || `Phương án ${optIdx + 1}`,
      };
    });

    return {
      questionVersionId: qId,
      number: num,
      part: "PART_1",
      stem,
      ...withFigures,
      options,
    };
  });

  const userTierRaw = String(obj.user_tier ?? obj.userTier ?? "STUDENT_PRO").toUpperCase();
  const userTier =
    userTierRaw === "FREE" ||
    userTierRaw === "STUDENT_PRO" ||
    userTierRaw === "TEACHER_PRO" ||
    userTierRaw === "SCHOOL_ENTERPRISE"
      ? userTierRaw
      : "STUDENT_PRO";

  return {
    attemptId: String(obj.attempt_id ?? obj.attemptId ?? attemptIdFallback),
    title: String(obj.title ?? "Đề thi trực tuyến H2Edu"),
    subjectName: String(obj.subject_name ?? obj.subjectName ?? "THPTQG 2025"),
    deadlineAtIso: String(
      obj.deadline_at ?? obj.deadlineAtIso ?? new Date(Date.now() + 90 * 60 * 1000).toISOString(),
    ),
    userTier,
    requiresProAsset: Boolean(obj.requires_pro_asset ?? obj.requiresProAsset ?? false),
    previewPdfUrl: (obj.preview_pdf_url ?? obj.previewPdfUrl ?? null) as string | null,
    rawAssetUrl: (obj.raw_asset_url ?? obj.rawAssetUrl ?? null) as string | null,
    questions,
  };
}

export function hydrateAnswersFromSyncPayload(
  questions: readonly ExamQuestion[],
  syncPayload: unknown,
  parseAnswerFn: (q: ExamQuestion, prev: QuestionAnswerState, payload: unknown) => QuestionAnswerState,
): AnswerMap {
  if (!syncPayload || typeof syncPayload !== "object") return {};
  const obj = syncPayload as Record<string, unknown>;
  const rawResponses = obj.responses;
  if (!rawResponses || typeof rawResponses !== "object" || Array.isArray(rawResponses)) {
    return {};
  }

  const questionById = new Map<string, ExamQuestion>();
  for (const q of questions) {
    questionById.set(q.questionVersionId, q);
  }

  const result: AnswerMap = {};
  for (const [qId, item] of Object.entries(rawResponses as Record<string, unknown>)) {
    const question = questionById.get(qId);
    if (!question || !item || typeof item !== "object") continue;
    const itemObj = item as Record<string, unknown>;
    const base = createEmptyAnswerState();
    const mapped = parseAnswerFn(question, base, itemObj.response_payload ?? itemObj);
    result[qId] = {
      ...mapped,
      flagged: Boolean(itemObj.is_flagged ?? mapped.flagged),
    };
  }
  return result;
}
