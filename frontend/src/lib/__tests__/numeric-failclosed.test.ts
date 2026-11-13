import { describe, expect, it } from "vitest";
import { buildInteractivePracticeTabs, resolveMasteryIndicator } from "../workspace1-study-hub";
import {
  evaluateDrivePairingCandidate,
  evaluateOutboxQueueHealth,
  evaluateParserDriftStatus,
  validateAdminMutationReason,
} from "../workspace6-admin-ops";
import { computeMatrixShortages, validateThptqg2025Preset, type MatrixCellSpec } from "../matrix-builder";
import {
  evaluatePublishButtonState,
  inspectAnswerDispute,
  normalizeAndClampBBox,
} from "../bbox-reviewer";
import { ExamProctorTracker } from "../exam-proctor";

const NON_FINITE = [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY];

describe("workspace1 mastery indicator", () => {
  const ok = { masteryRatio: 0.9, answeredCount: 30, standardError: 0.1 };

  it("returns INSUFFICIENT_DATA for any non-finite or negative input", () => {
    for (const bad of NON_FINITE) {
      for (const params of [
        { ...ok, standardError: bad },
        { ...ok, answeredCount: bad },
        { ...ok, masteryRatio: bad },
      ]) {
        const res = resolveMasteryIndicator(params);
        expect(res.status).toBe("INSUFFICIENT_DATA");
        expect(res.percent).toBeNull();
        expect(res.color).toBe("GRAY");
      }
    }
    expect(resolveMasteryIndicator({ ...ok, standardError: -0.1 }).status).toBe("INSUFFICIENT_DATA");
  });

  it("applies the 20-answer and 0.35 standard-error boundaries exactly", () => {
    expect(resolveMasteryIndicator({ ...ok, answeredCount: 19 }).status).toBe("INSUFFICIENT_DATA");
    expect(resolveMasteryIndicator({ ...ok, answeredCount: 20 }).status).toBe("ESTIMATED");
    expect(resolveMasteryIndicator({ ...ok, standardError: 0.35 }).status).toBe("INSUFFICIENT_DATA");
    expect(resolveMasteryIndicator({ ...ok, standardError: 0.3499 }).status).toBe("ESTIMATED");
  });

  it("applies the 80/50 thresholds to the unrounded value", () => {
    const at = (masteryRatio: number) => resolveMasteryIndicator({ ...ok, masteryRatio });
    expect(at(0.795)).toMatchObject({ color: "YELLOW", percent: 79 });
    expect(at(0.79999)).toMatchObject({ color: "YELLOW", percent: 79 });
    expect(at(0.8)).toMatchObject({ color: "GREEN", percent: 80 });
    expect(at(0.495)).toMatchObject({ color: "RED", percent: 49 });
    expect(at(0.49999)).toMatchObject({ color: "RED", percent: 49 });
    expect(at(0.5)).toMatchObject({ color: "YELLOW", percent: 50 });
  });

  it("clamps ratios outside 0..1", () => {
    expect(resolveMasteryIndicator({ ...ok, masteryRatio: 7 })).toMatchObject({ color: "GREEN", percent: 100 });
    expect(resolveMasteryIndicator({ ...ok, masteryRatio: -2 })).toMatchObject({ color: "RED", percent: 0 });
  });

  it("builds practice tabs with non-finite and negative counts disabled", () => {
    const tabs = buildInteractivePracticeTabs({
      ESSAY: Number.NaN,
      SINGLE_CHOICE: Number.POSITIVE_INFINITY,
      CLUSTER_TRUE_FALSE: -4,
      SHORT_NUMERIC: 2.9,
    });
    expect(tabs.map((t) => t.count)).toEqual([0, 0, 0, 2]);
    expect(tabs.map((t) => t.enabled)).toEqual([false, false, false, true]);
    expect(buildInteractivePracticeTabs({}).every((t) => !t.enabled && t.count === 0)).toBe(true);
    expect(buildInteractivePracticeTabs({ ESSAY: "5" as unknown as number })[0]?.count).toBe(0);
  });
});

