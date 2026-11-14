import React from "react";
import { ExamRoomShell } from "@/components/exam-room/ExamRoomShell";

export interface ExamRoomPageProps {
  params: Promise<{ attempt_id: string }>;
  searchParams?: Promise<{ demo?: string }>;
}

export default async function ExamRoomPage({ params, searchParams }: ExamRoomPageProps) {
  const { attempt_id: attemptId } = await params;
  const resolvedSearch = searchParams ? await searchParams : {};
  const isDemoMode = resolvedSearch.demo === "1";

  return <ExamRoomShell attemptId={attemptId} isDemoMode={isDemoMode} />;
}
