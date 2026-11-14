"use client";

import React, { useState } from "react";
import {
  resolveSlashCommand,
  type SlashCommandName,
  type StudioBlockInput,
  type StudioBlockType,
} from "@/lib/block-studio-linter";
import { BLOCK_TYPE_META, normalizeWordOrMathTypePaste } from "./types";

export interface StudioBlockEditorProps {
  blocks: readonly StudioBlockInput[];
  onChangeBlocks: (nextBlocks: StudioBlockInput[]) => void;
  availableAssetIds?: readonly string[];
  title?: string;
}

const SLASH_COMMANDS: readonly {
  readonly command: SlashCommandName;
  readonly label: string;
}[] = [
  { command: "/tomtat", label: "Tóm tắt lý thuyết" },
  { command: "/congthuc", label: "Hộp công thức" },
  { command: "/phuongphap", label: "Phương pháp giải" },
  { command: "/chuy", label: "Chú ý / Bẫy sai" },
  { command: "/vidu", label: "Ví dụ mẫu" },
  { command: "/cauhoi", label: "Nhúng câu hỏi" },
];

const LATEX_SNIPPETS: readonly { readonly label: string; readonly tex: string }[] = [
  { label: "\\vec{F}", tex: "$\\vec{F}$" },
  { label: "\\Delta\\varphi", tex: "$\\Delta\\varphi$" },
  { label: "P(B|A)", tex: "$P(B|A) = \\frac{P(B)P(A|B)}{P(A)}$" },
  { label: "\\xrightarrow{t°}", tex: "$A + B \\xrightarrow{t^\\circ} C$" },
];

