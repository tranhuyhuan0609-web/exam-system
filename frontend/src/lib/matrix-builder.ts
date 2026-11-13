/**
 * Workspace 5 Matrix Exam Builder pure helpers:
 * - Real-time ShortageReport over Topic x Difficulty x QuestionType matrix
 * - THPTQG 2025+ Math (22 items / 10 pts) & Natural Science (28 items / 10 pts) preset validation
 */

export type CognitiveDifficulty = "NB" | "TH" | "VD" | "VDC";
export type BlueprintItemType = "SINGLE_CHOICE" | "CLUSTER_TRUE_FALSE" | "SHORT_NUMERIC";
export type SubjectGroup2025 = "MATH" | "NATURAL_SCIENCE";

export interface MatrixCellSpec {
  topicId: string;
  difficulty: CognitiveDifficulty;
  questionType: BlueprintItemType;
  desiredCount: number;
  availableCount: number;
}

export interface ShortageCell {
  topicId: string;
  difficulty: CognitiveDifficulty;
  questionType: BlueprintItemType;
  desiredCount: number;
  availableCount: number;
  shortage: number;
}

export interface MatrixShortageReport {
  readyToAssemble: boolean;
  totalDesired: number;
  totalShortage: number;
  shortages: ShortageCell[];
}

export function computeMatrixShortages(cells: MatrixCellSpec[]): MatrixShortageReport {
  const shortages: ShortageCell[] = [];
  let totalDesired = 0;
  let totalShortage = 0;

  for (const cell of cells) {
    const desired = Math.max(0, Math.trunc(cell.desiredCount));
    const available = Math.max(0, Math.trunc(cell.availableCount));
    totalDesired += desired;
    const shortage = Math.max(0, desired - available);
    if (shortage > 0) {
      totalShortage += shortage;
      shortages.push({
        topicId: cell.topicId,
        difficulty: cell.difficulty,
        questionType: cell.questionType,
        desiredCount: desired,
        availableCount: available,
        shortage,
      });
    }
  }

  return {
    readyToAssemble: totalShortage === 0 && totalDesired > 0,
    totalDesired,
    totalShortage,
    shortages,
  };
}

export interface ThptqgPresetCounts {
  part1SingleChoice: number;
  part2ClusterTrueFalse: number;
  part3ShortNumeric: number;
}

export interface ThptqgPresetValidation {
  valid: boolean;
  expectedTotalItems: number;
  actualTotalItems: number;
  computedTotalPoints: number;
  errors: string[];
}

const PRESET_EXPECTATIONS: Record<
  SubjectGroup2025,
  { p1: number; p1Pts: number; p2: number; p2Pts: number; p3: number; p3Pts: number }
> = {
  MATH: { p1: 12, p1Pts: 0.25, p2: 4, p2Pts: 1.0, p3: 6, p3Pts: 0.5 },
  NATURAL_SCIENCE: { p1: 18, p1Pts: 0.25, p2: 4, p2Pts: 1.0, p3: 6, p3Pts: 0.25 },
};

export function validateThptqg2025Preset(
  subjectGroup: SubjectGroup2025,
  counts: ThptqgPresetCounts,
): ThptqgPresetValidation {
  const exp = PRESET_EXPECTATIONS[subjectGroup];
  const errors: string[] = [];

  if (counts.part1SingleChoice !== exp.p1) {
    errors.push(`Phần I yêu cầu ${exp.p1} câu ABCD (nhập ${counts.part1SingleChoice})`);
  }
  if (counts.part2ClusterTrueFalse !== exp.p2) {
    errors.push(`Phần II yêu cầu ${exp.p2} câu Đúng/Sai 4 ý (nhập ${counts.part2ClusterTrueFalse})`);
  }
  if (counts.part3ShortNumeric !== exp.p3) {
    errors.push(`Phần III yêu cầu ${exp.p3} câu Trả lời ngắn (nhập ${counts.part3ShortNumeric})`);
  }

  const computedTotalPoints = Number(
    (
      counts.part1SingleChoice * exp.p1Pts +
      counts.part2ClusterTrueFalse * exp.p2Pts +
      counts.part3ShortNumeric * exp.p3Pts
    ).toFixed(4),
  );

  return {
    valid: errors.length === 0 && Math.abs(computedTotalPoints - 10.0) < 1e-4,
    expectedTotalItems: exp.p1 + exp.p2 + exp.p3,
    actualTotalItems:
      counts.part1SingleChoice + counts.part2ClusterTrueFalse + counts.part3ShortNumeric,
    computedTotalPoints,
    errors,
  };
}
