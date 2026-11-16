"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/auth-client";
import type { ExamFigure } from "./types";

type FigureState =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly url: string }
  | { readonly status: "failed" };

async function loadSignedUrl(assetId: string): Promise<string> {
  const response = await apiFetch(`/api/v1/assets/${encodeURIComponent(assetId)}/signed-url`, {
    redirectOn401: false,
  });
  const body = (await response.json()) as { signed_url?: unknown };
  if (typeof body.signed_url !== "string" || !body.signed_url) {
    throw new Error("missing signed_url");
  }
  return body.signed_url;
}

function Figure({ figure }: { readonly figure: ExamFigure }) {
  const [state, setState] = useState<FigureState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    loadSignedUrl(figure.assetId).then(
      (url) => {
        if (!cancelled) setState({ status: "ready", url });
      },
      () => {
        if (!cancelled) setState({ status: "failed" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [figure.assetId]);

  if (state.status === "ready") {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- signed, short-lived URL of a private asset
      <img
        src={state.url}
        alt={figure.alt}
        className="max-h-80 max-w-full rounded-lg border border-slate-200 bg-white object-contain"
        onError={() => setState({ status: "failed" })}
      />
    );
  }
  return (
    <div
      role={state.status === "failed" ? "alert" : "status"}
      className="flex h-24 w-48 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 text-center text-xs text-slate-500"
    >
      {state.status === "failed" ? `Không tải được ${figure.alt.toLowerCase()}` : `Đang tải ${figure.alt.toLowerCase()}…`}
    </div>
  );
}

/** The pictures of a question (figures cut from the source paper), fetched through the signed URL of each asset. */
export function QuestionFigures({ figures }: { readonly figures: readonly ExamFigure[] }) {
  if (figures.length === 0) return null;
  return (
    <div data-testid="question-figures" className="mt-4 flex flex-wrap items-start gap-3">
      {figures.map((figure) => (
        <Figure key={figure.assetId} figure={figure} />
      ))}
    </div>
  );
}