describe("workspace6 admin reason", () => {
  it("ignores zero-width and invisible characters for the 15-character minimum", () => {
    expect(validateAdminMutationReason("REPLAY_DLQ", "​​ ﻿").errorCode).toBe("REASON_REQUIRED");
    const padded = "a​b​c​d​e​f​g​h​i​j​k​l​m​n";
    const short = validateAdminMutationReason("REPLAY_DLQ", padded);
    expect(short.ok).toBe(false);
    expect(short.errorCode).toBe("REASON_TOO_SHORT");
    expect(short.charLength).toBe(14);
    expect(validateAdminMutationReason("REPLAY_DLQ", `${padded}o`).ok).toBe(true);
  });

  it("handles boundaries, whitespace collapsing, astral characters and bad input", () => {
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "").errorCode).toBe("REASON_REQUIRED");
    expect(validateAdminMutationReason("OVERRIDE_GRADE", " \t\n ").errorCode).toBe("REASON_REQUIRED");
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "x".repeat(14)).ok).toBe(false);
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "x".repeat(15)).ok).toBe(true);
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "a   b\n\nc").normalizedReason).toBe("a b c");
    expect(validateAdminMutationReason("OVERRIDE_GRADE", "😀".repeat(15)).ok).toBe(true);
    expect(validateAdminMutationReason("OVERRIDE_GRADE", undefined as unknown as string).errorCode).toBe(
      "REASON_REQUIRED",
    );
  });
});

describe("workspace6 drive pairing", () => {
  const strong = {
    filenameSimilarity: 1,
    sameFolder: true,
    titleOverlap: 1,
    examQuestionCount: 22,
    keyQuestionCount: 22,
  };

  it("never confirms a pair whose question counts differ", () => {
    const res = evaluateDrivePairingCandidate({ ...strong, keyQuestionCount: 21 });
    expect(res.warningCodes).toContain("QUESTION_COUNT_MISMATCH");
    expect(res.confidencePct).toBeGreaterThanOrEqual(90);
    expect(res.canConfirm).toBe(false);
    expect(res.highConfidence).toBe(false);
  });

  it("confirms a perfect pair and treats clean boundary values correctly", () => {
    expect(evaluateDrivePairingCandidate(strong)).toMatchObject({
      confidencePct: 100,
      canConfirm: true,
      highConfidence: true,
      warningCodes: [],
    });
  });

  it("applies the 50% threshold to the unrounded confidence", () => {
    // 0.25 (same folder) + 0.35 * 0.7029 = 49.6015% -> rounds to 50 but must not confirm.
    const res = evaluateDrivePairingCandidate({
      filenameSimilarity: 0.7029,
      sameFolder: true,
      titleOverlap: 0,
      examQuestionCount: 0,
      keyQuestionCount: 0,
    });
    expect(res.confidencePct).toBe(50);
    expect(res.canConfirm).toBe(false);
  });

  it("treats non-finite counts and similarities as missing/zero", () => {
    for (const bad of NON_FINITE) {
      const res = evaluateDrivePairingCandidate({ ...strong, examQuestionCount: bad });
      expect(res.warningCodes).toContain("QUESTION_COUNT_MISSING");
      expect(Number.isFinite(res.confidencePct)).toBe(true);
      const res2 = evaluateDrivePairingCandidate({ ...strong, keyQuestionCount: bad, filenameSimilarity: bad });
      expect(Number.isFinite(res2.confidencePct)).toBe(true);
      expect(res2.highConfidence).toBe(false);
    }
  });
});

