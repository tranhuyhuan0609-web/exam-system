import { describe, expect, it } from "vitest";
import {
  lintKatexMarkdown,
  lintStudioDocument,
  resolveSlashCommand,
  type ClientAssetPermissionEntry,
} from "../block-studio-linter";

describe("block-studio-linter", () => {
  it("resolves VietJack slash commands accurately", () => {
    expect(resolveSlashCommand("/congthuc")).toBe("FORMULA_BOX");
    expect(resolveSlashCommand("/phuongphap")).toBe("METHOD_STEPS");
    expect(resolveSlashCommand("/chuy")).toBe("TIP_WARNING");
    expect(resolveSlashCommand("/vidu")).toBe("WORKED_EXAMPLE");
    expect(resolveSlashCommand("/tomtat")).toBe("THEORY_SUMMARY");
    expect(resolveSlashCommand("/cauhoi")).toBe("INTERACTIVE_QUESTION_EMBED");
    expect(resolveSlashCommand("/unknown")).toBeNull();
  });

  it("catches unclosed KaTeX delimiters, braces, and environments without false positives on escapes", () => {
    expect(lintKatexMarkdown("Giá \\$100 và công thức $f(x) = \\frac{1}{x}$ và $$x^2$$")).toEqual([]);
    expect(lintKatexMarkdown("\\(\\begin{cases} x=1 \\\\ y=2 \\end{cases}\\)")).toEqual([]);

    expect(lintKatexMarkdown("Thiếu $x + 1")[0]?.code).toBe("UNCLOSED_KATEX_INLINE");
    expect(lintKatexMarkdown("Thiếu $$x^2")[0]?.code).toBe("UNCLOSED_KATEX_DISPLAY");
    expect(lintKatexMarkdown("Sai ngoặc $\\frac{1}{2$")[0]?.code).toBe("UNBALANCED_KATEX_BRACE");
    expect(lintKatexMarkdown("Lệch env $\\begin{aligned} a=1 \\end{cases}$")[0]?.code).toBe(
      "MISMATCHED_KATEX_ENVIRONMENT",
    );
  });

  it("blocks cross-tenant assets and missing question embeds in Studio preview", () => {
    const catalog = new Map<string, ClientAssetPermissionEntry>([
      ["a-own", { assetId: "a-own", tenantId: "tenant-1", visibilityScope: "TENANT_PRIVATE" }],
      ["a-pub", { assetId: "a-pub", tenantId: "sys", visibilityScope: "SYSTEM_PUBLIC" }],
      ["a-other", { assetId: "a-other", tenantId: "tenant-2", visibilityScope: "TENANT_PRIVATE" }],
    ]);

    const okReport = lintStudioDocument(
      [
        { blockOrder: 0, blockType: "FORMULA_BOX", markdown: "$E = mc^2$", assetIds: ["a-own", "a-pub"] },
        { blockOrder: 1, blockType: "INTERACTIVE_QUESTION_EMBED", questionId: "q-1" },
      ],
      { authorTenantId: "tenant-1", assetCatalog: catalog },
    );
    expect(okReport.valid).toBe(true);

    const badReport = lintStudioDocument(
      [
        { blockOrder: 0, blockType: "WORKED_EXAMPLE", markdown: "Xem hình", assetIds: ["a-other"] },
        { blockOrder: 1, blockType: "INTERACTIVE_QUESTION_EMBED", questionId: "" },
      ],
      { authorTenantId: "tenant-1", assetCatalog: catalog },
    );
    expect(badReport.valid).toBe(false);
    expect(badReport.issues.map((i) => i.code)).toEqual([
      "CROSS_TENANT_ASSET_FORBIDDEN",
      "MISSING_EMBEDDED_QUESTION_ID",
    ]);
  });
});
