import { describe, expect, it } from "vitest";
import { ExamProctorTracker } from "../exam-proctor";

describe("ExamProctorTracker anti-cheat telemetry", () => {
  it("ignores brief <300ms visibility micro-flickers", () => {
    const tracker = new ExamProctorTracker();
    tracker.recordVisibilityHidden(1000);
    tracker.recordVisibilityVisible(1150);

    const summary = tracker.getSummary();
    expect(summary.hiddenSwitchCount).toBe(0);
    expect(summary.totalHiddenMs).toBe(0);
    expect(summary.riskLevel).toBe("LOW");
    expect(summary.flagForTeacherReview).toBe(false);
    expect(summary.events).toHaveLength(0);
  });

  it("accumulates real tab switches, fullscreen exits, and clipboard lengths without raw text", () => {
    const tracker = new ExamProctorTracker();

    tracker.recordVisibilityHidden(10_000);
    tracker.recordVisibilityVisible(22_500); // 12.5s hidden
    tracker.recordFullscreenExit(25_000);
    tracker.recordClipboardAction("PASTE", 30_000, 42);

    const medium = tracker.getSummary();
    expect(medium.hiddenSwitchCount).toBe(1);
    expect(medium.totalHiddenMs).toBe(12_500);
    expect(medium.fullscreenExitCount).toBe(1);
    expect(medium.pasteAttemptCount).toBe(1);
    expect(medium.riskLevel).toBe("MEDIUM");
    expect(medium.flagForTeacherReview).toBe(true);

    // Push past 60s total hidden -> HIGH_REVIEW_REQUIRED
    tracker.recordVisibilityHidden(40_000);
    tracker.recordVisibilityVisible(90_000);

    const high = tracker.getSummary();
    expect(high.riskLevel).toBe("HIGH_REVIEW_REQUIRED");
    expect(high.totalHiddenMs).toBe(62_500);
    expect(high.events.some((e) => e.clipboardLength === 42)).toBe(true);
  });
});
