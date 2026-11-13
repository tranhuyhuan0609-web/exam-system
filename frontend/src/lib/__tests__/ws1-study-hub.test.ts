import { describe, expect, it } from "vitest";
import {
  KATEX_SAFE_RENDER_OPTIONS,
  scanUnsafeLatexCommands,
} from "../katex-security";
import {
  buildInteractivePracticeTabs,
  resolveMasteryIndicator,
} from "../workspace1-study-hub";

describe("Workspace 1 KaTeX security", () => {
  it("enforces trust:false and throwOnError:false", () => {
    expect(KATEX_SAFE_RENDER_OPTIONS.trust).toBe(false);
    expect(KATEX_SAFE_RENDER_OPTIONS.throwOnError).toBe(false);
    expect(KATEX_SAFE_RENDER_OPTIONS.maxExpand).toBe(100);
  });

  it("blocks HTML/JS injection directives while allowing valid math/chem formulas", () => {
    expect(
      scanUnsafeLatexCommands("\\frac{P(A|B)P(B)}{P(A)} + \\text{H}_2\\text{SO}_4").safe,
    ).toBe(true);

    const xss1 = scanUnsafeLatexCommands("\\htmlData{xss=1}{x} + \\href{javascript:alert(1)}{click}");
    expect(xss1.safe).toBe(false);
    expect(xss1.violations).toContain("KATEX_DOM_INJECTION_MACRO");
    expect(xss1.violations).toContain("KATEX_JAVASCRIPT_OR_DATA_URI");
  });
});

describe("Workspace 1 mastery dot colors & 4-tab practice builder", () => {
  it("returns INSUFFICIENT_DATA when <20 questions or SE >= 0.35 (D06 rule)", () => {
    expect(
      resolveMasteryIndicator({ masteryRatio: 0.92, answeredCount: 19, standardError: 0.2 }),
    ).toMatchObject({ status: "INSUFFICIENT_DATA", color: "GRAY", percent: null });

    expect(
      resolveMasteryIndicator({ masteryRatio: 0.85, answeredCount: 30, standardError: 0.35 }),
    ).toMatchObject({ status: "INSUFFICIENT_DATA", color: "GRAY", percent: null });
  });

  it("maps mastery ratios >=80 GREEN, 50-79 YELLOW, <50 RED", () => {
    expect(
      resolveMasteryIndicator({ masteryRatio: 0.84, answeredCount: 25, standardError: 0.25 }).color,
    ).toBe("GREEN");
    expect(
      resolveMasteryIndicator({ masteryRatio: 0.65, answeredCount: 25, standardError: 0.25 }).color,
    ).toBe("YELLOW");
    expect(
      resolveMasteryIndicator({ masteryRatio: 0.42, answeredCount: 25, standardError: 0.25 }).color,
    ).toBe("RED");
  });

  it("builds the canonical 4 interactive practice tabs below lessons", () => {
    const tabs = buildInteractivePracticeTabs({
      ESSAY: 8,
      SINGLE_CHOICE: 24,
      CLUSTER_TRUE_FALSE: 13,
      SHORT_NUMERIC: 0,
    });
    expect(tabs).toHaveLength(4);
    expect(tabs[0]).toMatchObject({ type: "ESSAY", count: 8, enabled: true });
    expect(tabs[3]).toMatchObject({ type: "SHORT_NUMERIC", count: 0, enabled: false });
  });
});
