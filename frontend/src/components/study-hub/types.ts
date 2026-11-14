import type { UserTier } from "@/lib/ui-state-machine";
import type { PracticeTabType } from "@/lib/workspace1-study-hub";

export type CurriculumLevel = "CHAPTER" | "LESSON" | "SECTION" | "PROBLEM_TYPE";

export interface CurriculumMasteryMetrics {
  readonly masteryRatio: number;
  readonly answeredCount: number;
  readonly standardError: number;
}

export interface CurriculumTreeNode {
  readonly id: string;
  readonly slug: string;
  readonly level: CurriculumLevel;
  readonly code: string;
  readonly title: string;
  readonly mastery: CurriculumMasteryMetrics;
  readonly children?: readonly CurriculumTreeNode[];
}

export type StudyBlockKind =
  | "THEORY_SUMMARY"
  | "FORMULA_BOX"
  | "METHOD_STEPS"
  | "TIP_WARNING"
  | "WORKED_EXAMPLE";

export interface StudyBlock {
  readonly id: string;
  readonly kind: StudyBlockKind;
  readonly title: string;
  readonly content: string;
  readonly steps?: readonly string[];
  readonly solutionContent?: string;
}

export interface StudyPracticeItem {
  readonly id: string;
  readonly tabType: PracticeTabType;
  readonly label: string;
  readonly promptLatex: string;
  readonly hintText: string;
  readonly explanationLatex: string;
  readonly options?: readonly { readonly key: string; readonly label: string }[];
  readonly statements?: readonly { readonly key: "a" | "b" | "c" | "d"; readonly text: string }[];
  readonly expectedAnswer?:
    | { readonly kind: "SINGLE_CHOICE"; readonly key: string }
    | { readonly kind: "CLUSTER_TRUE_FALSE"; readonly map: Record<"a" | "b" | "c" | "d", boolean> }
    | { readonly kind: "SHORT_NUMERIC"; readonly numericValue: number }
    | { readonly kind: "ESSAY"; readonly rubricSummary: string };
}

export interface StudyHubErrorNoteItem {
  readonly id: string;
  readonly questionCode: string;
  readonly topicTitle: string;
  readonly summaryLatex: string;
  readonly diagnosis: string;
  readonly consecutiveCorrectCount: number;
  readonly status: "ACTIVE" | "RESOLVED";
  readonly variantDrillPrompt: string;
  readonly variantCorrectOptionKey: string;
  readonly variantOptions: readonly { readonly key: string; readonly label: string }[];
}

export type AiTutorMode = "HINT_MODE" | "EXPLAIN_MODE" | "CHALLENGE_MODE";

export interface StudyDocumentPayload {
  readonly slug: string;
  readonly title: string;
  readonly subjectLabel: string;
  readonly gradeLabel: string;
  readonly bookSeries: string;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string | null;
  readonly rawAssetUrl: string | null;
  readonly activeNodeId: string;
  readonly tree: readonly CurriculumTreeNode[];
  readonly blocks: readonly StudyBlock[];
  readonly practiceItems: readonly StudyPracticeItem[];
  readonly errorNotes: readonly StudyHubErrorNoteItem[];
}

export interface LearningPathTaskItem {
  readonly id: string;
  readonly order: number;
  readonly targetSlug: string;
  readonly targetTitle: string;
  readonly nodeCode: string;
  readonly mastery: CurriculumMasteryMetrics;
  readonly rationaleText: string;
  readonly isPrerequisiteRemediation: boolean;
  readonly prerequisiteOfTitle?: string;
  readonly teacherAssigned: boolean;
  readonly dueDateLabel?: string;
}

export const H2EDU_BRAND = {
  name: "H2Edu",
  slogan: "Học thông minh - Thi bứt phá",
  subtitle: "Kho học liệu SGK chuẩn hóa · Luyện tập 4 dạng THPTQG 2025 · Gia sư AI có kiểm chứng",
} as const;

