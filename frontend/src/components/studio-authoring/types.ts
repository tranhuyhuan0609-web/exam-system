import type { ClientAssetPermissionEntry, StudioBlockInput, StudioBlockType } from "@/lib/block-studio-linter";

export interface StudioContentDocumentData {
  readonly contentId: string;
  readonly title: string;
  readonly contentType: "ARTICLE" | "LESSON" | "DOCUMENT";
  readonly authorTenantId: string;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string | null;
  readonly rawAssetUrl: string | null;
  readonly blocks: readonly StudioBlockInput[];
}

export const BLOCK_TYPE_META: Readonly<
  Record<StudioBlockType, { readonly title: string; readonly command: string; readonly accent: string; readonly defaultMarkdown: string }>
> = {
  THEORY_SUMMARY: {
    title: "Tóm tắt lý thuyết",
    command: "/tomtat",
    accent: "border-l-blue-600 bg-blue-50/70",
    defaultMarkdown: "Tóm tắt định nghĩa cốt lõi: $P(A \\cap B) = P(A) \\cdot P(B|A)$.",
  },
  FORMULA_BOX: {
    title: "Công thức trọng tâm",
    command: "/congthuc",
    accent: "border-l-indigo-600 bg-indigo-50/80",
    defaultMarkdown: "$$P(B|A) = \\frac{P(B)P(A|B)}{P(A)}$$",
  },
  METHOD_STEPS: {
    title: "Phương pháp giải từng bước",
    command: "/phuongphap",
    accent: "border-l-emerald-600 bg-emerald-50/70",
    defaultMarkdown: "Bước 1: Gọi biến cố đầy đủ.\nBước 2: Áp dụng $\\vec{F} = m\\vec{a}$ hoặc công thức Bayes.",
  },
  TIP_WARNING: {
    title: "Chú ý & Bẫy hay sai",
    command: "/chuy",
    accent: "border-l-amber-500 bg-amber-50/80",
    defaultMarkdown: "Lưu ý kiểm tra điều kiện xác định và đơn vị $\\Delta \\varphi$ trước khi thay số.",
  },
  WORKED_EXAMPLE: {
    title: "Ví dụ minh họa có lời giải",
    command: "/vidu",
    accent: "border-l-slate-700 bg-slate-100/80",
    defaultMarkdown: "Ví dụ: Tính phản ứng tỏa nhiệt $A + B \\xrightarrow{t^\\circ} C$ với $n = 0.5\\text{ mol}$.",
  },
  INTERACTIVE_QUESTION_EMBED: {
    title: "Nhúng câu hỏi tương tác",
    command: "/cauhoi",
    accent: "border-l-purple-600 bg-purple-50/70",
    defaultMarkdown: "Câu hỏi luyện tập nhanh gắn cuối mục.",
  },
  HTML_TABLE: {
    title: "Bảng dữ liệu HTML",
    command: "/bang",
    accent: "border-l-cyan-600 bg-cyan-50/70",
    defaultMarkdown: "Bảng giá trị tham khảo dạng chuẩn.",
  },
};

export function createDefaultStudioAssetCatalog(): ReadonlyMap<string, ClientAssetPermissionEntry> {
  return new Map<string, ClientAssetPermissionEntry>([
    [
      "asset-bayes-tree",
      { assetId: "asset-bayes-tree", tenantId: "tenant-h2edu-1", visibilityScope: "TENANT_PRIVATE" },
    ],
    [
      "asset-thptqg-public",
      { assetId: "asset-thptqg-public", tenantId: "system", visibilityScope: "SYSTEM_PUBLIC" },
    ],
    [
      "asset-other-tenant",
      { assetId: "asset-other-tenant", tenantId: "tenant-external-99", visibilityScope: "TENANT_PRIVATE" },
    ],
  ]);
}

export function normalizeWordOrMathTypePaste(rawText: string): string {
  return rawText
    .replace(/\r\n/g, "\n")
    .replace(/−/g, "-")
    .replace(/×/g, "\\times ")
    .replace(/÷/g, "\\div ")
    .replace(/≤/g, "\\le ")
    .replace(/≥/g, "\\ge ")
    .replace(/°/g, "^\\circ ")
    .trim();
}

