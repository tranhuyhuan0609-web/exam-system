import { describe, expect, it } from "vitest";
import {
  isDangerousUrl,
  lintKatexMarkdown,
  lintMarkdownSafety,
  lintStudioDocument,
  resolveSlashCommand,
  type ClientAssetPermissionEntry,
} from "../block-studio-linter";
import { checkKatexSafety } from "../exam-input-policy";
import { scanUnsafeLatexCommands } from "../katex-security";
import { resolveWorkspaceUiState } from "../ui-state-machine";

describe("katex-security", () => {
  it.each([
    "\\href{https://x.test}{a}",
    "\\href {https://x.test}{a}",
    "\\url{https://x.test}",
    "\\url  {x}",
    "\\htmlClass{a}{b}",
    "\\htmlClass {a}{b}",
    "\\htmlId{a}{b}",
    "\\htmlStyle{color:red}{b}",
    "\\htmlData{k=v}{b}",
    "\\includegraphics{a.png}",
    "\\includegraphics [width=1]{a.png}",
    "\\include{a}",
    "\\input{a}",
    "\\input {a}",
    "\\def\\x{1}",
    "\\newcommand{\\x}{1}",
    "\\newcommand {\\x}{1}",
    "\\renewcommand{\\x}{1}",
    "x + \\gdef\\y{2}",
    "<script>alert(1)</script>",
    "<iframe src=x>",
  ])("rejects %s", (tex) => {
    const result = scanUnsafeLatexCommands(tex);
    expect(result.safe).toBe(false);
    expect(result.violations.length).toBeGreaterThan(0);
    expect(checkKatexSafety(tex).safe).toBe(false);
  });

  it.each(["", "\\frac{1}{2} + \\Delta\\varphi", "\\hrefx", "\\inputs", "\\default", "\\ce{H2O}", "a \\\\ b"])(
    "allows %j",
    (tex) => {
      expect(scanUnsafeLatexCommands(tex)).toEqual({ safe: true, violations: [] });
    },
  );

  it("keeps the javascript: code for \\href payloads and fails closed on non-string input", () => {
    expect(scanUnsafeLatexCommands("\\href{javascript:alert(1)}{x}").violations).toContain(
      "KATEX_JAVASCRIPT_OR_DATA_URI",
    );
    expect(scanUnsafeLatexCommands(undefined as unknown as string).safe).toBe(false);
  });
});