export const DEFAULT_STUDY_TREE: readonly CurriculumTreeNode[] = [
  {
    id: "ch-6",
    slug: "xac-suat-co-dieu-kien",
    level: "CHAPTER",
    code: "CHƯƠNG VI",
    title: "Xác suất có điều kiện (Toán 12 - KNTT)",
    mastery: { masteryRatio: 0.72, answeredCount: 45, standardError: 0.18 },
    children: [
      {
        id: "les-19",
        slug: "cong-thuc-bayes",
        level: "LESSON",
        code: "BÀI 19",
        title: "Công thức xác suất toàn phần và Công thức Bayes",
        mastery: { masteryRatio: 0.84, answeredCount: 28, standardError: 0.21 },
        children: [
          {
            id: "sec-19-1",
            slug: "cong-thuc-xac-suat-toan-phan",
            level: "SECTION",
            code: "MỤC 1",
            title: "Công thức xác suất toàn phần",
            mastery: { masteryRatio: 0.65, answeredCount: 24, standardError: 0.24 },
            children: [
              {
                id: "pt-bayes-y-sinh",
                slug: "cong-thuc-bayes",
                level: "PROBLEM_TYPE",
                code: "DẠNG 1",
                title: "Bài toán xét nghiệm y sinh & độ nhạy/đặc hiệu",
                mastery: { masteryRatio: 0.44, answeredCount: 22, standardError: 0.28 },
              },
              {
                id: "pt-bayes-may-moc",
                slug: "cong-thuc-bayes",
                level: "PROBLEM_TYPE",
                code: "DẠNG 2",
                title: "Phân xưởng sản xuất nhiều dây chuyền",
                mastery: { masteryRatio: 0.9, answeredCount: 12, standardError: 0.4 },
              },
            ],
          },
        ],
      },
    ],
  },
];

export const DEFAULT_ERROR_NOTES: readonly StudyHubErrorNoteItem[] = [
  {
    id: "en-bayes-01",
    questionCode: "TOAN12-BAYES-014",
    topicTitle: "Dạng 1: Xác suất toàn phần & Công thức Bayes",
    summaryLatex: "Nhầm lẫn $P(A \\mid B)$ với $P(B \\mid A)$ trong bài toán sàng lọc bệnh.",
    diagnosis: "Chưa viết sơ đồ cây trước khi thế số vào mẫu số $P(B)$.",
    consecutiveCorrectCount: 1,
    status: "ACTIVE",
    variantDrillPrompt:
      "Một xét nghiệm nhanh có độ nhạy $P(+ \\mid B) = 0{,}9$ và tỷ lệ nhiễm trong cộng đồng $P(B) = 0{,}02$. Biết $P(+ \\mid \\overline{B}) = 0{,}05$. Giá trị $P(+)$ bằng bao nhiêu?",
    variantCorrectOptionKey: "B",
    variantOptions: [
      { key: "A", label: "$0{,}018$" },
      { key: "B", label: "$0{,}067$" },
      { key: "C", label: "$0{,}049$" },
      { key: "D", label: "$0{,}950$" },
    ],
  },
  {
    id: "en-bayes-02",
    questionCode: "TOAN12-BAYES-022",
    topicTitle: "Dạng 2: Hai hộp bi / dây chuyền sản xuất",
    summaryLatex: "Quên điều kiện xung khắc đầy đủ $P(A_1) + P(A_2) = 1$.",
    diagnosis: "Tổng xác suất các biến cố giả thuyết chưa bằng $1$.",
    consecutiveCorrectCount: 0,
    status: "ACTIVE",
    variantDrillPrompt:
      "Hộp I có $3$ bi đỏ và $7$ bi xanh; hộp II có $6$ bi đỏ và $4$ bi xanh. Chọn ngẫu nhiên một hộp rồi rút $1$ bi. Tính xác suất rút được bi đỏ.",
    variantCorrectOptionKey: "C",
    variantOptions: [
      { key: "A", label: "$0{,}30$" },
      { key: "B", label: "$0{,}60$" },
      { key: "C", label: "$0{,}45$" },
      { key: "D", label: "$0{,}90$" },
    ],
  },
];

