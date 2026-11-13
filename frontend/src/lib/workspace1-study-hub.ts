/**
 * Workspace 1 (Learner Study & Practice Hub) curriculum mastery indicator
 * and 4-tab VietJack interactive exercise model.
 */

export type MasteryColor = "GREEN" | "YELLOW" | "RED" | "GRAY";

export interface MasteryIndicator {
  readonly status: "ESTIMATED" | "INSUFFICIENT_DATA";
  readonly color: MasteryColor;
  readonly percent: number | null;
  readonly label: string;
}

/**
 * V11.3 Chapter 3.4 D06 & Chapter 3.8B Workspace 1 rules:
 * - If answeredCount < 20 or standardError >= 0.35 -> INSUFFICIENT_DATA (never fabricate score)
 * - >= 80%: GREEN, 50-79%: YELLOW, < 50%: RED
 */
export function resolveMasteryIndicator(params: {
  readonly masteryRatio: number;
  readonly answeredCount: number;
  readonly standardError: number;
}): MasteryIndicator {
  if (params.answeredCount < 20 || params.standardError >= 0.35) {
    return {
      status: "INSUFFICIENT_DATA",
      color: "GRAY",
      percent: null,
      label: "Chưa đủ dữ liệu (< 20 câu)",
    };
  }

  const clamped = Math.max(0, Math.min(1, params.masteryRatio));
  const percent = Math.round(clamped * 100);
  if (percent >= 80) {
    return {
      status: "ESTIMATED",
      color: "GREEN",
      percent,
      label: `Làm chủ tốt (${percent}%)`,
    };
  }
  if (percent >= 50) {
    return {
      status: "ESTIMATED",
      color: "YELLOW",
      percent,
      label: `Cần củng cố (${percent}%)`,
    };
  }
  return {
    status: "ESTIMATED",
    color: "RED",
    percent,
    label: `Cần ôn tập lại (${percent}%)`,
  };
}

export type PracticeTabType =
  | "ESSAY"
  | "SINGLE_CHOICE"
  | "CLUSTER_TRUE_FALSE"
  | "SHORT_NUMERIC";

export interface PracticeTabSpec {
  readonly type: PracticeTabType;
  readonly label: string;
  readonly count: number;
  readonly enabled: boolean;
}

const CANONICAL_TAB_ORDER: readonly { readonly type: PracticeTabType; readonly label: string }[] = [
  { type: "ESSAY", label: "Tự luận" },
  { type: "SINGLE_CHOICE", label: "Trắc nghiệm ABCD" },
  { type: "CLUSTER_TRUE_FALSE", label: "Đúng/Sai 4 ý" },
  { type: "SHORT_NUMERIC", label: "Trả lời ngắn" },
];

/**
 * Builds the ordered 4 interactive practice tabs below each lesson in Workspace 1.
 */
export function buildInteractivePracticeTabs(
  countsByType: Partial<Record<PracticeTabType, number>>,
): readonly PracticeTabSpec[] {
  return CANONICAL_TAB_ORDER.map((item) => {
    const count = Math.max(0, Math.floor(countsByType[item.type] ?? 0));
    return {
      type: item.type,
      label: item.label,
      count,
      enabled: count > 0,
    };
  });
}
