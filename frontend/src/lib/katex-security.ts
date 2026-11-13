/**
 * Workspace 1 & Workspace 4 KaTeX XSS hardening options & LaTeX macro sanitizer
 * per V11.3 Chapter 3.8B (throwOnError: false, trust: false).
 */

export interface KatexHardenedOptions {
  readonly throwOnError: false;
  readonly trust: false;
  readonly strict: "warn";
  readonly maxSize: number;
  readonly maxExpand: number;
}

export const KATEX_SAFE_RENDER_OPTIONS: KatexHardenedOptions = Object.freeze({
  throwOnError: false,
  trust: false,
  strict: "warn",
  maxSize: 10,
  maxExpand: 100,
});

const UNSAFE_TEX_PATTERNS: readonly { readonly code: string; readonly regex: RegExp }[] = [
  { code: "KATEX_DOM_INJECTION_MACRO", regex: /\\html(?:Class|Id|Style|Data)\b/i },
  { code: "KATEX_UNSAFE_INCLUDE", regex: /\\(?:include|input)\b/i },
  {
    code: "KATEX_JAVASCRIPT_OR_DATA_URI",
    regex: /\\(?:href|url)\s*\{\s*(?:javascript|vbscript|data):/i,
  },
  { code: "KATEX_RAW_HTML_TAG", regex: /<\s*script\b/i },
];

export interface LatexSafetyScanResult {
  readonly safe: boolean;
  readonly violations: readonly string[];
}

/**
 * Scans LaTeX content before KaTeX rendering to reject DOM/XSS primitives
 * even as defense-in-depth alongside ``trust: false``.
 */
export function scanUnsafeLatexCommands(texSource: string): LatexSafetyScanResult {
  const violations: string[] = [];
  for (const rule of UNSAFE_TEX_PATTERNS) {
    if (rule.regex.test(texSource)) {
      violations.push(rule.code);
    }
  }
  return {
    safe: violations.length === 0,
    violations,
  };
}
