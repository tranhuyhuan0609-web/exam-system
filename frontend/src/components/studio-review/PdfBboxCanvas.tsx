"use client";

import React, { useRef, useState } from "react";
import { normalizeAndClampBBox, type BoundingBox } from "@/lib/bbox-reviewer";
import {
  BBOX_REGION_COLORS,
  type BboxRegion,
  type BboxRegionKind,
  type ReviewDocumentPage,
  type ReviewQuestionItem,
} from "./types";

export interface PdfBboxCanvasProps {
  page: ReviewDocumentPage;
  allPages: readonly ReviewDocumentPage[];
  pageItems: readonly ReviewQuestionItem[];
  selectedItem: ReviewQuestionItem;
  activeRegionKind: BboxRegionKind;
  onChangePage: (pageNo: number) => void;
  onSelectRegionKind: (kind: BboxRegionKind) => void;
  onSelectItem: (itemId: string) => void;
  onRecropRegion: (itemId: string, kind: BboxRegionKind, nextBox: BoundingBox) => void;
  zoomPercent: number;
  onZoomChange: (nextZoom: number) => void;
}

interface Point2D {
  readonly x: number;
  readonly y: number;
}

function clientPointToPageCoords(
  svgElement: SVGSVGElement | null,
  clientX: number,
  clientY: number,
  pageWidth: number,
  pageHeight: number,
): Point2D {
  if (!svgElement) {
    return {
      x: Math.max(0, Math.min(pageWidth, clientX)),
      y: Math.max(0, Math.min(pageHeight, clientY)),
    };
  }
  const rect = svgElement.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    const scaledX = ((clientX - rect.left) / rect.width) * pageWidth;
    const scaledY = ((clientY - rect.top) / rect.height) * pageHeight;
    return {
      x: Math.max(0, Math.min(pageWidth, Math.round(scaledX))),
      y: Math.max(0, Math.min(pageHeight, Math.round(scaledY))),
    };
  }
  // JSDOM layout rects are 0x0; map clientX/clientY directly within page bounds for deterministic drag tests.
  return {
    x: Math.max(0, Math.min(pageWidth, Math.round(clientX))),
    y: Math.max(0, Math.min(pageHeight, Math.round(clientY))),
  };
}

