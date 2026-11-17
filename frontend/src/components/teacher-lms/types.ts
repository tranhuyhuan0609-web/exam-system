import type {
  BlueprintItemType,
  CognitiveDifficulty,
  SubjectGroup2025,
} from "@/lib/matrix-builder";

export interface MatrixTopicRow {
  readonly topicId: string;
  readonly chapterCode: string;
  readonly topicName: string;
}

export interface MatrixAvailabilityMap {
  readonly [cellKey: string]: number;
}

export interface MatrixBuilderSessionData {
  readonly examId: string;
  readonly examTitle: string;
  readonly subjectGroup: SubjectGroup2025;
  readonly durationMinutes: number;
  readonly randomSeed: number;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string;
  readonly rawAssetUrl: string;
  readonly topics: readonly MatrixTopicRow[];
  readonly availability: MatrixAvailabilityMap;
  readonly initialDesired: Record<string, number>;
}

export interface StudentGradeRow {
  readonly studentId: string;
  readonly studentName: string;
  readonly studentCode: string;
  readonly submitted: boolean;
  readonly submittedAt: string | null;
  readonly isLate: boolean;
  readonly score: number | null;
  readonly attemptId: string | null;
  readonly weakTopicIds: readonly string[];
}

export interface ItemDistractorStat {
  readonly key: "A" | "B" | "C" | "D";
  readonly selectedCount: number;
  readonly selectedPct: number;
  readonly isCorrect: boolean;
}

export interface ClassItemAnalysisRow {
  readonly questionId: string;
  readonly stableCode: string;
  readonly topicName: string;
  readonly stemMd: string;
  readonly totalResponses: number;
  readonly wrongRatePct: number;
  readonly topDistractorKey: "A" | "B" | "C" | "D";
  readonly distractors: readonly ItemDistractorStat[];
}

export interface CopilotRemediationCluster {
  readonly clusterId: string;
  readonly topicId: string;
  readonly topicName: string;
  readonly summary: string;
  readonly studentIds: readonly string[];
  readonly studentNames: readonly string[];
  readonly recommendedQuestionCount: number;
  readonly assigned?: boolean;
}

/** One row of `GET /api/v1/assignments?class_id=`. */
export interface ClassAssignmentOption {
  readonly id: string;
  readonly class_id: string | null;
  readonly assessment_id: string | null;
  readonly assessment_version_id: string;
  readonly assessment_title: string | null;
  readonly due_at: string | null;
  readonly max_attempts: number;
}

/** One published paper of `GET /api/v1/assessments`. */
export interface PublishedPaperOption {
  readonly id: string;
  readonly title: string;
  readonly current_version_id: string | null;
}

export interface TeacherClassLmsData {
  readonly classId: string;
  readonly className: string;
  readonly subjectName: string;
  readonly inviteCode: string;
  readonly activeAssessmentTitle: string;
  readonly assessmentVersionId: string;
  readonly activeAssignmentId?: string;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string;
  readonly rawAssetUrl: string;
  readonly students: readonly StudentGradeRow[];
  readonly topMissedItems: readonly ClassItemAnalysisRow[];
  readonly copilotClusters: readonly CopilotRemediationCluster[];
}

export function matrixCellKey(
  topicId: string,
  difficulty: CognitiveDifficulty,
  questionType: BlueprintItemType,
): string {
  return `${topicId}::${difficulty}::${questionType}`;
}

export const MATRIX_DIFFICULTIES: readonly CognitiveDifficulty[] = ["NB", "TH", "VD", "VDC"];

export const MATRIX_ITEM_TYPES: readonly {
  readonly key: BlueprintItemType;
  readonly shortLabel: string;
  readonly fullLabel: string;
}[] = [
  { key: "SINGLE_CHOICE", shortLabel: "P.I (ABCD)", fullLabel: "Phần I · Trắc nghiệm 4 lựa chọn" },
  { key: "CLUSTER_TRUE_FALSE", shortLabel: "P.II (Đ/S)", fullLabel: "Phần II · Đúng/Sai 4 ý" },
  { key: "SHORT_NUMERIC", shortLabel: "P.III (TLN)", fullLabel: "Phần III · Trả lời ngắn" },
];

/** A builder with nothing in it yet: what a real session starts from until the question bank has answered. */
export function createEmptyMatrixBuilderData(subjectGroup: SubjectGroup2025 = "MATH"): MatrixBuilderSessionData {
  return {
    examId: "",
    examTitle: subjectGroup === "MATH" ? "Đề thi mới · Toán" : "Đề thi mới · Lý / Hóa / Sinh",
    subjectGroup,
    durationMinutes: subjectGroup === "MATH" ? 90 : 50,
    randomSeed: 20250626,
    requiresProAsset: false,
    previewPdfUrl: "",
    rawAssetUrl: "",
    topics: [],
    availability: {},
    initialDesired: {},
  };
}

