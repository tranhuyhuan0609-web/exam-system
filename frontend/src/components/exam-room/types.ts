import type { ClusterTrueFalseAnswers } from "@/lib/exam-input-policy";
import type { UserTier } from "@/lib/ui-state-machine";

export type ChoiceLetter = "A" | "B" | "C" | "D";
export type ClusterSubKey = "a" | "b" | "c" | "d";

export interface ExamOptionItem {
  readonly key: ChoiceLetter;
  readonly text: string;
}

export interface ClusterStatementItem {
  readonly key: ClusterSubKey;
  readonly text: string;
}

export interface ExamQuestionPart1 {
  readonly questionVersionId: string;
  readonly number: number;
  readonly part: "PART_1";
  readonly stem: string;
  readonly options: readonly ExamOptionItem[];
}

export interface ExamQuestionPart2 {
  readonly questionVersionId: string;
  readonly number: number;
  readonly part: "PART_2";
  readonly stem: string;
  readonly statements: readonly ClusterStatementItem[];
}

export interface ExamQuestionPart3 {
  readonly questionVersionId: string;
  readonly number: number;
  readonly part: "PART_3";
  readonly stem: string;
}

export type ExamQuestion = ExamQuestionPart1 | ExamQuestionPart2 | ExamQuestionPart3;

export interface QuestionAnswerState {
  readonly choice: ChoiceLetter | null;
  readonly eliminated: readonly ChoiceLetter[];
  readonly cluster: ClusterTrueFalseAnswers;
  readonly shortText: string;
  readonly flagged: boolean;
}

export type AnswerMap = Record<string, QuestionAnswerState>;

export interface ExamAttemptData {
  readonly attemptId: string;
  readonly title: string;
  readonly subjectName: string;
  readonly deadlineAtIso: string;
  readonly userTier: UserTier;
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl?: string | null;
  readonly rawAssetUrl?: string | null;
  readonly questions: readonly ExamQuestion[];
  readonly initialAnswers?: AnswerMap;
}

export function createEmptyAnswerState(): QuestionAnswerState {
  return {
    choice: null,
    eliminated: [],
    cluster: { a: null, b: null, c: null, d: null },
    shortText: "",
    flagged: false,
  };
}

export function isPart1Answered(state: QuestionAnswerState | undefined): boolean {
  return state?.choice !== null && state?.choice !== undefined;
}

export function isPart3Answered(state: QuestionAnswerState | undefined): boolean {
  return Boolean(state?.shortText && state.shortText.trim().length > 0);
}

export function createDemoExamAttempt(attemptId: string): ExamAttemptData {
  return {
    attemptId,
    title: "Đề thi thử THPTQG 2025 — Môn Toán (Mẫu chuẩn 3 phần)",
    subjectName: "Toán học",
    deadlineAtIso: new Date(Date.now() + 90 * 60 * 1000).toISOString(),
    userTier: "STUDENT_PRO",
    requiresProAsset: false,
    questions: [
      {
        questionVersionId: "q-p1-1",
        number: 1,
        part: "PART_1",
        stem: "Cho hàm số $y = x^3 - 3x^2 + 2$. Giá trị cực tiểu của hàm số đã cho bằng bao nhiêu?",
        options: [
          { key: "A", text: "$y_{CT} = -2$" },
          { key: "B", text: "$y_{CT} = 2$" },
          { key: "C", text: "$y_{CT} = 0$" },
          { key: "D", text: "$y_{CT} = -4$" },
        ],
      },
      {
        questionVersionId: "q-p1-2",
        number: 2,
        part: "PART_1",
        stem: "Tính tích phân $$I = \\int_{0}^{1} 2x\\,dx$$.",
        options: [
          { key: "A", text: "$I = 1$" },
          { key: "B", text: "$I = 2$" },
          { key: "C", text: "$I = \\frac{1}{2}$" },
          { key: "D", text: "$I = 0$" },
        ],
      },
      {
        questionVersionId: "q-p2-1",
        number: 3,
        part: "PART_2",
        stem: "Trong không gian $Oxyz$, xét mặt cầu $(S): (x-1)^2 + (y+2)^2 + z^2 = 9$. Xét tính đúng/sai của các mệnh đề sau:",
        statements: [
          { key: "a", text: "Tâm mặt cầu $(S)$ có tọa độ là $I(1; -2; 0)$." },
          { key: "b", text: "Bán kính mặt cầu $(S)$ bằng $R = 9$." },
          { key: "c", text: "Điểm $M(1; 1; 0)$ nằm trên mặt cầu $(S)$." },
          { key: "d", text: "Mặt phẳng $(Oxy)$ cắt mặt cầu $(S)$ theo giao tuyến là đường tròn bán kính $3$." },
        ],
      },
      {
        questionVersionId: "q-p3-1",
        number: 4,
        part: "PART_3",
        stem: "Tìm nghiệm thực của phương trình $2^{x} = 1024$ và điền kết quả vào ô trả lời ngắn (tối đa 4 ký tự).",
      },
    ],
  };
}