export function PdfBboxCanvas({
  page,
  allPages,
  pageItems,
  selectedItem,
  activeRegionKind,
  onChangePage,
  onSelectRegionKind,
  onSelectItem,
  onRecropRegion,
  zoomPercent,
  onZoomChange,
}: PdfBboxCanvasProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [dragStart, setDragStart] = useState<Point2D | null>(null);
  const [dragCurrent, setDragCurrent] = useState<Point2D | null>(null);

  const visibleRegions: { readonly itemId: string; readonly region: BboxRegion }[] = [];
  for (const item of pageItems) {
    for (const region of item.regions) {
      visibleRegions.push({ itemId: item.id, region });
    }
  }

  const activeRegion =
    selectedItem.regions.find((r) => r.kind === activeRegionKind) ?? selectedItem.regions[0] ?? null;

  const previewDraftBox =
    dragStart && dragCurrent
      ? normalizeAndClampBBox(
          { x0: dragStart.x, y0: dragStart.y, x1: dragCurrent.x, y1: dragCurrent.y },
          page.width,
          page.height,
          8,
        )
      : null;

  const startDrag = (clientX: number, clientY: number) => {
    const pt = clientPointToPageCoords(svgRef.current, clientX, clientY, page.width, page.height);
    setDragStart(pt);
    setDragCurrent(pt);
  };

  const moveDrag = (clientX: number, clientY: number) => {
    if (!dragStart) return;
    setDragCurrent(clientPointToPageCoords(svgRef.current, clientX, clientY, page.width, page.height));
  };

  const endDrag = (clientX: number, clientY: number) => {
    if (!dragStart) return;
    const endPt = clientPointToPageCoords(svgRef.current, clientX, clientY, page.width, page.height);
    const clamped = normalizeAndClampBBox(
      { x0: dragStart.x, y0: dragStart.y, x1: endPt.x, y1: endPt.y },
      page.width,
      page.height,
      8,
    );
    setDragStart(null);
    setDragCurrent(null);
    onRecropRegion(selectedItem.id, activeRegionKind, clamped);
  };

  return (
    <section
      data-testid="ws3-left-canvas-pane"
      data-focused-item-id={selectedItem.id}
      data-focused-page={page.pageNo}
      data-zoom-percent={zoomPercent}
      className="flex flex-col rounded-2xl border border-slate-200 bg-white shadow-xs"
    >
      {/* Canvas Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {allPages.map((p) => (
            <button
              key={p.pageNo}
              type="button"
              data-testid={`ws3-page-btn-${p.pageNo}`}
              onClick={() => onChangePage(p.pageNo)}
              className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition ${
                p.pageNo === page.pageNo
                  ? "bg-indigo-700 text-white"
                  : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-100"
              }`}
            >
              Trang {p.pageNo}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          {(["QUESTION", "FIGURE_CROP", "HTML_TABLE"] as const).map((kind) => {
            const meta = BBOX_REGION_COLORS[kind];
            const active = activeRegionKind === kind;
            return (
              <button
                key={kind}
                type="button"
                data-testid={`ws3-kind-btn-${kind}`}
                onClick={() => onSelectRegionKind(kind)}
                className={`rounded-md border px-2 py-1 text-[11px] font-bold transition ${
                  active ? `${meta.badge} ring-2 ring-slate-900/20` : "border-slate-200 bg-white text-slate-600"
                }`}
              >
                {kind === "QUESTION" ? "Câu hỏi" : kind === "FIGURE_CROP" ? "Hình vẽ" : "Bảng HTML"}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-1">
          <button
            type="button"
            data-testid="ws3-zoom-out"
            onClick={() => onZoomChange(Math.max(80, zoomPercent - 20))}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-xs font-bold text-slate-700"
          >
            -
          </button>
          <span data-testid="ws3-zoom-label" className="min-w-[44px] text-center text-xs font-semibold text-slate-700">
            {zoomPercent}%
          </span>
          <button
            type="button"
            data-testid="ws3-zoom-in"
            onClick={() => onZoomChange(Math.min(180, zoomPercent + 20))}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-xs font-bold text-slate-700"
          >
            +
          </button>
        </div>
      </div>

      {/* Legend & Current Focus Coordinates */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2 text-xs text-slate-600">
        <div className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-xs bg-blue-600" /> QUESTION
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-xs bg-orange-600" /> FIGURE_CROP
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-xs bg-purple-600" /> HTML_TABLE
          </span>
        </div>
        {activeRegion && (
          <span data-testid="ws3-active-bbox-coords" className="font-mono text-[11px] font-semibold text-slate-800">
            Focus {selectedItem.stableCode} ({activeRegion.kind}): x0={activeRegion.box.x0}, y0={activeRegion.box.y0}, x1=
            {activeRegion.box.x1}, y1={activeRegion.box.y1}
          </span>
        )}
      </div>

      {/* PDF Canvas + SVG Drag Overlay */}
      <div className="overflow-auto bg-slate-100 p-4">
        <div
          data-testid="ws3-pdf-canvas"
          style={{ width: `${zoomPercent}%` }}
          className="mx-auto min-w-[320px] max-w-full rounded-xl border border-slate-300 bg-white shadow-md"
        >
          <svg
            ref={svgRef}
            data-testid="ws3-bbox-svg"
            viewBox={`0 0 ${page.width} ${page.height}`}
            className="h-auto w-full cursor-crosshair select-none"
            onMouseDown={(e) => startDrag(e.clientX, e.clientY)}
            onMouseMove={(e) => moveDrag(e.clientX, e.clientY)}
            onMouseUp={(e) => endDrag(e.clientX, e.clientY)}
          >
            {/* Simulated PDF Page Background */}
            <rect x={0} y={0} width={page.width} height={page.height} fill="#ffffff" />
            <text x={36} y={44} fontSize={13} fontWeight="bold" fill="#334155">
              {page.heading}
            </text>
            <line x1={36} y1={58} x2={page.width - 36} y2={58} stroke="#e2e8f0" strokeWidth={1.5} />

            {page.previewLines.map((line, idx) => (
              <text key={idx} x={48} y={84 + idx * 140} fontSize={11} fill="#64748b">
                {line}
              </text>
            ))}

            {/* Existing BBox Regions */}
            {visibleRegions.map(({ itemId, region }) => {
              const palette = BBOX_REGION_COLORS[region.kind];
              const isSelected = itemId === selectedItem.id;
              const width = Math.max(8, region.box.x1 - region.box.x0);
              const height = Math.max(8, region.box.y1 - region.box.y0);
              return (
                <g
                  key={region.id}
                  data-testid={`ws3-bbox-rect-${region.id}`}
                  data-bbox-kind={region.kind}
                  data-focused={isSelected ? "true" : "false"}
                  onMouseDown={(e) => {
                    onSelectItem(itemId);
                    onSelectRegionKind(region.kind);
                    // Propagate when user wants to redraw from inside box only if holding drag cleanly:
                    if (e.shiftKey) {
                      e.stopPropagation();
                    }
                  }}
                >
                  <rect
                    x={region.box.x0}
                    y={region.box.y0}
                    width={width}
                    height={height}
                    fill={palette.fill}
                    stroke={palette.stroke}
                    strokeWidth={isSelected ? 3 : 1.5}
                    strokeDasharray={isSelected ? undefined : "4 2"}
                    rx={4}
                  />
                  <rect
                    x={region.box.x0}
                    y={Math.max(0, region.box.y0 - 18)}
                    width={Math.min(220, Math.max(110, region.label.length * 6.5))}
                    height={18}
                    fill={palette.stroke}
                    rx={3}
                  />
                  <text
                    x={region.box.x0 + 6}
                    y={Math.max(12, region.box.y0 - 5)}
                    fontSize={10}
                    fontWeight="bold"
                    fill="#ffffff"
                  >
                    {region.label}
                  </text>
                </g>
              );
            })}

            {/* Live Re-Crop Draft Overlay */}
            {previewDraftBox && (
              <rect
                data-testid="ws3-drag-draft-rect"
                x={previewDraftBox.x0}
                y={previewDraftBox.y0}
                width={previewDraftBox.x1 - previewDraftBox.x0}
                height={previewDraftBox.y1 - previewDraftBox.y0}
                fill="rgba(16, 185, 129, 0.16)"
                stroke="#059669"
                strokeWidth={2.5}
                strokeDasharray="5 3"
              />
            )}
          </svg>
        </div>
      </div>

      <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
        Kéo-thả chuột trực tiếp trên trang PDF để cắt lại vùng{" "}
        <span className="font-semibold text-slate-800">{activeRegionKind}</span> cho{" "}
        <span className="font-semibold text-slate-800">{selectedItem.stableCode}</span> (tự động kẹp biên trang qua{" "}
        <code>normalizeAndClampBBox</code>).
      </p>
    </section>
  );
}