describe("workspace6 parser drift", () => {
  it("never reports HEALTHY for non-finite telemetry", () => {
    for (const bad of NON_FINITE) {
      expect(evaluateParserDriftStatus(bad, 40).status).toBe("WATCH");
      expect(evaluateParserDriftStatus(5, bad).status).toBe("WATCH");
      expect(evaluateParserDriftStatus(bad, bad).shouldAutoPause).toBe(false);
    }
  });

  it("pauses only with 30+ pages and a rate above 15%", () => {
    expect(evaluateParserDriftStatus(0, 0)).toEqual({ status: "HEALTHY", emptyRatePct: 0, shouldAutoPause: false });
    expect(evaluateParserDriftStatus(5, -3).status).toBe("HEALTHY");
    expect(evaluateParserDriftStatus(15, 29).status).toBe("WATCH");
    expect(evaluateParserDriftStatus(5, 30)).toMatchObject({ status: "PAUSED_DRIFT", shouldAutoPause: true });
    expect(evaluateParserDriftStatus(4, 30).status).toBe("HEALTHY");
    expect(evaluateParserDriftStatus(9, 60).status).toBe("HEALTHY");
    expect(evaluateParserDriftStatus(-5, 40).emptyRatePct).toBe(0);
    expect(evaluateParserDriftStatus(500, 40).emptyRatePct).toBe(100);
  });
});

describe("workspace6 outbox health", () => {
  it("treats a non-finite queue age as an SLO breach and a non-finite DLQ size as non-empty", () => {
    for (const bad of NON_FINITE) {
      const res = evaluateOutboxQueueHealth(bad, 0);
      expect(res.status).toBe("SLO_BREACH");
      expect(res.alertCodes).toContain("INGESTION_QUEUE_AGE_UNKNOWN");
      expect(evaluateOutboxQueueHealth(1, bad).alertCodes).toContain("DLQ_NON_EMPTY");
    }
  });

  it("uses > 90s and > 0 boundaries", () => {
    expect(evaluateOutboxQueueHealth(90, 0)).toMatchObject({ status: "OK", alertCodes: [] });
    expect(evaluateOutboxQueueHealth(90.0001, 0).status).toBe("SLO_BREACH");
    expect(evaluateOutboxQueueHealth(-5, 0).status).toBe("OK");
    expect(evaluateOutboxQueueHealth(1, 1)).toMatchObject({ status: "OK", alertCodes: ["DLQ_NON_EMPTY"] });
  });
});

describe("matrix-builder", () => {
  const cell = (desiredCount: number, availableCount: number): MatrixCellSpec => ({
    topicId: "T",
    difficulty: "NB",
    questionType: "SINGLE_CHOICE",
    desiredCount,
    availableCount,
  });

  it("is not ready when any count is non-finite or negative", () => {
    for (const bad of [...NON_FINITE, -1]) {
      const a = computeMatrixShortages([cell(3, 3), cell(4, bad)]);
      expect(a.readyToAssemble).toBe(false);
      expect(a.invalidCellCount).toBe(1);
      expect(a.totalShortage).toBe(4);
      const d = computeMatrixShortages([cell(bad, 3)]);
      expect(d.readyToAssemble).toBe(false);
      expect(d.invalidCellCount).toBe(1);
    }
  });

  it("covers empty input, exact fit and fractional counts", () => {
    expect(computeMatrixShortages([])).toMatchObject({ readyToAssemble: false, totalDesired: 0, invalidCellCount: 0 });
    expect(computeMatrixShortages([cell(0, 5)]).readyToAssemble).toBe(false);
    expect(computeMatrixShortages([cell(3, 3)])).toMatchObject({ readyToAssemble: true, totalShortage: 0 });
    expect(computeMatrixShortages([cell(3.9, 3.1)]).readyToAssemble).toBe(true);
    expect(computeMatrixShortages([cell(5, 0)]).shortages[0]?.shortage).toBe(5);
  });

  it("rejects unknown subject groups, non-integer and non-finite counts without throwing", () => {
    const counts = { part1SingleChoice: 12, part2ClusterTrueFalse: 4, part3ShortNumeric: 6 };
    for (const subject of ["constructor", "__proto__", "toString", "PHYSICS"]) {
      const res = validateThptqg2025Preset(subject as "MATH", counts);
      expect(res.valid).toBe(false);
      expect(res.errors).toHaveLength(1);
    }
    for (const bad of [...NON_FINITE, -1, 12.5]) {
      const res = validateThptqg2025Preset("MATH", { ...counts, part1SingleChoice: bad });
      expect(res.valid).toBe(false);
      expect(res.errors.length).toBeGreaterThan(0);
      expect(Number.isFinite(res.computedTotalPoints)).toBe(true);
    }
    expect(validateThptqg2025Preset("MATH", { ...counts, part3ShortNumeric: 5 }).valid).toBe(false);
    expect(
      validateThptqg2025Preset("NATURAL_SCIENCE", { part1SingleChoice: 18, part2ClusterTrueFalse: 4, part3ShortNumeric: 6 })
        .valid,
    ).toBe(true);
  });
});