export function createDemoStudyDocument(slug: string): StudyDocumentPayload {
  const isProDocument = slug.includes("pro") || slug.includes("nang-cao");

  return {
    slug,
    title: isProDocument
      ? "Chuyên đề PRO: 50 Bài toán Bayes vận dụng cao & Bẫy thực tế"
      : "Bài 19: Công thức xác suất toàn phần và Công thức Bayes",
    subjectLabel: "Toán học",
    gradeLabel: "Lớp 12",
    bookSeries: "Kết nối tri thức",
    requiresProAsset: isProDocument,
    previewPdfUrl: isProDocument
      ? `/api/v1/assets/preview/demo-${slug}-2pages.pdf`
      : `/api/v1/assets/preview/${slug}.pdf`,
    rawAssetUrl: isProDocument
      ? `https://cdn.h2edu.internal/bronze/pro-raw-${slug}-full.pdf`
      : null,
    activeNodeId: "pt-bayes-y-sinh",
    tree: DEFAULT_STUDY_TREE,
    blocks: [
      {
        id: "blk-theory",
        kind: "THEORY_SUMMARY",
        title: "Tóm tắt lý thuyết",
        content:
          "Cho hai biến cố $A$ và $B$ với $0 < P(A) < 1$ và $P(B) > 0$. Biến cố $B$ được phân hoạch theo $A$ và biến cố đối $\\overline{A}$ tạo nền tảng cho xác suất hậu nghiệm.",
      },
      {
        id: "blk-formula",
        kind: "FORMULA_BOX",
        title: "Hộp công thức đóng khung nổi bật",
        content:
          "Công thức xác suất toàn phần: $$P(B) = P(A)\\cdot P(B \\mid A) + P(\\overline{A})\\cdot P(B \\mid \\overline{A})$$ Công thức Bayes: $$P(A \\mid B) = \\frac{P(A)\\cdot P(B \\mid A)}{P(A)\\cdot P(B \\mid A) + P(\\overline{A})\\cdot P(B \\mid \\overline{A})}$$",
      },
      {
        id: "blk-method",
        kind: "METHOD_STEPS",
        title: "Các bước giải bài toán Bayes",
        content: "Thực hiện tuần tự $4$ bước để không nhầm chiều điều kiện:",
        steps: [
          "Bước 1: Gọi hệ đầy đủ biến cố giả thuyết $A$ và $\\overline{A}$ (hoặc $A_1, A_2, \\dots, A_n$).",
          "Bước 2: Xác định biến cố kết quả quan sát được $B$ và đọc các xác suất $P(B \\mid A)$, $P(B \\mid \\overline{A})$.",
          "Bước 3: Tính mẫu số $P(B)$ bằng công thức xác suất toàn phần.",
          "Bước 4: Áp dụng công thức Bayes để tính $P(A \\mid B)$ và kiểm tra giá trị nằm trong $[0; 1]$.",
        ],
      },
      {
        id: "blk-tip",
        kind: "TIP_WARNING",
        title: "Chú ý / Bẫy hay sai",
        content:
          "Tuyệt đối phân biệt xác suất tiền nghiệm $P(B \\mid A)$ (độ nhạy của xét nghiệm) với xác suất hậu nghiệm $P(A \\mid B)$ (xác suất thực sự mắc bệnh khi đã dương tính). Dùng sơ đồ cây $2$ tầng để kiểm tra tổng nhánh luôn bằng $1$.",
      },
      {
        id: "blk-example",
        kind: "WORKED_EXAMPLE",
        title: "Ví dụ mẫu có lời giải chi tiết",
        content:
          "Tỷ lệ một loại linh kiện lỗi là $P(A) = 0{,}04$. Bộ kiểm tra tự động báo lỗi đúng với xác suất $P(B \\mid A) = 0{,}95$ và báo nhầm trên linh kiện đạt chuẩn với $P(B \\mid \\overline{A}) = 0{,}02$. Tính $P(A \\mid B)$ (làm tròn đến hàng phần trăm).",
        solutionContent:
          "Ta có $P(B) = 0{,}04 \\cdot 0{,}95 + 0{,}96 \\cdot 0{,}02 = 0{,}038 + 0{,}0192 = 0{,}0572$. Suy ra $P(A \\mid B) = \\frac{0{,}038}{0{,}0572} \\approx 0{,}66$.",
      },
    ],
    practiceItems: [
      {
        id: "prac-essay-1",
        tabType: "ESSAY",
        label: "Bài tự luận 1",
        promptLatex:
          "Trình bày sơ đồ cây và lập luận tìm xác suất hậu nghiệm $P(A_1 \\mid B)$ với $P(A_1) = 0{,}3$, $P(B \\mid A_1) = 0{,}8$, $P(B \\mid A_2) = 0{,}2$.",
        hintText: "Kiểm tra $P(A_2) = 1 - P(A_1) = 0{,}7$, sau đó tính $P(B)$ theo xác suất toàn phần.",
        explanationLatex:
          "$P(B) = 0{,}3 \\cdot 0{,}8 + 0{,}7 \\cdot 0{,}2 = 0{,}38 \\Rightarrow P(A_1 \\mid B) = \\frac{0{,}24}{0{,}38} \\approx 0{,}63$.",
        expectedAnswer: {
          kind: "ESSAY",
          rubricSummary: "Nêu đúng hệ đầy đủ (0.25đ), tính đúng $P(B)=0.38$ (0.25đ), kết quả $\\frac{12}{19}$ (0.5đ).",
        },
      },
      {
        id: "prac-abcd-1",
        tabType: "SINGLE_CHOICE",
        label: "Câu trắc nghiệm 1",
        promptLatex:
          "Một hộp chứa $40\\%$ linh kiện từ máy I (tỷ lệ lỗi $5\\%$) và $60\\%$ từ máy II (tỷ lệ lỗi $2\\%$). Lấy ngẫu nhiên $1$ linh kiện. Xác suất lấy được linh kiện lỗi bằng bao nhiêu?",
        hintText: "Áp dụng công thức xác suất toàn phần $P(E) = 0{,}4 \\cdot 0{,}05 + 0{,}6 \\cdot 0{,}02$.",
        explanationLatex: "$P(E) = 0{,}4 \\cdot 0{,}05 + 0{,}6 \\cdot 0{,}02 = 0{,}02 + 0{,}012 = 0{,}032$.",
        options: [
          { key: "A", label: "$0{,}032$" },
          { key: "B", label: "$0{,}070$" },
          { key: "C", label: "$0{,}020$" },
          { key: "D", label: "$0{,}625$" },
        ],
        expectedAnswer: { kind: "SINGLE_CHOICE", key: "A" },
      },
      {
        id: "prac-tf-1",
        tabType: "CLUSTER_TRUE_FALSE",
        label: "Câu Đúng/Sai 4 ý",
        promptLatex:
          "Trong một đợt kiểm tra, $P(A) = 0{,}2$, $P(B \\mid A) = 0{,}9$, $P(B \\mid \\overline{A}) = 0{,}1$. Xét tính đúng sai của các mệnh đề sau:",
        hintText: "Tính trước $P(\\overline{A}) = 0{,}8$ và $P(B) = 0{,}2 \\cdot 0{,}9 + 0{,}8 \\cdot 0{,}1 = 0{,}26$.",
        explanationLatex: "a) Đúng ($0{,}8$); b) Đúng ($0{,}18$); c) Sai ($P(B)=0{,}26$); d) Đúng ($\\frac{9}{13}$).",
        statements: [
          { key: "a", text: "$P(\\overline{A}) = 0{,}8$." },
          { key: "b", text: "$P(A \\cap B) = 0{,}18$." },
          { key: "c", text: "$P(B) = 0{,}50$." },
          { key: "d", text: "$P(A \\mid B) = \\frac{9}{13}$." },
        ],
        expectedAnswer: {
          kind: "CLUSTER_TRUE_FALSE",
          map: { a: true, b: true, c: false, d: true },
        },
      },
      {
        id: "prac-short-1",
        tabType: "SHORT_NUMERIC",
        label: "Câu trả lời ngắn 1",
        promptLatex:
          "Với dữ kiện $P(A) = 0{,}5$, $P(B \\mid A) = 0{,}8$, $P(B \\mid \\overline{A}) = 0{,}2$. Tính giá trị của $P(B)$.",
        hintText: "Nhân từng nhánh và cộng lại, điền số thập phân dạng dấu phẩy hoặc dấu chấm (tối đa $4$ ký tự).",
        explanationLatex: "$P(B) = 0{,}5 \\cdot 0{,}8 + 0{,}5 \\cdot 0{,}2 = 0{,}5$.",
        expectedAnswer: { kind: "SHORT_NUMERIC", numericValue: 0.5 },
      },
    ],
    errorNotes: DEFAULT_ERROR_NOTES,
  };
}