export function StudioBlockEditor({
  blocks,
  onChangeBlocks,
  availableAssetIds = ["asset-bayes-tree", "asset-thptqg-public", "asset-other-tenant"],
  title = "Trình soạn thảo khối Block Studio (Gõ lệnh /congthuc, /phuongphap, /chuy, /vidu)",
}: StudioBlockEditorProps) {
  const [slashInput, setSlashInput] = useState("");
  const [activeBlockOrder, setActiveBlockOrder] = useState<number>(blocks[0]?.blockOrder ?? 1);
  const [slashError, setSlashError] = useState<string | null>(null);

  const reindexBlocks = (list: StudioBlockInput[]): StudioBlockInput[] =>
    list.map((b, idx) => ({ ...b, blockOrder: idx + 1 }));

  const appendBlockByType = (blockType: StudioBlockType) => {
    const meta = BLOCK_TYPE_META[blockType];
    const nextOrder = blocks.length + 1;
    const newBlock: StudioBlockInput = {
      blockOrder: nextOrder,
      blockType,
      markdown: meta.defaultMarkdown,
      ...(blockType === "INTERACTIVE_QUESTION_EMBED" ? { questionId: "q-embed-1" } : {}),
    };
    onChangeBlocks([...blocks, newBlock]);
    setActiveBlockOrder(nextOrder);
    setSlashError(null);
  };

  const handleRunSlashCommand = (rawCommand: string) => {
    const resolved = resolveSlashCommand(rawCommand);
    if (!resolved) {
      setSlashError(`Không nhận diện được lệnh '${rawCommand}'. Dùng /congthuc, /phuongphap, /chuy, /vidu, /tomtat hoặc /cauhoi.`);
      return;
    }
    appendBlockByType(resolved);
    setSlashInput("");
  };

  const handleBlockMarkdownChange = (blockOrder: number, rawValue: string) => {
    const firstLine = rawValue.split("\n")[0]?.trim() ?? "";
    const matchedSlash = /^\/[a-z]+$/i.test(firstLine) ? resolveSlashCommand(firstLine) : null;

    onChangeBlocks(
      blocks.map((b) => {
        if (b.blockOrder !== blockOrder) return b;
        if (matchedSlash) {
          return {
            ...b,
            blockType: matchedSlash,
            markdown: BLOCK_TYPE_META[matchedSlash].defaultMarkdown,
            ...(matchedSlash === "INTERACTIVE_QUESTION_EMBED" && !b.questionId
              ? { questionId: "q-embed-1" }
              : {}),
          };
        }
        return { ...b, markdown: rawValue };
      }),
    );
  };

  const handleInsertSnippet = (tex: string) => {
    if (blocks.length === 0) {
      onChangeBlocks([{ blockOrder: 1, blockType: "THEORY_SUMMARY", markdown: tex }]);
      setActiveBlockOrder(1);
      return;
    }
    const targetOrder = blocks.some((b) => b.blockOrder === activeBlockOrder)
      ? activeBlockOrder
      : blocks[0]!.blockOrder;
    onChangeBlocks(
      blocks.map((b) =>
        b.blockOrder === targetOrder
          ? { ...b, markdown: `${b.markdown ?? ""}${b.markdown ? " " : ""}${tex}` }
          : b,
      ),
    );
  };

  const handleToggleAsset = (blockOrder: number, assetId: string) => {
    onChangeBlocks(
      blocks.map((b) => {
        if (b.blockOrder !== blockOrder) return b;
        const current = b.assetIds ?? [];
        const nextAssets = current.includes(assetId)
          ? current.filter((id) => id !== assetId)
          : [...current, assetId];
        return { ...b, assetIds: nextAssets };
      }),
    );
  };

  const slashSuggestions = slashInput.trim().startsWith("/")
    ? SLASH_COMMANDS.filter((item) => item.command.startsWith(slashInput.trim().toLowerCase()))
    : [];

  return (
    <div data-testid="ws4-block-editor" className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
      <div>
        <h2 className="text-sm font-extrabold uppercase tracking-wide text-slate-800">{title}</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Nhập lệnh dấu gạch chéo hoặc bấm nút nhanh để chèn khối chuẩn VietJack nâng cấp.
        </p>
      </div>

      {/* Slash Command Input + Autocomplete */}
      <div className="space-y-2 rounded-xl border border-indigo-200 bg-indigo-50/50 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            data-testid="ws4-slash-command-input"
            aria-label="Nhập lệnh khối Block Studio"
            placeholder="Gõ lệnh ví dụ /congthuc, /phuongphap, /chuy, /vidu..."
            value={slashInput}
            onChange={(e) => {
              setSlashInput(e.target.value);
              setSlashError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleRunSlashCommand(slashInput);
              }
            }}
            className="min-w-[240px] flex-1 rounded-lg border border-indigo-300 bg-white px-3 py-1.5 font-mono text-xs text-slate-900 focus:border-indigo-600 focus:outline-none"
          />
          <button
            type="button"
            data-testid="ws4-run-slash-btn"
            onClick={() => handleRunSlashCommand(slashInput)}
            className="rounded-lg bg-indigo-700 px-3 py-1.5 text-xs font-bold text-white transition hover:bg-indigo-800"
          >
            Chèn khối
          </button>
        </div>

        {slashSuggestions.length > 0 && (
          <div data-testid="ws4-slash-suggestions" className="flex flex-wrap gap-1.5">
            {slashSuggestions.map((item) => (
              <button
                key={item.command}
                type="button"
                data-testid={`ws4-suggestion-${item.command.slice(1)}`}
                onClick={() => handleRunSlashCommand(item.command)}
                className="rounded-md border border-indigo-300 bg-white px-2 py-1 text-xs font-semibold text-indigo-800 hover:bg-indigo-100"
              >
                <code>{item.command}</code> · {item.label}
              </button>
            ))}
          </div>
        )}

        {slashError && (
          <p data-testid="ws4-slash-error" className="text-xs font-semibold text-red-700">
            {slashError}
          </p>
        )}

        <div className="flex flex-wrap gap-1.5 pt-1">
          {SLASH_COMMANDS.map((item) => (
            <button
              key={item.command}
              type="button"
              data-testid={`ws4-quick-cmd-${item.command.slice(1)}`}
              onClick={() => handleRunSlashCommand(item.command)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 transition hover:border-indigo-400 hover:bg-indigo-50"
            >
              {item.command} ({item.label})
            </button>
          ))}
        </div>
      </div>

      {/* Math/Physics/Chem LaTeX Snippet Toolbar */}
      <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 pb-3">
        <span className="text-[11px] font-bold uppercase text-slate-500">Chèn nhanh Toán/Lý/Hóa:</span>
        {LATEX_SNIPPETS.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => handleInsertSnippet(s.tex)}
            className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5 font-mono text-[11px] font-semibold text-slate-700 hover:bg-slate-100"
          >
            {s.label}
          </button>
        ))}
        <button
          type="button"
          data-testid="ws4-normalize-paste-btn"
          onClick={() =>
            onChangeBlocks(
              blocks.map((b) => ({
                ...b,
                markdown: b.markdown ? normalizeWordOrMathTypePaste(b.markdown) : b.markdown,
              })),
            )
          }
          className="ml-auto rounded border border-emerald-300 bg-emerald-50 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-800 hover:bg-emerald-100"
        >
          Chuẩn hóa dán Word/MathType
        </button>
      </div>

      {/* Block List */}
      <div className="space-y-3">
        {blocks.map((block) => {
          const meta = BLOCK_TYPE_META[block.blockType];
          const isFormulaBox = block.blockType === "FORMULA_BOX";
          return (
            <div
              key={block.blockOrder}
              data-testid={`ws4-editor-block-${block.blockOrder}`}
              data-block-type={block.blockType}
              onClick={() => setActiveBlockOrder(block.blockOrder)}
              className={`rounded-xl border border-slate-200 border-l-4 p-3.5 transition ${meta.accent} ${
                activeBlockOrder === block.blockOrder ? "ring-1 ring-indigo-500" : ""
              }`}
            >
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-900 px-2 py-0.5 text-[11px] font-bold text-white">
                    #{block.blockOrder} · {block.blockType}
                  </span>
                  <span className="text-xs font-semibold text-slate-700">{meta.title}</span>
                </div>
                <button
                  type="button"
                  data-testid={`ws4-delete-block-${block.blockOrder}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onChangeBlocks(reindexBlocks(blocks.filter((b) => b.blockOrder !== block.blockOrder)));
                  }}
                  className="rounded border border-red-200 bg-white px-2 py-0.5 text-[11px] font-semibold text-red-700 hover:bg-red-50"
                >
                  Xóa khối
                </button>
              </div>

              {block.blockType === "INTERACTIVE_QUESTION_EMBED" && (
                <div className="mb-2 flex items-center gap-2">
                  <label
                    htmlFor={`ws4-qid-${block.blockOrder}`}
                    className="text-xs font-semibold text-slate-700"
                  >
                    Question ID:
                  </label>
                  <input
                    id={`ws4-qid-${block.blockOrder}`}
                    data-testid={`ws4-question-id-input-${block.blockOrder}`}
                    type="text"
                    value={block.questionId ?? ""}
                    onChange={(e) =>
                      onChangeBlocks(
                        blocks.map((b) =>
                          b.blockOrder === block.blockOrder ? { ...b, questionId: e.target.value } : b,
                        ),
                      )
                    }
                    className="rounded border border-slate-300 bg-white px-2.5 py-1 font-mono text-xs text-slate-900"
                  />
                </div>
              )}

              <textarea
                data-testid={`ws4-block-textarea-${block.blockOrder}`}
                rows={isFormulaBox ? 2 : 3}
                value={block.markdown ?? ""}
                onChange={(e) => handleBlockMarkdownChange(block.blockOrder, e.target.value)}
                placeholder={
                  isFormulaBox
                    ? "Nhập biểu thức LaTeX thuần (FORMULA_BOX tự kiểm tra toàn khối)..."
                    : "Nhập nội dung Markdown + KaTeX ($...$ hoặc $$...$$)..."
                }
                className="w-full rounded-lg border border-slate-300 bg-white p-2.5 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:outline-none"
              />

              {/* Asset Attachment Chips */}
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="text-[11px] font-semibold text-slate-600">Đính kèm asset_id:</span>
                {availableAssetIds.map((assetId) => {
                  const attached = block.assetIds?.includes(assetId) ?? false;
                  return (
                    <button
                      key={assetId}
                      type="button"
                      data-testid={`ws4-asset-toggle-${block.blockOrder}-${assetId}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggleAsset(block.blockOrder, assetId);
                      }}
                      className={`rounded-md border px-2 py-0.5 font-mono text-[10px] font-semibold transition ${
                        attached
                          ? "border-indigo-600 bg-indigo-700 text-white"
                          : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                      }`}
                    >
                      {attached ? "✓ " : "+ "}
                      {assetId}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
