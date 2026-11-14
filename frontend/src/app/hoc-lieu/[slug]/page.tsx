import React from "react";
import { StudyHubShell } from "@/components/study-hub/StudyHubShell";
import type { UserTier } from "@/lib/ui-state-machine";

export interface StudyDocumentPageProps {
  params: Promise<{ slug: string }>;
  searchParams?: Promise<{ tier?: string; activeAttempt?: string }>;
}

function parseTier(raw?: string): UserTier {
  if (raw === "STUDENT_PRO" || raw === "TEACHER_PRO" || raw === "SCHOOL_ENTERPRISE") {
    return raw;
  }
  return "FREE";
}

export default async function StudyDocumentPage({
  params,
  searchParams,
}: StudyDocumentPageProps) {
  const { slug } = await params;
  const resolvedSearch = searchParams ? await searchParams : {};
  const initialUserTier = parseTier(resolvedSearch.tier);

  return (
    <StudyHubShell
      slug={slug}
      initialUserTier={initialUserTier}
      activeExamAttemptId={resolvedSearch.activeAttempt ?? null}
    />
  );
}
