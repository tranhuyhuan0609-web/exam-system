import { scanUnsafeLatexCommands } from "./katex-security";

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
  if (typeof command !== "string") return null;
  const normalized = command.trim().toLowerCase();
  // Object.hasOwn: user text such as "constructor" / "__proto__" must never reach the prototype chain.
  return Object.hasOwn(SLASH_COMMAND_MAP, normalized)
    ? SLASH_COMMAND_MAP[normalized as SlashCommandName]
    : null;
}

const INVISIBLE_CHARS_RE = /[\u200B-\u200F\u2028-\u202F\u2060-\u206F\uFEFF\u00AD\u180E]/g;
const HTML_ENTITY_NAMES: Readonly<Record<string, string>> = {
  colon: ":",
  tab: "\t",
  newline: "\n",
  lpar: "(",
  rpar: ")",
  sol: "/",
  period: ".",
  comma: ",",
  semi: ";",
};

function decodeEntities(value: string): string {
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);?/gi, (match, body: string) => {
    const lower = body.toLowerCase();
    if (lower.startsWith("#x")) {
      const cp = Number.parseInt(lower.slice(2), 16);
      return Number.isInteger(cp) && cp >= 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : "";
    }
    if (lower.startsWith("#")) {
      const cp = Number.parseInt(lower.slice(1), 10);
      return Number.isInteger(cp) && cp >= 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : "";
    }
    return Object.hasOwn(HTML_ENTITY_NAMES, lower) ? (HTML_ENTITY_NAMES[lower] as string) : match;
  });
}

/** True for `javascript:`, `data:` and `vbscript:` URLs, also when obfuscated with entities, %-escapes or whitespace. */
export function isDangerousUrl(rawUrl: string): boolean {
  let url = rawUrl;
  for (let round = 0; round < 3; round += 1) {
    url = decodeEntities(url);
    try {
      url = decodeURIComponent(url);
    } catch {
      // keep the partially decoded text
    }
  }
  const compact = url.replace(INVISIBLE_CHARS_RE, "").replace(/[\u0000-\u0020]/g, "").toLowerCase();
  return /^(?:javascript|data|vbscript):/.test(compact);
}

/**
 * Lints Markdown links/images/reference definitions/autolinks and raw HTML for dangerous URL schemes,
 * inline event handlers and active tags. Applies to every block that carries Markdown.
 */
export function lintMarkdownSafety(markdown: string, blockOrder = 0): StudioLintIssue[] {
  const issues: StudioLintIssue[] = [];
  const urls: string[] = [];

  for (const m of markdown.matchAll(/!?\[[^\]]*\]\(\s*<?([^)>]*?)>?(?:\s+["'][^)]*)?\)/g)) {
    urls.push(m[1] ?? "");
  }
  for (const m of markdown.matchAll(/^[ \t]{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)/gm)) {
    urls.push(m[1] ?? "");
  }
  for (const m of markdown.matchAll(/<([a-z][a-z0-9+.-]*:[^>\s]*)>/gi)) {
    urls.push(m[1] ?? "");
  }
  const attrRe =
    /\b(?:href|src|xlink:href|action|formaction|data|poster|srcset)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi;
  for (const m of markdown.matchAll(attrRe)) {
    urls.push(m[1] ?? m[2] ?? m[3] ?? "");
  }

  if (urls.some(isDangerousUrl)) {
    issues.push({
      blockOrder,
      code: "UNSAFE_URL_SCHEME",
      message: "javascript:, data: and vbscript: URLs are not allowed in links, images or HTML",
    });
  }
  if (/<[^>]*[\s"'/]on[a-z]+\s*=/i.test(markdown)) {
    issues.push({
      blockOrder,
      code: "INLINE_EVENT_HANDLER",
      message: "Inline HTML event handlers (onerror=, onclick=, ...) are not allowed",
    });
  }
  if (/<\s*\/?\s*(?:script|iframe|object|embed)\b/i.test(markdown)) {
    issues.push({
      blockOrder,
      code: "UNSAFE_HTML_TAG",
      message: "<script>, <iframe>, <object> and <embed> tags are not allowed",
    });
  }
  return issues;
}

/**
 * Validate inline & display KaTeX syntax (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`),
 * curly brace balance, and `\begin{env}...\end{env}` matching in browser editors.
 */
export function lintKatexMarkdown(
  markdown: string,
  blockOrder = 0,
  options: { readonly wholeInputIsMath?: boolean } = {},
): StudioLintIssue[] {
  const spans: string[] = options.wholeInputIsMath ? [markdown] : [];
  const issues = lintKatexSyntax(markdown, blockOrder, spans);
  const seen = new Set<string>();
  for (const span of spans) {
    for (const code of scanUnsafeLatexCommands(span).violations) {
      if (seen.has(code)) continue;
      seen.add(code);
      issues.push({
        blockOrder,
        code: "UNSAFE_LATEX_COMMAND",
        message: `Unsafe LaTeX command rejected (${code})`,
      });
    }
  }
  return issues;
}

function lintKatexSyntax(markdown: string, blockOrder: number, spans: string[]): StudioLintIssue[] {
  const issues: StudioLintIssue[] = [];
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
          spans.push(markdown.slice(i + 2));
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
          spans.push(markdown.slice(i + 2));
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
        spans.push(markdown.slice(start));
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
      issues.push(
        ...lintKatexMarkdown(block.markdown, block.blockOrder, {
          wholeInputIsMath: block.blockType === "FORMULA_BOX",
        }),
        ...lintMarkdownSafety(block.markdown, block.blockOrder),
      );
    }

    if (
      block.blockType === "INTERACTIVE_QUESTION_EMBED" &&
      !(block.questionId ?? "").replace(INVISIBLE_CHARS_RE, "").trim()
    ) {
      issues.push({
        blockOrder: block.blockOrder,
        code: "MISSING_EMBEDDED_QUESTION_ID",
        message: "Interactive question embed requires a non-empty questionId",
      });
    }

    if (block.assetIds && block.assetIds.length > 0 && !options.assetCatalog) {
      // Fail closed: without a permission catalog no asset can be proven permitted.
      for (const assetId of block.assetIds) {
        issues.push({
          blockOrder: block.blockOrder,
          code: "ASSET_CATALOG_UNAVAILABLE",
          message: `Asset '${assetId}' cannot be verified because the asset catalog is unavailable`,
        });
      }
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
          (options.authorTenantId !== "" && entry.tenantId === options.authorTenantId) ||
          entry.visibilityScope === "SYSTEM_PUBLIC" ||
          (entry.visibilityScope === "SHARED_PARTNER" &&
            options.authorTenantId !== "" &&
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
