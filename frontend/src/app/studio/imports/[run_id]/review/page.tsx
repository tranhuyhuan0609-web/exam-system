import React from "react";
import { BboxReviewerShell } from "@/components/studio-review/BboxReviewerShell";
import type { UserTier } from "@/lib/ui-state-machine";

export interface ImportReviewPageProps {
  params: Promise<{ run_id: string }>;
  searchParams?: Promise<{ tier?: string }>;
}

function parseStudioTier(raw?: string): UserTier {
  if (raw === "FREE" || raw === "STUDENT_PRO" || raw === "SCHOOL_ENTERPRISE") {
    return raw;
  }
  return "TEACHER_PRO";
}

export default async function ImportReviewPage({ params, searchParams }: ImportReviewPageProps) {
  const { run_id: runId } = await params;
  const resolvedSearch = searchParams ? await searchParams : {};
  const initialUserTier = parseStudioTier(resolvedSearch.tier);

  return <BboxReviewerShell runId={runId} initialUserTier={initialUserTier} />;
}