export const DEFAULT_LEARNING_PATH: readonly LearningPathTaskItem[] = [
  {
    id: "lp-prereq-1",
    order: 1,
    targetSlug: "cong-thuc-bayes",
    targetTitle: "Ôn lại: Xác suất có điều kiện cơ bản & Bảng $2 \\times 2$",
    nodeCode: "MATH.PROB.COND",
    mastery: { masteryRatio: 0.44, answeredCount: 22, standardError: 0.28 },
    rationaleText:
      "Hệ thống phát hiện nút kiến thức mục tiêu Công thức Bayes đạt 44% (< 50%) và nút tiên quyết Xác suất có điều kiện cũng dưới 60%, nên ưu tiên củng cố phần gốc trước.",
    isPrerequisiteRemediation: true,
    prerequisiteOfTitle: "Công thức Bayes & Xác suất hậu nghiệm",
    teacherAssigned: false,
  },
  {
    id: "lp-core-2",
    order: 2,
    targetSlug: "cong-thuc-bayes",
    targetTitle: "Luyện tập: Công thức xác suất toàn phần (10 câu trọng tâm)",
    nodeCode: "MATH.PROB.TOTAL",
    mastery: { masteryRatio: 0.65, answeredCount: 24, standardError: 0.24 },
    rationaleText:
      "Củng cố mẫu số $P(B)$ qua 10 bài toán phân xưởng và xét nghiệm y sinh trước khi chuyển sang câu vận dụng Bayes.",
    isPrerequisiteRemediation: false,
    teacherAssigned: false,
  },
  {
    id: "lp-teacher-3",
    order: 3,
    targetSlug: "bo-de-thptqg-2025-pro",
    targetTitle: "Bài tập bắt buộc của GV: Đề kiểm tra 15 phút Xác suất Lớp 12A1",
    nodeCode: "CLASS.12A1.QUIZ03",
    mastery: { masteryRatio: 0.84, answeredCount: 28, standardError: 0.21 },
    rationaleText:
      "Nhiệm vụ bắt buộc do giáo viên giao — được giữ cố định trong lộ trình và không bị thuật toán gợi ý tự động ghi đè.",
    isPrerequisiteRemediation: false,
    teacherAssigned: true,
    dueDateLabel: "Hạn nộp: 21:00 Chủ nhật",
  },
];

export function canAccessProContent(tier: UserTier, requiresProAsset: boolean): boolean {
  if (!requiresProAsset) {
    return true;
  }
  return tier === "STUDENT_PRO" || tier === "TEACHER_PRO" || tier === "SCHOOL_ENTERPRISE";
}
