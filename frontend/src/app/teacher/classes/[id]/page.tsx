import React from "react";
import type { UserTier } from "@/lib/ui-state-machine";
import { TeacherClassLmsShell } from "@/components/teacher-lms/TeacherClassLmsShell";

export interface TeacherClassPageProps {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ tier?: string }>;
}

function parseTeacherTier(raw?: string): UserTier {
  if (raw === "FREE" || raw === "STUDENT_PRO" || raw === "SCHOOL_ENTERPRISE") {
    return raw;
  }
  return "TEACHER_PRO";
}

export default async function TeacherClassPage({ params, searchParams }: TeacherClassPageProps) {
  const { id } = await params;
  const resolvedSearch = searchParams ? await searchParams : {};
  const initialUserTier = parseTeacherTier(resolvedSearch.tier);

  return <TeacherClassLmsShell classId={id} initialUserTier={initialUserTier} />;
}
