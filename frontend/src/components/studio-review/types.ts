import type {
  BoundingBox,
  ReviewerRightsStatus,
  ReviewerVerificationStatus,
} from "@/lib/bbox-reviewer";

export type BboxRegionKind = "QUESTION" | "FIGURE_CROP" | "HTML_TABLE";

export interface BboxRegion {
  readonly id: string;
  readonly kind: BboxRegionKind;
  readonly label: string;
  readonly box: BoundingBox;
}

export interface ReviewQuestionItem {
  readonly id: string;
  readonly orderIndex: number;
  readonly pageNo: number;
  readonly stableCode: string;
  readonly questionType: "SINGLE_CHOICE" | "CLUSTER_TRUE_FALSE" | "SHORT_NUMERIC" | "ESSAY";
  readonly stemMarkdown: string;
  readonly options: readonly { readonly key: string; readonly label: string }[];
  readonly sourceAnswer: string;
  readonly sympyAnswer: string;
  readonly sympyTraceSummary: string;
  readonly rightsStatus: ReviewerRightsStatus;
  readonly verificationStatus: ReviewerVerificationStatus;
  readonly regions: readonly BboxRegion[];
}

export interface ReviewDocumentPage {
  readonly pageNo: number;
  readonly width: number;
  readonly height: number;
  readonly heading: string;
  readonly previewLines: readonly string[];
}

export interface ImportReviewSessionData {
  readonly runId: string;
  readonly documentTitle: string;
  readonly sourceName: string;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string | null;
  readonly rawAssetUrl: string | null;
  readonly pages: readonly ReviewDocumentPage[];
  readonly items: readonly ReviewQuestionItem[];
}

export const BBOX_REGION_COLORS: Readonly<
  Record<BboxRegionKind, { readonly stroke: string; readonly fill: string; readonly badge: string; readonly label: string }>
> = {
  QUESTION: {
    stroke: "#2563eb",
    fill: "rgba(37, 99, 235, 0.12)",
    badge: "bg-blue-100 text-blue-800 border-blue-300",
    label: "Câu hỏi (QUESTION)",
  },
  FIGURE_CROP: {
    stroke: "#ea580c",
    fill: "rgba(234, 88, 12, 0.14)",
    badge: "bg-orange-100 text-orange-800 border-orange-300",
    label: "Hình cắt (FIGURE_CROP)",
  },
  HTML_TABLE: {
    stroke: "#9333ea",
    fill: "rgba(147, 51, 234, 0.12)",
    badge: "bg-purple-100 text-purple-800 border-purple-300",
    label: "Bảng biểu (HTML_TABLE)",
  },
};

