export type StudioBlockType =
  | "THEORY_SUMMARY"
  | "FORMULA_BOX"
  | "METHOD_STEPS"
  | "TIP_WARNING"
  | "WORKED_EXAMPLE"
  | "INTERACTIVE_QUESTION_EMBED"
  | "HTML_TABLE";

export type SlashCommandName =
  | "/tomtat"
  | "/congthuc"
  | "/phuongphap"
  | "/chuy"
  | "/vidu"
  | "/cauhoi";

const SLASH_COMMAND_MAP: Record<SlashCommandName, StudioBlockType> = {
  "/tomtat": "THEORY_SUMMARY",
  "/congthuc": "FORMULA_BOX",
  "/phuongphap": "METHOD_STEPS",
  "/chuy": "TIP_WARNING",
  "/vidu": "WORKED_EXAMPLE",
  "/cauhoi": "INTERACTIVE_QUESTION_EMBED",
};

export interface StudioBlockInput {
  blockOrder: number;
  blockType: StudioBlockType;
  markdown?: string;
  assetIds?: readonly string[];
  questionId?: string;
}

export interface ClientAssetPermissionEntry {
  assetId: string;
  tenantId: string;
  visibilityScope: "SYSTEM_PUBLIC" | "TENANT_PRIVATE" | "SHARED_PARTNER";
  sharedTenantIds?: readonly string[];
}

export interface StudioLintIssue {
  blockOrder: number;
  code: string;
  message: string;
}

export interface StudioLintReport {
  valid: boolean;
  issues: StudioLintIssue[];
}

export function resolveSlashCommand(command: string): StudioBlockType | null {
  const normalized = command.trim().toLowerCase() as SlashCommandName;
  return SLASH_COMMAND_MAP[normalized] ?? null;
}

/**
 * Validate inline & display KaTeX syntax (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`),
 * curly brace balance, and `\begin{env}...\end{env}` matching in browser editors.
 */
export function lintKatexMarkdown(markdown: string, blockOrder = 0): StudioLintIssue[] {
  const issues: StudioLintIssue[] = [];
  const spans: string[] = [];
  const n = markdown.length;
  let i = 0;

  while (i < n) {
    const ch = markdown[i];
    if (ch === "\\") {
      const next = markdown[i + 1] ?? "";
      if (next === "(") {
        const end = markdown.indexOf("\\)", i + 2);
        if (end === -1) {
          issues.push({
            blockOrder,
            code: "UNCLOSED_KATEX_INLINE",
            message: "Unclosed '\\(' inline KaTeX delimiter",
          });
          return issues;
        }
        spans.push(markdown.slice(i + 2, end));
        i = end + 2;
        continue;
      }
      if (next === "[") {
        const end = markdown.indexOf("\\]", i + 2);
        if (end === -1) {
          issues.push({
            blockOrder,
            code: "UNCLOSED_KATEX_DISPLAY",
            message: "Unclosed '\\[' display KaTeX delimiter",
          });
          return issues;
        }
        spans.push(markdown.slice(i + 2, end));
        i = end + 2;
        continue;
      }
      i += 2;
      continue;
    }

    if (ch === "$") {
      const isDisplay = i + 1 < n && markdown[i + 1] === "$";
      const delim = isDisplay ? "$$" : "$";
      const start = i + delim.length;
      let j = start;
      let found = -1;
      while (j < n) {
        if (markdown[j] === "\\") {
          j += 2;
          continue;
        }
        if (isDisplay) {
          if (markdown[j] === "$" && markdown[j + 1] === "$") {
            found = j;
            break;
          }
        } else if (markdown[j] === "$" && markdown[j + 1] !== "$") {
          found = j;
          break;
        }
        j += 1;
      }
      if (found === -1) {
        issues.push({
          blockOrder,
          code: isDisplay ? "UNCLOSED_KATEX_DISPLAY" : "UNCLOSED_KATEX_INLINE",
          message: `Unclosed '${delim}' KaTeX delimiter`,
        });
        return issues;
      }
      spans.push(markdown.slice(start, found));
      i = found + delim.length;
      continue;
    }

    i += 1;
  }

  const envRegex = /\\(begin|end)\{([a-zA-Z0-9*]+)\}/g;
  for (const span of spans) {
    let depth = 0;
    for (let k = 0; k < span.length; k += 1) {
      if (span[k] === "\\") {
        k += 1;
        continue;
      }
      if (span[k] === "{") {
        depth += 1;
      } else if (span[k] === "}") {
        depth -= 1;
        if (depth < 0) {
          issues.push({
            blockOrder,
            code: "UNBALANCED_KATEX_BRACE",
            message: "Unexpected closing '}' brace in KaTeX expression",
          });
          break;
        }
      }
    }
    if (depth > 0) {
      issues.push({
        blockOrder,
        code: "UNBALANCED_KATEX_BRACE",
        message: "Unclosed '{' brace in KaTeX expression",
      });
    }

    const envStack: string[] = [];
    for (const match of span.matchAll(envRegex)) {
      const kind = match[1];
      const envName = match[2] ?? "";
      if (kind === "begin") {
        envStack.push(envName);
      } else {
        const top = envStack.pop();
        if (top !== envName) {
          issues.push({
            blockOrder,
            code: "MISMATCHED_KATEX_ENVIRONMENT",
            message: `Mismatched \\end{${envName}} in KaTeX block`,
          });
          break;
        }
      }
    }
    if (envStack.length > 0) {
      const unclosed = envStack[envStack.length - 1] ?? "";
      issues.push({
        blockOrder,
        code: "MISMATCHED_KATEX_ENVIRONMENT",
        message: `Unclosed \\begin{${unclosed}} environment in KaTeX block`,
      });
    }
  }

  return issues;
}

export function lintStudioDocument(
  blocks: readonly StudioBlockInput[],
  options: {
    authorTenantId: string;
    assetCatalog?: ReadonlyMap<string, ClientAssetPermissionEntry>;
  },
): StudioLintReport {
  const issues: StudioLintIssue[] = [];

  for (const block of blocks) {
    if (block.markdown) {
      issues.push(...lintKatexMarkdown(block.markdown, block.blockOrder));
    }

    if (block.blockType === "INTERACTIVE_QUESTION_EMBED" && !block.questionId?.trim()) {
      issues.push({
        blockOrder: block.blockOrder,
        code: "MISSING_EMBEDDED_QUESTION_ID",
        message: "Interactive question embed requires a non-empty questionId",
      });
    }

    if (options.assetCatalog && block.assetIds) {
      for (const assetId of block.assetIds) {
        const entry = options.assetCatalog.get(assetId);
        if (!entry) {
          issues.push({
            blockOrder: block.blockOrder,
            code: "MISSING_ASSET",
            message: `Asset '${assetId}' was not found`,
          });
          continue;
        }
        const permitted =
          entry.tenantId === options.authorTenantId ||
          entry.visibilityScope === "SYSTEM_PUBLIC" ||
          (entry.visibilityScope === "SHARED_PARTNER" &&
            (entry.sharedTenantIds?.includes(options.authorTenantId) ?? false));
        if (!permitted) {
          issues.push({
            blockOrder: block.blockOrder,
            code: "CROSS_TENANT_ASSET_FORBIDDEN",
            message: `Asset '${assetId}' belongs to another tenant`,
          });
        }
      }
    }
  }

  return {
    valid: issues.length === 0,
    issues,
  };
}