describe("block-studio-linter safety", () => {
  const codes = (md: string, type: "THEORY_SUMMARY" | "HTML_TABLE" | "FORMULA_BOX" = "THEORY_SUMMARY") =>
    lintStudioDocument([{ blockOrder: 1, blockType: type, markdown: md }], { authorTenantId: "t" }).issues.map(
      (i) => i.code,
    );

  it.each([
    "[x](javascript:alert(1))",
    "[x]( JavaScript:alert(1) )",
    "![i](data:text/html;base64,AAAA)",
    "[x](vbscript:msgbox)",
    "[x](jav&#97;script:alert(1))",
    "[x](jav&#x61;script&colon;alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](java\u200Bscript:alert(1))",
    "[x](%6Aavascript:alert(1))",
    "[x]: javascript:alert(1)",
    "<javascript:alert(1)>",
    '<a href="javascript:alert(1)">x</a>',
    "<a href='data:text/html,x'>x</a>",
    "<img src=vbscript:x>",
  ])("flags dangerous URL in %j", (md) => {
    expect(codes(md)).toContain("UNSAFE_URL_SCHEME");
    expect(codes(md, "HTML_TABLE")).toContain("UNSAFE_URL_SCHEME");
  });

  it.each(['<img src="x" onerror="alert(1)">', "<img src=x onerror=alert(1)>", '<td onclick = "x()">a</td>', '<p/onmouseover=x>'])(
    "flags inline event handler in %j",
    (md) => {
      expect(codes(md, "HTML_TABLE")).toContain("INLINE_EVENT_HANDLER");
    },
  );

  it("flags active tags and passes benign Markdown/HTML", () => {
    expect(codes("<script>x</script>")).toContain("UNSAFE_HTML_TAG");
    expect(codes("<table><tr><td>a</td></tr></table>", "HTML_TABLE")).toEqual([]);
    expect(codes("[ok](https://example.com/a?b=1) ![i](/img/a.png) <https://example.com>")).toEqual([]);
    expect(codes("Văn bản có từ onerror = nhưng không phải thẻ")).toEqual([]);
    expect(lintMarkdownSafety("")).toEqual([]);
  });

  it("isDangerousUrl handles invalid escapes without throwing", () => {
    expect(isDangerousUrl("%E0%A4%A")).toBe(false);
    expect(isDangerousUrl("&#99999999999;x")).toBe(false);
    expect(isDangerousUrl("")).toBe(false);
    expect(isDangerousUrl("https://a.test/javascript:x")).toBe(false);
  });

  it("runs the LaTeX scanner on inline, display, unclosed and formula-box math", () => {
    expect(codes("$\\href{https://x}{y}$")).toContain("UNSAFE_LATEX_COMMAND");
    expect(codes("$$\\input{secret}$$")).toContain("UNSAFE_LATEX_COMMAND");
    expect(codes("\\(\\htmlClass{a}{b}\\)")).toContain("UNSAFE_LATEX_COMMAND");
    expect(codes("\\[\\includegraphics{a}\\]")).toContain("UNSAFE_LATEX_COMMAND");
    expect(codes("$\\def\\x{1} unclosed")).toEqual(
      expect.arrayContaining(["UNCLOSED_KATEX_INLINE", "UNSAFE_LATEX_COMMAND"]),
    );
    expect(codes("\\def\\x{1}", "FORMULA_BOX")).toContain("UNSAFE_LATEX_COMMAND");
    expect(codes("Plain \\href text outside math")).toEqual([]);
    expect(lintKatexMarkdown("$\\frac{1}{2}$")).toEqual([]);
    const dup = lintKatexMarkdown("$\\input{a}$ $\\input{b}$", 3).filter((i) => i.code === "UNSAFE_LATEX_COMMAND");
    expect(dup).toHaveLength(1);
    expect(dup[0]?.blockOrder).toBe(3);
  });

  it("fails closed when assetCatalog is missing or the author tenant is empty", () => {
    const report = lintStudioDocument(
      [{ blockOrder: 2, blockType: "THEORY_SUMMARY", assetIds: ["a1", "a2"] }],
      { authorTenantId: "t" },
    );
    expect(report.valid).toBe(false);
    expect(report.issues.map((i) => i.code)).toEqual(["ASSET_CATALOG_UNAVAILABLE", "ASSET_CATALOG_UNAVAILABLE"]);

    expect(lintStudioDocument([{ blockOrder: 1, blockType: "THEORY_SUMMARY", assetIds: [] }], { authorTenantId: "t" }).valid).toBe(true);
    expect(lintStudioDocument([{ blockOrder: 1, blockType: "THEORY_SUMMARY" }], { authorTenantId: "t" }).valid).toBe(true);

    const catalog = new Map<string, ClientAssetPermissionEntry>([
      ["x", { assetId: "x", tenantId: "", visibilityScope: "TENANT_PRIVATE" }],
      ["y", { assetId: "y", tenantId: "o", visibilityScope: "SHARED_PARTNER", sharedTenantIds: [""] }],
      ["z", { assetId: "z", tenantId: "o", visibilityScope: "WEIRD" as unknown as "TENANT_PRIVATE" }],
    ]);
    const empty = lintStudioDocument([{ blockOrder: 1, blockType: "THEORY_SUMMARY", assetIds: ["x", "y", "z"] }], {
      authorTenantId: "",
      assetCatalog: catalog,
    });
    expect(empty.issues.map((i) => i.code)).toEqual([
      "CROSS_TENANT_ASSET_FORBIDDEN",
      "CROSS_TENANT_ASSET_FORBIDDEN",
      "CROSS_TENANT_ASSET_FORBIDDEN",
    ]);
  });

  it("looks up slash commands with hasOwn only", () => {
    for (const key of ["constructor", "__proto__", "toString", "hasOwnProperty", "valueOf", "/constructor", ""]) {
      expect(resolveSlashCommand(key)).toBeNull();
    }
    expect(resolveSlashCommand("  /CongThuc ")).toBe("FORMULA_BOX");
    expect(resolveSlashCommand(undefined as unknown as string)).toBeNull();
  });

  it("ignores invisible characters in the embedded question id", () => {
    const report = lintStudioDocument(
      [{ blockOrder: 1, blockType: "INTERACTIVE_QUESTION_EMBED", questionId: "\u200B \uFEFF" }],
      { authorTenantId: "t" },
    );
    expect(report.issues.map((i) => i.code)).toContain("MISSING_EMBEDDED_QUESTION_ID");
    expect(
      lintStudioDocument([{ blockOrder: 1, blockType: "INTERACTIVE_QUESTION_EMBED", questionId: "q1" }], {
        authorTenantId: "t",
      }).valid,
    ).toBe(true);
  });
});