/** A class with nothing in it yet: what a real session starts from until the API has answered. */
export function createEmptyTeacherClassData(classId: string): TeacherClassLmsData {
  return {
    classId,
    className: "Lớp học",
    subjectName: "",
    inviteCode: "",
    activeAssessmentTitle: "Chưa giao đề nào",
    assessmentVersionId: "",
    requiresProAsset: false,
    previewPdfUrl: "",
    rawAssetUrl: "",
    students: [],
    topMissedItems: [],
    copilotClusters: [],
  };
}

export function createDemoMatrixBuilderData(
  subjectGroup: SubjectGroup2025 = "MATH",
): MatrixBuilderSessionData {
  const topics: MatrixTopicRow[] =
    subjectGroup === "MATH"
      ? [
          { topicId: "TOAN12_HAM_SO", chapterCode: "Ch. I", topicName: "Ứng dụng đạo hàm & Khảo sát hàm số" },
          { topicId: "TOAN12_MU_LOG", chapterCode: "Ch. II", topicName: "Hàm số lũy thừa, mũ và logarit" },
          { topicId: "TOAN12_NGUYEN_HAM", chapterCode: "Ch. III", topicName: "Nguyên hàm, tích phân và ứng dụng" },
          { topicId: "TOAN12_XAC_SUAT_BAYES", chapterCode: "Ch. VI", topicName: "Xác suất có điều kiện & Công thức Bayes" },
        ]
      : [
          { topicId: "LY12_VAT_LY_NHIET", chapterCode: "Ch. I", topicName: "Vật lý nhiệt & Nội năng" },
          { topicId: "LY12_KHI_LY_TUONG", chapterCode: "Ch. II", topicName: "Khí lý tưởng & Phương trình trạng thái" },
          { topicId: "LY12_TU_TRUONG", chapterCode: "Ch. III", topicName: "Từ trường & Cảm ứng điện từ" },
          { topicId: "LY12_HAT_NHAN", chapterCode: "Ch. IV", topicName: "Vật lý hạt nhân & Phóng xạ" },
        ];

  const availability: Record<string, number> = {};
  for (const t of topics) {
    for (const diff of MATRIX_DIFFICULTIES) {
      for (const it of MATRIX_ITEM_TYPES) {
        // Make VDC SHORT_NUMERIC intentionally tight in Bayes / Nuclear to showcase real-time ShortageReport.
        let avail = diff === "NB" ? 18 : diff === "TH" ? 14 : diff === "VD" ? 8 : 3;
        if (
          (t.topicId === "TOAN12_XAC_SUAT_BAYES" || t.topicId === "LY12_HAT_NHAN") &&
          diff === "VDC" &&
          it.key === "SHORT_NUMERIC"
        ) {
          avail = 1;
        }
        availability[matrixCellKey(t.topicId, diff, it.key)] = avail;
      }
    }
  }

  // Seed default THPTQG 2025+ distribution (MATH = 12/4/6 = 22 câu; NATURAL_SCIENCE = 18/4/6 = 28 câu)
  const initialDesired: Record<string, number> = {};
  if (subjectGroup === "MATH") {
    initialDesired[matrixCellKey(topics[0]!.topicId, "NB", "SINGLE_CHOICE")] = 4;
    initialDesired[matrixCellKey(topics[1]!.topicId, "NB", "SINGLE_CHOICE")] = 3;
    initialDesired[matrixCellKey(topics[2]!.topicId, "TH", "SINGLE_CHOICE")] = 3;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VD", "SINGLE_CHOICE")] = 2;

    initialDesired[matrixCellKey(topics[0]!.topicId, "TH", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[1]!.topicId, "TH", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[2]!.topicId, "VD", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VD", "CLUSTER_TRUE_FALSE")] = 1;

    initialDesired[matrixCellKey(topics[0]!.topicId, "VD", "SHORT_NUMERIC")] = 2;
    initialDesired[matrixCellKey(topics[2]!.topicId, "VD", "SHORT_NUMERIC")] = 2;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VDC", "SHORT_NUMERIC")] = 2; // Desired 2 vs Avail 1 -> live Shortage!
  } else {
    initialDesired[matrixCellKey(topics[0]!.topicId, "NB", "SINGLE_CHOICE")] = 5;
    initialDesired[matrixCellKey(topics[1]!.topicId, "NB", "SINGLE_CHOICE")] = 5;
    initialDesired[matrixCellKey(topics[2]!.topicId, "TH", "SINGLE_CHOICE")] = 5;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VD", "SINGLE_CHOICE")] = 3;

    initialDesired[matrixCellKey(topics[0]!.topicId, "TH", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[1]!.topicId, "TH", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[2]!.topicId, "VD", "CLUSTER_TRUE_FALSE")] = 1;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VD", "CLUSTER_TRUE_FALSE")] = 1;

    initialDesired[matrixCellKey(topics[0]!.topicId, "VD", "SHORT_NUMERIC")] = 2;
    initialDesired[matrixCellKey(topics[2]!.topicId, "VD", "SHORT_NUMERIC")] = 2;
    initialDesired[matrixCellKey(topics[3]!.topicId, "VDC", "SHORT_NUMERIC")] = 2; // Desired 2 vs Avail 1 -> live Shortage!
  }

  return {
    examId: subjectGroup === "MATH" ? "asm-thptqg-math-2025" : "asm-thptqg-phys-2025",
    examTitle:
      subjectGroup === "MATH"
        ? "Đề thi thử THPTQG 2025+ Môn Toán (Chuẩn 22 câu)"
        : "Đề thi thử THPTQG 2025+ Môn Vật lí (Chuẩn 28 câu)",
    subjectGroup,
    durationMinutes: subjectGroup === "MATH" ? 90 : 50,
    randomSeed: 20250626,
    requiresProAsset: false,
    previewPdfUrl: "/previews/thptqg-blueprint-preview.pdf",
    rawAssetUrl: "/assets/thptqg-blueprint-full.pdf",
    topics,
    availability,
    initialDesired,
  };
}

export function createDemoTeacherClassData(classId: string): TeacherClassLmsData {
  return {
    classId,
    className: classId === "class-12a1" ? "Lớp 12A1 — Chuyên Toán" : `Lớp ${classId}`,
    subjectName: "Toán học 12 (SGK Kết nối tri thức)",
    inviteCode: "H2EDU-12A1-2025",
    activeAssessmentTitle: "Đề kiểm tra Định kỳ Chuyên đề Đạo hàm & Bayes",
    assessmentVersionId: "av-2025-12a1-01",
    requiresProAsset: false,
    previewPdfUrl: "/previews/gradebook-report-preview.pdf",
    rawAssetUrl: "/assets/gradebook-report-full.csv",
    students: [
      {
        studentId: "stu-01",
        studentName: "Nguyễn Minh Anh",
        studentCode: "HS12001",
        submitted: true,
        submittedAt: "2026-11-14 08:42",
        isLate: false,
        score: 9.25,
        attemptId: "att-001",
        weakTopicIds: [],
      },
      {
        studentId: "stu-02",
        studentName: "Trần Quốc Bảo",
        studentCode: "HS12002",
        submitted: true,
        submittedAt: "2026-11-14 08:49",
        isLate: false,
        score: 6.5,
        attemptId: "att-002",
        weakTopicIds: ["TOAN12_XAC_SUAT_BAYES"],
      },
      {
        studentId: "stu-03",
        studentName: "Lê Thu Hà",
        studentCode: "HS12003",
        submitted: true,
        submittedAt: "2026-11-14 09:05",
        isLate: true,
        score: 4.75,
        attemptId: "att-003",
        weakTopicIds: ["TOAN12_XAC_SUAT_BAYES", "TOAN12_HAM_SO"],
      },
      {
        studentId: "stu-04",
        studentName: "Phạm Gia Huy",
        studentCode: "HS12004",
        submitted: true,
        submittedAt: "2026-11-14 08:55",
        isLate: false,
        score: 7.75,
        attemptId: "att-004",
        weakTopicIds: ["TOAN12_XAC_SUAT_BAYES"],
      },
      {
        studentId: "stu-05",
        studentName: "Hoàng Ngọc Linh",
        studentCode: "HS12005",
        submitted: false,
        submittedAt: null,
        isLate: false,
        score: null,
        attemptId: null,
        weakTopicIds: ["TOAN12_XAC_SUAT_BAYES"],
      },
      {
        studentId: "stu-06",
        studentName: "Vũ Đức Mạnh",
        studentCode: "HS12006",
        submitted: false,
        submittedAt: null,
        isLate: false,
        score: null,
        attemptId: null,
        weakTopicIds: ["TOAN12_HAM_SO"],
      },
    ],
    topMissedItems: [
      {
        questionId: "q-bayes-19",
        stableCode: "LEG-19",
        topicName: "Công thức Bayes",
        stemMd: "Tính xác suất có điều kiện $P(A \\mid B) = \\frac{P(B \\mid A)P(A)}{P(B)}$ trong mô hình xét nghiệm.",
        totalResponses: 48,
        wrongRatePct: 72.9,
        topDistractorKey: "C",
        distractors: [
          { key: "A", selectedCount: 13, selectedPct: 27.1, isCorrect: true },
          { key: "B", selectedCount: 8, selectedPct: 16.7, isCorrect: false },
          { key: "C", selectedCount: 22, selectedPct: 45.8, isCorrect: false },
          { key: "D", selectedCount: 5, selectedPct: 10.4, isCorrect: false },
        ],
      },
      {
        questionId: "q-cuctri-21",
        stableCode: "LEG-21",
        topicName: "Cực trị hàm chứa trị tuyệt đối",
        stemMd: "Số điểm cực trị của hàm số $y = |x^3 - 3x^2 + m|$ khi $m \\in (0; 4)$ là:",
        totalResponses: 48,
        wrongRatePct: 64.6,
        topDistractorKey: "B",
        distractors: [
          { key: "A", selectedCount: 7, selectedPct: 14.6, isCorrect: false },
          { key: "B", selectedCount: 21, selectedPct: 43.8, isCorrect: false },
          { key: "C", selectedCount: 3, selectedPct: 6.2, isCorrect: false },
          { key: "D", selectedCount: 17, selectedPct: 35.4, isCorrect: true },
        ],
      },
      {
        questionId: "q-tichphan-18",
        stableCode: "LEG-18",
        topicName: "Tích phân hàm ẩn",
        stemMd: "Cho $f'(x) + 2f(x) = e^{-2x}$. Giá trị $\\int_0^1 f(x)\\,dx$ bằng:",
        totalResponses: 48,
        wrongRatePct: 58.3,
        topDistractorKey: "A",
        distractors: [
          { key: "A", selectedCount: 18, selectedPct: 37.5, isCorrect: false },
          { key: "B", selectedCount: 20, selectedPct: 41.7, isCorrect: true },
          { key: "C", selectedCount: 6, selectedPct: 12.5, isCorrect: false },
          { key: "D", selectedCount: 4, selectedPct: 8.3, isCorrect: false },
        ],
      },
      {
        questionId: "q-log-15",
        stableCode: "LEG-15",
        topicName: "Bất phương trình mũ – logarit",
        stemMd: "Số nghiệm nguyên của bất phương trình $\\log_2(x^2 - 3x + 2) \\le 1$:",
        totalResponses: 48,
        wrongRatePct: 45.8,
        topDistractorKey: "D",
        distractors: [
          { key: "A", selectedCount: 26, selectedPct: 54.2, isCorrect: true },
          { key: "B", selectedCount: 5, selectedPct: 10.4, isCorrect: false },
          { key: "C", selectedCount: 5, selectedPct: 10.4, isCorrect: false },
          { key: "D", selectedCount: 12, selectedPct: 25.0, isCorrect: false },
        ],
      },
      {
        questionId: "q-don-dieu-12",
        stableCode: "LEG-12",
        topicName: "Tính đơn điệu tham số m",
        stemMd: "Tìm tất cả tham số $m$ để $y = \\frac{mx - 4}{x - m}$ đồng biến trên $(0; +\\infty)$:",
        totalResponses: 48,
        wrongRatePct: 39.6,
        topDistractorKey: "B",
        distractors: [
          { key: "A", selectedCount: 29, selectedPct: 60.4, isCorrect: true },
          { key: "B", selectedCount: 14, selectedPct: 29.2, isCorrect: false },
          { key: "C", selectedCount: 3, selectedPct: 6.2, isCorrect: false },
          { key: "D", selectedCount: 2, selectedPct: 4.2, isCorrect: false },
        ],
      },
    ],
    copilotClusters: [
      {
        clusterId: "cluster-bayes-01",
        topicId: "TOAN12_XAC_SUAT_BAYES",
        topicName: "Xác suất có điều kiện & Công thức Bayes",
        summary:
          "3/4 học sinh nộp bài nhầm lẫn công thức xác suất đảo P(A|B) với P(A ∩ B), chọn phương án nhiễu C ở câu LEG-19.",
        studentIds: ["stu-02", "stu-03", "stu-04"],
        studentNames: ["Trần Quốc Bảo", "Lê Thu Hà", "Phạm Gia Huy"],
        recommendedQuestionCount: 10,
      },
    ],
  };
}