describe("bbox-reviewer", () => {
  const box = { x0: 10, y0: 10, x1: 100, y1: 100 };

  it("throws on non-finite or non-positive page size, coordinates and minSize", () => {
    for (const bad of [...NON_FINITE, 0, -5]) {
      expect(() => normalizeAndClampBBox(box, bad, 800)).toThrow();
      expect(() => normalizeAndClampBBox(box, 600, bad)).toThrow();
    }
    for (const bad of NON_FINITE) {
      expect(() => normalizeAndClampBBox({ ...box, x0: bad }, 600, 800)).toThrow(/finite/);
      expect(() => normalizeAndClampBBox({ ...box, y1: bad }, 600, 800)).toThrow(/finite/);
      expect(() => normalizeAndClampBBox(box, 600, 800, bad)).toThrow();
    }
    expect(() => normalizeAndClampBBox(box, 600, 800, -1)).toThrow();
  });

  it("expands degenerate boxes to minSize, also at the page edge", () => {
    expect(normalizeAndClampBBox({ x0: 50, y0: 50, x1: 50, y1: 50 }, 600, 800)).toEqual({
      x0: 50,
      y0: 50,
      x1: 58,
      y1: 58,
    });
    expect(normalizeAndClampBBox({ x0: 600, y0: 800, x1: 600, y1: 800 }, 600, 800)).toEqual({
      x0: 592,
      y0: 792,
      x1: 600,
      y1: 800,
    });
  });

  it("does not report two different huge integers as MATCHED", () => {
    const a = `1${"0".repeat(400)}`;
    const b = `2${"0".repeat(400)}`;
    expect(inspectAnswerDispute(a, b).status).toBe("DISPUTED_ANSWER");
    expect(inspectAnswerDispute(a, a).status).toBe("MATCHED");
    expect(inspectAnswerDispute("", "1").status).toBe("INSUFFICIENT_DATA");
    expect(inspectAnswerDispute(null, undefined).status).toBe("INSUFFICIENT_DATA");
    expect(inspectAnswerDispute(" $x^2$ ", "X^2").status).toBe("MATCHED");
    expect(inspectAnswerDispute("-0", "0").status).toBe("MATCHED");
  });

  it("keeps Publish disabled unless isDisputed is explicitly false", () => {
    const base = { rightsStatus: "OWNED", verificationStatus: "VERIFIED" } as const;
    expect(evaluatePublishButtonState({ ...base, isDisputed: undefined as unknown as boolean }).disabled).toBe(true);
    expect(evaluatePublishButtonState({ ...base, isDisputed: false }).disabled).toBe(false);
    expect(evaluatePublishButtonState({ ...base, rightsStatus: "constructor" as "OWNED", isDisputed: false }).disabled).toBe(true);
    expect(evaluatePublishButtonState({ ...base, verificationStatus: "AUTO_VERIFIED", isDisputed: false }).disabled).toBe(true);
  });
});

