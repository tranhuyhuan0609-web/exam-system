import type { ReviewerRightsStatus as RightsStatus } from "@/lib/bbox-reviewer";

export interface OutboxLatencyPoint {
  readonly label: string;
  readonly lagSeconds: number;
  readonly dlqCount: number;
}

export interface DlqItemRecord {
  readonly id: string;
  readonly eventType: string;
  readonly errorCode: string;
  readonly attempts: number;
  readonly lastSeenAt: string;
  readonly status: "FAILED_DLQ" | "REPLAYED";
}

export interface FeatureFlagRecord {
  readonly key: string;
  readonly description: string;
  readonly enabled: boolean;
  readonly pinnedVersion: string;
}

export interface AuditEventRecord {
  readonly id: string;
  readonly action: string;
  readonly actorEmail: string;
  readonly targetRef: string;
  readonly reason: string;
  readonly createdAt: string;
}

export interface CrawlerSourceMonitorRecord {
  readonly id: string;
  readonly name: string;
  readonly baseUrl: string;
  readonly rightsStatus: RightsStatus;
  readonly policyStatus: "ACTIVE" | "PAUSED_DRIFT" | "PAUSED_MANUAL" | "REVOKED";
  readonly emptyPageCount: number;
  readonly consecutivePages: number;
}

export interface DrivePairingCandidateRecord {
  readonly id: string;
  readonly examFileName: string;
  readonly keyFileName: string;
  readonly folderName: string;
  readonly filenameSimilarity: number;
  readonly sameFolder: boolean;
  readonly titleOverlap: number;
  readonly examQuestionCount: number;
  readonly keyQuestionCount: number;
  readonly status: "PENDING_CONFIRM" | "CONFIRMED";
}

export interface TakedownCaseRecord {
  readonly id: string;
  readonly targetType: "CONTENT_ITEM" | "QUESTION" | "SOURCE";
  readonly targetId: string;
  readonly targetTitle: string;
  readonly currentRightsStatus: RightsStatus;
  readonly claimantName: string;
  readonly legalNoticeRef: string;
  readonly status: "OPEN" | "EXECUTED";
  readonly executedReason?: string;
}

export interface AdminOpsDashboardData {
  readonly oldestQueuedAgeSeconds: number;
  readonly dlqItems: readonly DlqItemRecord[];
  readonly latencySeries: readonly OutboxLatencyPoint[];
  readonly featureFlags: readonly FeatureFlagRecord[];
  readonly auditEvents: readonly AuditEventRecord[];
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string;
  readonly rawAssetUrl: string;
}

export interface AdminSourcesConsoleData {
  readonly driveConnected: boolean;
  readonly driveScope: "drive.file" | "service_account";
  readonly serviceAccountEmail: string;
  readonly pickedFileNames: readonly string[];
  readonly crawlerSources: readonly CrawlerSourceMonitorRecord[];
  readonly pairingCandidates: readonly DrivePairingCandidateRecord[];
  readonly auditEvents: readonly AuditEventRecord[];
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string;
  readonly rawAssetUrl: string;
}

export interface AdminTakedownsConsoleData {
  readonly cases: readonly TakedownCaseRecord[];
  readonly auditEvents: readonly AuditEventRecord[];
  readonly requiresProAsset: boolean;
  readonly previewPdfUrl: string;
  readonly rawAssetUrl: string;
}

export function createDemoAdminOpsData(): AdminOpsDashboardData {
  return {
    oldestQueuedAgeSeconds: 112, // Intentionally > 90s SLO to showcase live SLO_BREACH alerting
    dlqItems: [
      {
        id: "dlq-101",
        eventType: "ingestion.document.parsed",
        errorCode: "PARSER_TABLE_COLSPAN_OVERFLOW",
        attempts: 3,
        lastSeenAt: "2026-11-14 18:10:04",
        status: "FAILED_DLQ",
      },
      {
        id: "dlq-102",
        eventType: "search.projection.reindex",
        errorCode: "PGTRGM_TIMEOUT",
        attempts: 2,
        lastSeenAt: "2026-11-14 18:14:22",
        status: "FAILED_DLQ",
      },
    ],
    latencySeries: [
      { label: "18:00", lagSeconds: 18, dlqCount: 0 },
      { label: "18:05", lagSeconds: 42, dlqCount: 1 },
      { label: "18:10", lagSeconds: 95, dlqCount: 2 },
      { label: "18:15", lagSeconds: 112, dlqCount: 2 },
    ],
    featureFlags: [
      {
        key: "parser.vlm_table_fallback",
        description: "Ghim parser trích bảng 3 cột Hướng dẫn chấm",
        enabled: true,
        pinnedVersion: "parser-a06-v2.4.1",
      },
      {
        key: "ai.tutor.strict_exam_lock",
        description: "Chặn cứng AI Tutor khi thí sinh có attempt IN_PROGRESS",
        enabled: true,
        pinnedVersion: "guard-e02-v1.0.0",
      },
    ],
    auditEvents: [
      {
        id: "aud-9001",
        action: "PIN_PARSER_VERSION",
        actorEmail: "ops@h2edu.vn",
        targetRef: "flag:parser.vlm_table_fallback",
        reason: "Ghim parser A06 phiên bản 2.4.1 ổn định cho bảng 3 cột",
        createdAt: "2026-11-14 17:45:00",
      },
    ],
    requiresProAsset: false,
    previewPdfUrl: "/previews/ops-audit-sample.pdf",
    rawAssetUrl: "/assets/ops-audit-full.pdf",
  };
}