describe("ui-state-machine fail-closed", () => {
  const base = {
    isLoading: false,
    previewPdfUrl: "https://cdn/preview.pdf",
    rawAssetUrl: "https://cdn/raw.pdf",
  } as const;

  it("treats a missing requiresProAsset flag as locked and never exposes the raw URL", () => {
    for (const tier of [undefined, "FREE"] as const) {
      const state = resolveWorkspaceUiState({ ...base, userTier: tier });
      expect(state.kind).toBe("LockedProPreview");
      expect(state.exposedAssetUrl).toBe("https://cdn/preview.pdf");
    }
    const offline = resolveWorkspaceUiState({ ...base, isOffline: true });
    expect(offline.kind).toBe("LockedProPreview");
    expect(offline.exposedAssetUrl).not.toContain("raw.pdf");
  });

  it("unlocks only for an explicit false flag or a Pro tier", () => {
    expect(resolveWorkspaceUiState({ ...base, requiresProAsset: false }).exposedAssetUrl).toBe("https://cdn/raw.pdf");
    expect(resolveWorkspaceUiState({ ...base, userTier: "TEACHER_PRO" }).kind).toBe("Ready");
    expect(resolveWorkspaceUiState({ ...base, requiresProAsset: true, userTier: "FREE" }).kind).toBe("LockedProPreview");
  });

  it("copes with non-finite or negative counts", () => {
    const open = { ...base, requiresProAsset: false } as const;
    for (const itemCount of [Number.NaN, Number.NEGATIVE_INFINITY, -3, 0]) {
      expect(resolveWorkspaceUiState({ ...open, itemCount }).kind).toBe("EmptyState");
    }
    expect(resolveWorkspaceUiState({ ...open, itemCount: Number.POSITIVE_INFINITY }).kind).toBe("EmptyState");
    for (const pending of [Number.NaN, Number.POSITIVE_INFINITY, -5]) {
      const state = resolveWorkspaceUiState({ ...open, isOffline: true, pendingAutosaveCount: pending });
      expect(state.kind).toBe("OfflineRetryBanner");
      expect(state.bannerMessage).toContain("(0 câu");
      expect(state.interactiveExamAllowed).toBe(true);
    }
    expect(resolveWorkspaceUiState({ ...open, isOffline: true, pendingAutosaveCount: 4.9 }).bannerMessage).toContain("(4 câu");
  });

  it("keeps conflict and fatal error precedence", () => {
    expect(resolveWorkspaceUiState({ isLoading: true, isSequenceConflict409: true }).kind).toBe("SequenceConflictModal");
    expect(resolveWorkspaceUiState({ isLoading: true, fatalErrorCode: "E" }).kind).toBe("ErrorBoundary");
  });
});
