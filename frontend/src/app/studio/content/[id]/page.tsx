import React from "react";
import { StudioContentEditorShell } from "@/components/studio-authoring/StudioContentEditorShell";
import type { UserTier } from "@/lib/ui-state-machine";

export interface StudioContentPageProps {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ tier?: string }>;
}

function parseStudioTier(raw?: string): UserTier {
  if (raw === "FREE" || raw === "STUDENT_PRO" || raw === "SCHOOL_ENTERPRISE") {
    return raw;
  }
  return "TEACHER_PRO";
}

export default async function StudioContentPage({ params, searchParams }: StudioContentPageProps) {
  const { id } = await params;
  const resolvedSearch = searchParams ? await searchParams : {};
  const initialUserTier = parseStudioTier(resolvedSearch.tier);

  return <StudioContentEditorShell contentId={id} initialUserTier={initialUserTier} />;
}