export function createDemoAdminSourcesData(): AdminSourcesConsoleData {
  return {
    driveConnected: true,
    driveScope: "drive.file",
    serviceAccountEmail: "ulp-drive-sync@h2edu-prod.iam.gserviceaccount.com",
    pickedFileNames: [
      "De_thi_thu_THPT_Chuyen_KHTN_Toan_2025.pdf",
      "HDC_Chuyen_KHTN_Toan_2025.docx",
      "Bo_de_Vat_Ly_12_KNTT.zip",
    ],
    crawlerSources: [
      {
        id: "src-crawl-01",
        name: "VTED Public Bank Mirror",
        baseUrl: "https://example-licensed-bank.vn/toan-12",
        rightsStatus: "LICENSED",
        policyStatus: "ACTIVE",
        emptyPageCount: 2,
        consecutivePages: 30,
      },
      {
        id: "src-crawl-02",
        name: "ToanMath Archive Node",
        baseUrl: "https://example-open-math.vn/de-thi",
        rightsStatus: "ALLOWED_DERIVATIVE",
        policyStatus: "PAUSED_DRIFT",
        emptyPageCount: 7, // 7/30 = 23.3% > 15% across 30 consecutive pages -> PAUSED_DRIFT!
        consecutivePages: 30,
      },
    ],
    pairingCandidates: [
      {
        id: "pair-01",
        examFileName: "De_Toan_12_KNTT_So_01.pdf",
        keyFileName: "HDC_De_Toan_12_KNTT_So_01.pdf",
        folderName: "THPTQG_2025/Toan_12",
        filenameSimilarity: 0.94,
        sameFolder: true,
        titleOverlap: 0.92,
        examQuestionCount: 22,
        keyQuestionCount: 22,
        status: "PENDING_CONFIRM",
      },
      {
        id: "pair-02",
        examFileName: "De_Vat_Ly_12_Lan_2.pdf",
        keyFileName: "Dap_An_Vat_Ly_12_Lan_2.docx",
        folderName: "Archive_Khac",
        filenameSimilarity: 0.65,
        sameFolder: false,
        titleOverlap: 0.72,
        examQuestionCount: 28,
        keyQuestionCount: 25, // Count mismatch -> canConfirm = false!
        status: "PENDING_CONFIRM",
      },
    ],
    auditEvents: [],
    requiresProAsset: false,
    previewPdfUrl: "/previews/sources-manifest.pdf",
    rawAssetUrl: "/assets/sources-manifest-full.pdf",
  };
}

export function createDemoAdminTakedownsData(): AdminTakedownsConsoleData {
  return {
    cases: [
      {
        id: "td-2026-01",
        targetType: "CONTENT_ITEM",
        targetId: "ci-nxb-gd-0042",
        targetTitle: "Chuyên đề Hình học không gian nâng cao (NXB A)",
        currentRightsStatus: "PENDING_REVIEW",
        claimantName: "Đại diện Pháp chế NXB Giáo Dục A",
        legalNoticeRef: "DMCA-VN-2026-1114-01",
        status: "OPEN",
      },
      {
        id: "td-2026-02",
        targetType: "QUESTION",
        targetId: "q-vted-8812",
        targetTitle: "Câu hỏi cực trị số phức tham số m (LEG-8812)",
        currentRightsStatus: "LICENSED",
        claimantName: "Tác giả độc lập Nguyễn Văn B",
        legalNoticeRef: "COPYRIGHT-2026-098",
        status: "OPEN",
      },
    ],
    auditEvents: [
      {
        id: "aud-td-00",
        action: "CREATE_TAKEDOWN_CASE",
        actorEmail: "legal@h2edu.vn",
        targetRef: "td-2026-01",
        reason: "Tiếp nhận công văn yêu cầu gỡ bản quyền số DMCA-VN-2026-1114-01",
        createdAt: "2026-11-14 16:30:00",
      },
    ],
    requiresProAsset: false,
    previewPdfUrl: "/previews/takedown-notice.pdf",
    rawAssetUrl: "/assets/takedown-notice-full.pdf",
  };
}
