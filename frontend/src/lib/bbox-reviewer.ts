/**
 * Workspace 3 Side-by-Side Bounding-Box Reviewer pure helpers:
 * - Drag re-crop bbox normalization & page boundary clamping
 * - HDC vs SymPy answer dispute detector (DISPUTED_ANSWER)
 * - Fail-closed Publish button state evaluator
 */

export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function normalizeAndClampBBox(
  box: BoundingBox,
  pageWidth: number,
  pageHeight: number,
  minSize = 8,
): BoundingBox {
  if (!(Number.isFinite(pageWidth) && pageWidth > 0 && Number.isFinite(pageHeight) && pageHeight > 0)) {
    throw new Error("pageWidth and pageHeight must be positive");
  }
  if (![box.x0, box.y0, box.x1, box.y1].every(Number.isFinite)) {
    throw new Error("bounding box coordinates must be finite numbers");
  }
  if (!Number.isFinite(minSize) || minSize < 0) {
    throw new Error("minSize must be a non-negative finite number");
  }
  const minX = Math.max(0, Math.min(pageWidth, Math.min(box.x0, box.x1)));
  const maxX = Math.max(0, Math.min(pageWidth, Math.max(box.x0, box.x1)));
  const minY = Math.max(0, Math.min(pageHeight, Math.min(box.y0, box.y1)));
  const maxY = Math.max(0, Math.min(pageHeight, Math.max(box.y0, box.y1)));

  let x0 = minX;
  let x1 = maxX;
  if (x1 - x0 < minSize) {
    x1 = Math.min(pageWidth, x0 + minSize);
    if (x1 - x0 < minSize) x0 = Math.max(0, x1 - minSize);
  }

  let y0 = minY;
  let y1 = maxY;
  if (y1 - y0 < minSize) {
    y1 = Math.min(pageHeight, y0 + minSize);
    if (y1 - y0 < minSize) y0 = Math.max(0, y1 - minSize);
  }

  return { x0, y0, x1, y1 };
}

function normalizeAnswerForComparison(raw: string): string {
  const trimmed = raw.trim().replace(/^\$+|\$+$/g, "").trim();
  // Normalize decimal comma if simple numeric token
  // Only collapse to a number when it stays finite: "9"x400 would otherwise become "Infinity" for both
  // sides and two different answers would be reported as MATCHED.
  if (/^-?\d+,\d+$/.test(trimmed)) {
    const n = Number(trimmed.replace(",", "."));
    if (Number.isFinite(n)) return String(n);
  } else if (/^-?\d+(\.\d+)?$/.test(trimmed)) {
    const n = Number(trimmed);
    if (Number.isFinite(n)) return String(n);
  }
  return trimmed.toUpperCase();
}

export interface AnswerDisputeResult {
  status: "MATCHED" | "DISPUTED_ANSWER" | "INSUFFICIENT_DATA";
  normalizedSource: string;
  normalizedSympy: string;
}

export function inspectAnswerDispute(
  sourceAnswer: string | null | undefined,
  sympyAnswer: string | null | undefined,
): AnswerDisputeResult {
  const s = (sourceAnswer ?? "").trim();
  const y = (sympyAnswer ?? "").trim();
  if (!s || !y) {
    return { status: "INSUFFICIENT_DATA", normalizedSource: s, normalizedSympy: y };
  }
  const ns = normalizeAnswerForComparison(s);
  const ny = normalizeAnswerForComparison(y);
  return {
    status: ns === ny ? "MATCHED" : "DISPUTED_ANSWER",
    normalizedSource: ns,
    normalizedSympy: ny,
  };
}

export type ReviewerRightsStatus =
  | "PENDING_REVIEW"
  | "OWNED"
  | "LICENSED"
  | "ALLOWED_DERIVATIVE"
  | "RESTRICTED"
  | "TAKEDOWN";

export type ReviewerVerificationStatus =
  | "UNCHECKED"
  | "AUTO_VERIFIED"
  | "VERIFIED"
  | "DISPUTED"
  | "REJECTED";

export interface PublishButtonState {
  disabled: boolean;
  reasons: string[];
}

const ALLOWED_PUBLISH_RIGHTS = new Set<ReviewerRightsStatus>([
  "OWNED",
  "LICENSED",
  "ALLOWED_DERIVATIVE",
]);

export function evaluatePublishButtonState(params: {
  rightsStatus: ReviewerRightsStatus;
  verificationStatus: ReviewerVerificationStatus;
  isDisputed: boolean;
}): PublishButtonState {
  const reasons: string[] = [];

  if (!ALLOWED_PUBLISH_RIGHTS.has(params.rightsStatus)) {
    reasons.push(`Bản quyền (${params.rightsStatus}) chưa đủ điều kiện xuất bản`);
  }
  if (params.verificationStatus !== "VERIFIED") {
    reasons.push(`Chuyên môn cần đạt VERIFIED (hiện tại: ${params.verificationStatus})`);
  }
  // Fail closed: only an explicit `false` means "not disputed".
  if (params.isDisputed !== false) {
    reasons.push("Cần xử lý nhãn DISPUTED_ANSWER trước khi Publish");
  }

  return {
    disabled: reasons.length > 0,
    reasons,
  };
}