export function createDemoImportReviewSession(runId: string): ImportReviewSessionData {
  return {
    runId,
    documentTitle: `Đề kiểm tra Chuyên đề Xác suất Bayes & Vật lí (${runId})`,
    sourceName: "So_GDDT_HaNoi_Toan_2025_Scan.pdf",
    requiresProAsset: false,
    previewPdfUrl: `/api/v1/assets/preview-${runId}.pdf`,
    rawAssetUrl: `/api/v1/assets/raw-${runId}.pdf`,
    pages: [
      {
        pageNo: 1,
        width: 595,
        height: 842,
        heading: "TRANG 1 / 2 · ĐỀ GỐC ĐÃ QUÉT OCR (300 DPI)",
        previewLines: [
          "Câu 1: Một lô sản phẩm do hai máy I và II sản xuất với tỉ lệ 60% và 40%. Tìm P(I | phế phẩm).",
          "[Sơ đồ cây xác suất có điều kiện — FIGURE_CROP]",
          "Câu 2: Cho hàm số f(x) = x^3 - 3x + 2. Bảng biến thiên bên dưới:",
          "[Bảng xét dấu f'(x) — HTML_TABLE]",
        ],
      },
      {
        pageNo: 2,
        width: 595,
        height: 842,
        heading: "TRANG 2 / 2 · PHẦN TRẢ LỜI NGẮN & TỰ LUẬN",
        previewLines: [
          "Câu 3: Một con lắc lò xo dao động điều hòa với tần số góc ω = 10 rad/s, biên độ A = 5 cm.",
          "[Đồ thị li độ x(t) — FIGURE_CROP]",
        ],
      },
    ],
    items: [
      {
        id: "cand-q1",
        orderIndex: 1,
        pageNo: 1,
        stableCode: "IMP-2025-001",
        questionType: "SHORT_NUMERIC",
        stemMarkdown:
          "Hai phân xưởng I và II lần lượt chiếm $60\\%$ và $40\\%$ sản lượng. Tỉ lệ phế phẩm tương ứng là $1\\%$ và $3\\%$. Chọn ngẫu nhiên một phế phẩm, tính xác suất nó do phân xưởng I sản xuất ($P(A|B)$).",
        options: [],
        sourceAnswer: "0.42",
        sympyAnswer: "0.33",
        sympyTraceSummary:
          "SymPy: (0.6*0.01) / (0.6*0.01 + 0.4*0.03) = 0.006 / 0.018 = 1/3 ≈ 0.33 (HDC gốc ghi nhầm 0.42).",
        rightsStatus: "PENDING_REVIEW",
        verificationStatus: "DISPUTED",
        regions: [
          {
            id: "reg-q1-stem",
            kind: "QUESTION",
            label: "Câu 1 · Văn bản",
            box: { x0: 48, y0: 96, x1: 545, y1: 245 },
          },
          {
            id: "reg-q1-fig",
            kind: "FIGURE_CROP",
            label: "Hình 1 · Sơ đồ cây Bayes",
            box: { x0: 84, y0: 258, x1: 420, y1: 410 },
          },
        ],
      },
      {
        id: "cand-q2",
        orderIndex: 2,
        pageNo: 1,
        stableCode: "IMP-2025-002",
        questionType: "SINGLE_CHOICE",
        stemMarkdown: "Giá trị cực tiểu của hàm số $y = x^3 - 3x + 2$ trên $\\mathbb{R}$ bằng bao nhiêu?",
        options: [
          { key: "A", label: "$4$" },
          { key: "B", label: "$0$" },
          { key: "C", label: "$-2$" },
          { key: "D", label: "$2$" },
        ],
        sourceAnswer: "B",
        sympyAnswer: "B",
        sympyTraceSummary: "SymPy: y'(x)=3x^2-3=0 tại x=±1; f(1)=0 là cực tiểu -> B.",
        rightsStatus: "RESTRICTED",
        verificationStatus: "AUTO_VERIFIED",
        regions: [
          {
            id: "reg-q2-stem",
            kind: "QUESTION",
            label: "Câu 2 · Văn bản",
            box: { x0: 48, y0: 450, x1: 545, y1: 580 },
          },
          {
            id: "reg-q2-table",
            kind: "HTML_TABLE",
            label: "Bảng biến thiên HTML_TABLE",
            box: { x0: 64, y0: 590, x1: 510, y1: 725 },
          },
        ],
      },
      {
        id: "cand-q3",
        orderIndex: 3,
        pageNo: 2,
        stableCode: "IMP-2025-003",
        questionType: "SHORT_NUMERIC",
        stemMarkdown:
          "Vật dao động điều hòa $x = 5\\cos(10t)\\text{ (cm)}$. Tính tốc độ tại vị trí $x = 3\\text{ cm}$ (theo đơn vị $\\text{cm/s}$).",
        options: [],
        sourceAnswer: "40",
        sympyAnswer: "40",
        sympyTraceSummary: "SymPy: v = ω * sqrt(A^2 - x^2) = 10 * sqrt(25 - 9) = 40.",
        rightsStatus: "OWNED",
        verificationStatus: "VERIFIED",
        regions: [
          {
            id: "reg-q3-stem",
            kind: "QUESTION",
            label: "Câu 3 · Văn bản",
            box: { x0: 52, y0: 110, x1: 540, y1: 250 },
          },
          {
            id: "reg-q3-fig",
            kind: "FIGURE_CROP",
            label: "Hình 3 · Đồ thị x(t)",
            box: { x0: 96, y0: 270, x1: 480, y1: 455 },
          },
        ],
      },
    ],
  };
}
