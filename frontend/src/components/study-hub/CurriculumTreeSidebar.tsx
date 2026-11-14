"use client";

import React, { useState } from "react";
import { resolveMasteryIndicator, type MasteryColor } from "@/lib/workspace1-study-hub";
import type { CurriculumTreeNode } from "./types";

const MASTERY_DOT_STYLES: Record<MasteryColor, string> = {
  GREEN: "bg-emerald-500 ring-emerald-200",
  YELLOW: "bg-amber-400 ring-amber-200",
  RED: "bg-rose-500 ring-rose-200",
  GRAY: "bg-slate-400 ring-slate-200",
};

const LEVEL_BADGE_LABELS: Record<CurriculumTreeNode["level"], string> = {
  CHAPTER: "Chương",
  LESSON: "Bài",
  SECTION: "Mục",
  PROBLEM_TYPE: "Dạng bài",
};

export interface CurriculumTreeSidebarProps {
  tree: readonly CurriculumTreeNode[];
  activeNodeId: string;
  onSelectNode: (node: CurriculumTreeNode) => void;
}

interface TreeBranchProps {
  node: CurriculumTreeNode;
  depth: number;
  activeNodeId: string;
  onSelectNode: (node: CurriculumTreeNode) => void;
}

function TreeBranch({ node, depth, activeNodeId, onSelectNode }: TreeBranchProps) {
  const mastery = resolveMasteryIndicator({
    masteryRatio: node.mastery.masteryRatio,
    answeredCount: node.mastery.answeredCount,
    standardError: node.mastery.standardError,
  });
  const isActive = node.id === activeNodeId;

  return (
    <li data-level={node.level} data-node-id={node.id} className="space-y-1">
      <button
        type="button"
        onClick={() => onSelectNode(node)}
        aria-current={isActive ? "location" : undefined}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
        className={`flex w-full items-start gap-2 rounded-lg py-1.5 pr-2 text-left text-xs transition ${
          isActive
            ? "bg-blue-50 font-semibold text-blue-900 ring-1 ring-blue-200"
            : "text-slate-700 hover:bg-slate-100"
        }`}
      >
        <span
          data-testid={`mastery-dot-${node.id}`}
          data-mastery-color={mastery.color}
          data-mastery-status={mastery.status}
          title={mastery.label}
          className={`mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ring-2 ${MASTERY_DOT_STYLES[mastery.color]}`}
        />
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-bold uppercase tracking-wide text-slate-400">
            {LEVEL_BADGE_LABELS[node.level]} · {node.code}
          </span>
          <span className="block leading-snug">{node.title}</span>
          <span className="mt-0.5 block text-[10px] text-slate-500">{mastery.label}</span>
        </span>
      </button>

      {node.children && node.children.length > 0 && (
        <ul className="space-y-1 border-l border-slate-200 pl-1">
          {node.children.map((child) => (
            <TreeBranch
              key={child.id}
              node={child}
              depth={depth + 1}
              activeNodeId={activeNodeId}
              onSelectNode={onSelectNode}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export function CurriculumTreeSidebar({
  tree,
  activeNodeId,
  onSelectNode,
}: CurriculumTreeSidebarProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      data-testid="workspace1-curriculum-sidebar"
      aria-label="Mục lục SGK 4 cấp"
      className={`shrink-0 transition-all ${
        collapsed ? "w-full lg:w-[56px]" : "w-full lg:w-[260px]"
      }`}
    >
      <div className="sticky top-[76px] rounded-2xl border border-slate-200 bg-white p-3 shadow-xs">
        <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-2">
          {!collapsed && (
            <div>
              <h2 className="text-xs font-extrabold uppercase tracking-wider text-slate-800">
                Mục lục SGK 4 cấp
              </h2>
              <p className="text-[11px] text-slate-500">Chương → Bài → Mục → Dạng bài</p>
            </div>
          )}
          <button
            type="button"
            data-testid="toggle-curriculum-sidebar"
            onClick={() => setCollapsed((prev) => !prev)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Mở rộng mục lục" : "Thu gọn mục lục"}
            className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            {collapsed ? ">>" : "<<"}
          </button>
        </div>

        {!collapsed && (
          <>
            <ul className="mt-3 space-y-1.5">
              {tree.map((rootNode) => (
                <TreeBranch
                  key={rootNode.id}
                  node={rootNode}
                  depth={0}
                  activeNodeId={activeNodeId}
                  onSelectNode={onSelectNode}
                />
              ))}
            </ul>

            <div className="mt-4 rounded-xl border border-slate-100 bg-slate-50 p-2.5 text-[11px] text-slate-600">
              <div className="font-semibold text-slate-800">Chú giải Mastery</div>
              <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  &ge; 80% Tốt
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-amber-400" />
                  50–79% Ôn thêm
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-rose-500" />
                  &lt; 50% Yếu
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-slate-400" />
                  &lt; 20 câu / Chưa rõ
                </span>
              </div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
