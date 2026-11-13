/**
 * Workspace 1 & 2 (C07) pure UI policy helpers:
 * - THPTQG 2025+ 4-character short-numeric input validator & comma->dot normalizer
 * - Part II 4-part True/False partial-completion palette detector
 * - Safe KaTeX render options (throwOnError: false, trust: false) & XSS filter
 */

export const MAX_SHORT_NUMERIC_CHARS = 4;

export const SAFE_KATEX_OPTIONS = Object.freeze({
  throwOnError: false,
  trust: false,
  strict: false,
});

export interface ShortNumericValidationResult {
  valid: boolean;
  displayValue: string;
  normalizedValue: string | null;
  warning: string | null;
}

const THOUSANDS_DOT_RE = /^\d{1,3}\.\d{3}$/;
const NUMERIC_CHAR_RE = /^[-0-9,.]+$/;

export function validateShortNumericInput(raw: string): ShortNumericValidationResult {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { valid: true, displayValue: "", normalizedValue: null, warning: null };
  }

  if (THOUSANDS_DOT_RE.test(trimmed)) {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: "Không nhập dấu chấm phân cách hàng nghìn (ví dụ 1.000). Chỉ nhập tối đa 4 ký tự.",
    };
  }

  if (trimmed.length > MAX_SHORT_NUMERIC_CHARS) {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: `Ô trả lời ngắn chỉ cho phép tối đa ${MAX_SHORT_NUMERIC_CHARS} ký tự.`,
    };
  }

  if (!NUMERIC_CHAR_RE.test(trimmed)) {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: "Chỉ được nhập chữ số 0-9, dấu '-' ở đầu và dấu ',' thập phân.",
    };
  }

  if (trimmed.indexOf("-", 1) !== -1) {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: "Dấu '-' chỉ được đặt ở vị trí đầu tiên.",
    };
  }

  const separators = trimmed.match(/[,.]/g) ?? [];
  if (separators.length > 1) {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: "Chỉ được có tối đa 1 dấu thập phân.",
    };
  }

  if (trimmed === "-" || trimmed === "," || trimmed === "." || trimmed === "-," || trimmed === "-.") {
    return {
      valid: false,
      displayValue: trimmed,
      normalizedValue: null,
      warning: "Giá trị số chưa hoàn chỉnh.",
    };
  }

  const normalized = trimmed.replace(",", ".");
  return {
    valid: true,
    displayValue: trimmed,
    normalizedValue: normalized,
    warning: null,
  };
}

export type ClusterTrueFalseStatus = "UNANSWERED" | "PARTIAL_WARNING" | "COMPLETE";

export interface ClusterTrueFalseAnswers {
  a?: boolean | null;
  b?: boolean | null;
  c?: boolean | null;
  d?: boolean | null;
}

export function evaluateClusterTrueFalseState(
  answers: ClusterTrueFalseAnswers,
): ClusterTrueFalseStatus {
  const keys: Array<keyof ClusterTrueFalseAnswers> = ["a", "b", "c", "d"];
  const answeredCount = keys.filter((k) => typeof answers[k] === "boolean").length;
  if (answeredCount === 0) return "UNANSWERED";
  if (answeredCount < 4) return "PARTIAL_WARNING";
  return "COMPLETE";
}

export interface KatexSafetyCheck {
  safe: boolean;
  reasons: string[];
}

const UNSAFE_KATEX_PATTERNS: Array<{ re: RegExp; reason: string }> = [
  { re: /\\href\s*\{\s*javascript:/i, reason: "javascript: URI blocked in \\href" },
  { re: /\\url\s*\{\s*javascript:/i, reason: "javascript: URI blocked in \\url" },
  { re: /\\html(Class|Style|Id|Data)\b/i, reason: "trust-required HTML macro blocked" },
];

export function checkKatexSafety(tex: string): KatexSafetyCheck {
  const reasons = UNSAFE_KATEX_PATTERNS.filter((p) => p.re.test(tex)).map((p) => p.reason);
  return {
    safe: reasons.length === 0,
    reasons,
  };
}