export function createDemoStudioContentDocument(contentId: string): StudioContentDocumentData {
  return {
    contentId,
    title:
      contentId === "cong-thuc-bayes"
        ? "Bài 19 · Công thức Bayes & Xác suất có điều kiện (SGK KNTT Toán 12)"
        : `Biên soạn Học liệu (${contentId})`,
    contentType: "ARTICLE",
    authorTenantId: "tenant-h2edu-1",
    requiresProAsset: false,
    previewPdfUrl: `/api/v1/assets/preview-${contentId}.pdf`,
    rawAssetUrl: `/api/v1/assets/raw-${contentId}.pdf`,
    blocks: [
      {
        blockOrder: 1,
        blockType: "THEORY_SUMMARY",
        markdown:
          "Hệ biến cố đầy đủ $(B_1, B_2, \\dots, B_n)$ phân hoạch không gian mẫu $\\Omega$, thỏa mãn $P(B_i) > 0$ và $\\sum P(B_i) = 1$.",
        assetIds: ["asset-bayes-tree"],
      },
      {
        blockOrder: 2,
        blockType: "FORMULA_BOX",
        markdown: "P(B_i | A) = \\frac{P(B_i) P(A | B_i)}{\\sum_{j=1}^{n} P(B_j) P(A | B_j)}",
      },
      {
        blockOrder: 3,
        blockType: "METHOD_STEPS",
        markdown:
          "Bước 1: Xác định biến cố giả thiết $A$ đã xảy ra.\nBước 2: Lập sơ đồ cây và tính xác suất toàn phần $P(A)$.",
      },
    ],
  };
}

export interface StudioQuestionDraftData {
  readonly authorTenantId: string;
  readonly requiresProAsset: boolean;
  readonly stableCode: string;
  readonly topicCode: string;
  readonly questionType:
    | "SINGLE_CHOICE"
    | "MULTIPLE_SELECT"
    | "CLUSTER_TRUE_FALSE"
    | "SHORT_NUMERIC"
    | "ESSAY"
    | "GROUP_STEM";
  readonly difficulty: "NB" | "TH" | "VD" | "VDC";
  readonly frameworkTag: string;
  readonly stemMarkdown: string;
  readonly options: readonly { readonly key: string; readonly label: string }[];
  readonly correctChoice: string;
  readonly clusterTargets: Readonly<Record<"a" | "b" | "c" | "d", boolean>>;
  readonly shortNumericAnswer: string;
  readonly solutionBlocks: readonly StudioBlockInput[];
}

export function createDemoQuestionAuthoringDraft(): StudioQuestionDraftData {
  return {
    authorTenantId: "tenant-h2edu-1",
    requiresProAsset: false,
    stableCode: "MATH12-BAYES-NEW-01",
    topicCode: "MATH.PROB.BAYES",
    questionType: "SINGLE_CHOICE",
    difficulty: "VD",
    frameworkTag: "THPTQG_2025",
    stemMarkdown:
      "Một nhà máy có 2 dây chuyền sản xuất A ($70\\%$) và B ($30\\%$). Tỉ lệ sản phẩm lỗi tương ứng là $2\\%$ và $5\\%$. Lấy ngẫu nhiên một sản phẩm bị lỗi, xác suất sản phẩm đó thuộc dây chuyền B là:",
    options: [
      { key: "A", label: "$\\frac{15}{29}$" },
      { key: "B", label: "$\\frac{14}{29}$" },
      { key: "C", label: "$0.30$" },
      { key: "D", label: "$0.05$" },
    ],
    correctChoice: "A",
    clusterTargets: { a: true, b: false, c: true, d: false },
    shortNumericAnswer: "0,52",
    solutionBlocks: [
      {
        blockOrder: 1,
        blockType: "FORMULA_BOX",
        markdown: "P(B|\\text{Lỗi}) = \\frac{0.3 \\times 0.05}{0.7 \\times 0.02 + 0.3 \\times 0.05} = \\frac{15}{29}",
      },
    ],
  };
}
