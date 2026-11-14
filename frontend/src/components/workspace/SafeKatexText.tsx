"use client";

import katex from "katex";
import React from "react";
import { KATEX_SAFE_RENDER_OPTIONS, scanUnsafeLatexCommands } from "@/lib/katex-security";

export interface SafeKatexRenderResult {
  readonly html: string;
  readonly blocked: boolean;
  readonly violations: readonly string[];
}

type TextSegment =
  | { kind: "text"; value: string }
  | { kind: "math"; value: string; displayMode: boolean };

/**
 * Hardened LaTeX renderer enforcing `trust: false` and `throwOnError: false`,
 * pre-filtered by `scanUnsafeLatexCommands` to strip DOM/XSS vectors like
 * `\href{javascript:...}`, `\htmlClass`, `\htmlStyle`, `\def`, and `\includegraphics`.
 */
export function renderSafeKatexFormula(
  formula: string,
  displayMode = false,
): SafeKatexRenderResult {
  const scan = scanUnsafeLatexCommands(formula);
  if (!scan.safe) {
    return {
      html: "",
      blocked: true,
      violations: scan.violations,
    };
  }

  const html = katex.renderToString(formula, {
    ...KATEX_SAFE_RENDER_OPTIONS,
    displayMode,
    throwOnError: false,
    trust: false,
  });

  return {
    html,
    blocked: false,
    violations: [],
  };
}

/**
 * Splits mixed Vietnamese prose and LaTeX math delimiters (`$$...$$`, `\[...\]`,
 * `\(...\)`, `$...$`) so plain text stays escaped React nodes and math renders via KaTeX.
 */
export function splitMixedLatexSegments(source: string): TextSegment[] {
  if (!source) {
    return [];
  }

  const pattern = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$(?:\\.|[^$\\])+\$)/g;
  const segments: TextSegment[] = [];
  let lastIndex = 0;

  for (const match of source.matchAll(pattern)) {
    const idx = match.index ?? 0;
    if (idx > lastIndex) {
      segments.push({ kind: "text", value: source.slice(lastIndex, idx) });
    }

    const token = match[0];
    if (token.startsWith("$$") && token.endsWith("$$")) {
      segments.push({ kind: "math", value: token.slice(2, -2).trim(), displayMode: true });
    } else if (token.startsWith("\\[") && token.endsWith("\\]")) {
      segments.push({ kind: "math", value: token.slice(2, -2).trim(), displayMode: true });
    } else if (token.startsWith("\\(") && token.endsWith("\\)")) {
      segments.push({ kind: "math", value: token.slice(2, -2).trim(), displayMode: false });
    } else {
      segments.push({ kind: "math", value: token.slice(1, -1).trim(), displayMode: false });
    }

    lastIndex = idx + token.length;
  }

  if (lastIndex < source.length) {
    segments.push({ kind: "text", value: source.slice(lastIndex) });
  }

  return segments;
}

export interface SafeKatexTextProps {
  text: string;
  displayMode?: boolean;
  pureFormula?: boolean;
  className?: string;
}

export function SafeKatexText({
  text,
  displayMode = false,
  pureFormula = false,
  className = "",
}: SafeKatexTextProps) {
  if (pureFormula) {
    const rendered = renderSafeKatexFormula(text, displayMode);
    if (rendered.blocked) {
      return (
        <span
          data-katex-blocked="true"
          data-violations={rendered.violations.join(",")}
          className={`inline-block rounded border border-amber-300 bg-amber-50 px-2 py-0.5 font-mono text-xs text-amber-900 ${className}`.trim()}
        >
          [Công thức đã được làm sạch an toàn]
        </span>
      );
    }

    return (
      <span
        data-katex-rendered="true"
        className={className}
        dangerouslySetInnerHTML={{ __html: rendered.html }}
      />
    );
  }

  const segments = splitMixedLatexSegments(text);
  return (
    <span className={className}>
      {segments.map((seg, idx) => {
        if (seg.kind === "text") {
          return <React.Fragment key={idx}>{seg.value}</React.Fragment>;
        }

        const rendered = renderSafeKatexFormula(seg.value, seg.displayMode);
        if (rendered.blocked) {
          return (
            <span
              key={idx}
              data-katex-blocked="true"
              data-violations={rendered.violations.join(",")}
              className="mx-1 inline-block rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 font-mono text-xs text-amber-900"
            >
              [Công thức đã bị chặn]
            </span>
          );
        }

        return (
          <span
            key={idx}
            data-katex-rendered="true"
            className={seg.displayMode ? "my-2 block overflow-x-auto" : "inline"}
            dangerouslySetInnerHTML={{ __html: rendered.html }}
          />
        );
      })}
    </span>
  );
}
