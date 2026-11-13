import { describe, expect, it } from "vitest";
import {
  SAFE_KATEX_OPTIONS,
  checkKatexSafety,
  evaluateClusterTrueFalseState,
  validateShortNumericInput,
} from "../exam-input-policy";
import {
  evaluatePublishButtonState,
  inspectAnswerDispute,
  normalizeAndClampBBox,
} from "../bbox-reviewer";
import { computeMatrixShortages, validateThptqg2025Preset } from "../matrix-builder";

describe("exam-input-policy (Workspace 1 & 2)", () => {
  it("accepts <=4 char numeric inputs and converts decimal comma to dot", () => {
    const res = validateShortNumericInput("-3,5");
    expect(res.valid).toBe(true);
    expect(res.normalizedValue).toBe("-3.5");
    expect(res.warning).toBeNull();
  });

  it("blocks thousands separator dots like 1.000 and >4 char overflow", () => {
    const thousands = validateShortNumericInput("1.000");
    expect(thousands.valid).toBe(false);
    expect(thousands.warning).toContain("hàng nghìn");

    const overflow = validateShortNumericInput("12345");
    expect(overflow.valid).toBe(false);
  });

  it("flags partial Part II True/False completion as warning", () => {
    expect(evaluateClusterTrueFalseState({})).toBe("UNANSWERED");
    expect(evaluateClusterTrueFalseState({ a: true, b: false })).toBe("PARTIAL_WARNING");
    expect(
      evaluateClusterTrueFalseState({ a: true, b: false, c: true, d: false }),
    ).toBe("COMPLETE");
  });

  it("enforces safe KaTeX config and blocks javascript/trust injection", () => {
    expect(SAFE_KATEX_OPTIONS).toEqual({ throwOnError: false, trust: false, strict: false });
    expect(checkKatexSafety("\\href{javascript:alert(1)}{click}").safe).toBe(false);
    expect(checkKatexSafety("\\frac{1}{2} + \\Delta\\varphi").safe).toBe(true);
  });
});

describe("bbox-reviewer (Workspace 3)", () => {
  it("normalizes swapped drag corners and clamps within page dimensions", () => {
    const box = normalizeAndClampBBox({ x0: 620, y0: 900, x1: -10, y1: 120 }, 595, 842);
    expect(box).toEqual({ x0: 0, y0: 120, x1: 595, y1: 842 });
  });

  it("detects DISPUTED_ANSWER only when normalized HDC vs SymPy differ", () => {
    expect(inspectAnswerDispute("0,75", "0.75").status).toBe("MATCHED");
    expect(inspectAnswerDispute("12.5", "13.5").status).toBe("DISPUTED_ANSWER");
  });

  it("keeps Publish button disabled unless VERIFIED + cleared rights + no dispute", () => {
    const blocked = evaluatePublishButtonState({
      rightsStatus: "PENDING_REVIEW",
      verificationStatus: "VERIFIED",
      isDisputed: false,
    });
    expect(blocked.disabled).toBe(true);

    const disputed = evaluatePublishButtonState({
      rightsStatus: "OWNED",
      verificationStatus: "VERIFIED",
      isDisputed: true,
    });
    expect(disputed.disabled).toBe(true);

    const ready = evaluatePublishButtonState({
      rightsStatus: "OWNED",
      verificationStatus: "VERIFIED",
      isDisputed: false,
    });
    expect(ready.disabled).toBe(false);
  });
});

describe("matrix-builder (Workspace 5)", () => {
  it("calculates shortages across matrix cells", () => {
    const report = computeMatrixShortages([
      {
        topicId: "MATH.BAYES",
        difficulty: "VD",
        questionType: "SHORT_NUMERIC",
        desiredCount: 4,
        availableCount: 2,
      },
      {
        topicId: "MATH.FUNC",
        difficulty: "NB",
        questionType: "SINGLE_CHOICE",
        desiredCount: 5,
        availableCount: 9,
      },
    ]);
    expect(report.readyToAssemble).toBe(false);
    expect(report.totalShortage).toBe(2);
    expect(report.shortages).toHaveLength(1);
    expect(report.shortages[0]?.topicId).toBe("MATH.BAYES");
  });

  it("validates THPTQG 2025+ Math (22 items, 10đ) and Natural Science (28 items, 10đ)", () => {
    const mathOk = validateThptqg2025Preset("MATH", {
      part1SingleChoice: 12,
      part2ClusterTrueFalse: 4,
      part3ShortNumeric: 6,
    });
    expect(mathOk.valid).toBe(true);
    expect(mathOk.actualTotalItems).toBe(22);
    expect(mathOk.computedTotalPoints).toBe(10);

    const physOk = validateThptqg2025Preset("NATURAL_SCIENCE", {
      part1SingleChoice: 18,
      part2ClusterTrueFalse: 4,
      part3ShortNumeric: 6,
    });
    expect(physOk.valid).toBe(true);
    expect(physOk.actualTotalItems).toBe(28);
    expect(physOk.computedTotalPoints).toBe(10);
  });
});