describe("exam-proctor", () => {
  it("does not treat a still-hidden tab as LOW risk when no timestamp is given", () => {
    const t = new ExamProctorTracker();
    t.recordVisibilityHidden(1_000);
    for (const now of [undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      const s = t.getSummary(now);
      expect(s.tabHiddenNow).toBe(true);
      expect(s.riskLevel).toBe("MEDIUM");
      expect(s.flagForTeacherReview).toBe(true);
    }
    expect(t.getSummary(1_100).riskLevel).toBe("LOW");
    expect(t.getSummary(1_000 + 10_000).riskLevel).toBe("MEDIUM");
    expect(t.getSummary(1_000 + 60_000).riskLevel).toBe("HIGH_REVIEW_REQUIRED");
    t.recordVisibilityVisible(1_050);
    expect(t.getSummary().tabHiddenNow).toBe(false);
    expect(t.getSummary().riskLevel).toBe("LOW");
  });

  it("counts a hide/show pair with an invalid timestamp instead of poisoning totals with NaN", () => {
    const t = new ExamProctorTracker();
    t.recordVisibilityHidden(Number.NaN);
    t.recordVisibilityVisible(5_000);
    t.recordVisibilityHidden(100);
    t.recordVisibilityVisible(Number.NaN);
    const s = t.getSummary();
    expect(s.hiddenSwitchCount).toBe(2);
    expect(Number.isFinite(s.totalHiddenMs)).toBe(true);
    expect(s.riskLevel).toBe("MEDIUM");
    for (const ev of s.events) expect(Number.isFinite(ev.occurredAtMs)).toBe(true);
  });

  it("sanitises clipboard length and keeps counting paste attempts", () => {
    const t = new ExamProctorTracker();
    t.recordClipboardAction("PASTE", 10, Number.NaN);
    t.recordClipboardAction("COPY", Number.NaN, Number.POSITIVE_INFINITY);
    t.recordClipboardAction("PASTE", 12, -5);
    t.recordClipboardAction("PASTE", 13, 7.9);
    const s = t.getSummary();
    expect(s.events.map((e) => e.clipboardLength)).toEqual([0, 0, 0, 7]);
    expect(s.events.every((e) => Number.isFinite(e.occurredAtMs))).toBe(true);
    expect(s.pasteAttemptCount).toBe(3);
    expect(s.copyAttemptCount).toBe(1);
    expect(s.riskLevel).toBe("HIGH_REVIEW_REQUIRED");
  });

  it("falls back to defaults for invalid options and honours maxStoredEvents", () => {
    const t = new ExamProctorTracker({ minHiddenDurationMs: Number.NaN, maxStoredEvents: Number.NaN });
    t.recordVisibilityHidden(0);
    t.recordVisibilityVisible(299);
    expect(t.getSummary().hiddenSwitchCount).toBe(0);
    t.recordVisibilityHidden(1_000);
    t.recordVisibilityVisible(1_300);
    expect(t.getSummary().hiddenSwitchCount).toBe(1);

    const capped = new ExamProctorTracker({ maxStoredEvents: 2 });
    for (let i = 0; i < 5; i += 1) capped.recordFullscreenExit(i);
    expect(capped.getSummary().events).toHaveLength(2);
    expect(capped.getSummary().fullscreenExitCount).toBe(5);
    expect(new ExamProctorTracker({ maxStoredEvents: 0 }).getSummary().events).toEqual([]);
  });

  it("applies the documented risk thresholds", () => {
    const hidden = (n: number) => {
      const t = new ExamProctorTracker();
      for (let i = 0; i < n; i += 1) {
        t.recordVisibilityHidden(i * 1_000);
        t.recordVisibilityVisible(i * 1_000 + 400);
      }
      return t.getSummary().riskLevel;
    };
    expect(hidden(1)).toBe("LOW");
    expect(hidden(2)).toBe("MEDIUM");
    expect(hidden(4)).toBe("MEDIUM");
    expect(hidden(5)).toBe("HIGH_REVIEW_REQUIRED");

    const fs = new ExamProctorTracker();
    fs.recordFullscreenExit(1);
    expect(fs.getSummary().riskLevel).toBe("LOW");
    fs.recordFullscreenExit(2);
    expect(fs.getSummary().riskLevel).toBe("MEDIUM");

    const longHide = new ExamProctorTracker();
    longHide.recordVisibilityHidden(0);
    longHide.recordVisibilityVisible(60_000);
    expect(longHide.getSummary().riskLevel).toBe("HIGH_REVIEW_REQUIRED");
    // ignoring a second hidden call while already hidden / a visible call while visible
    longHide.recordVisibilityVisible(70_000);
    expect(longHide.getSummary().hiddenSwitchCount).toBe(1);
  });
});
