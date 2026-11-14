"""Parallel Wave Orchestrator for Claude Code + Gemini 4 Hydrogen (ULP V11.3).

Provides deterministic wave definitions (WAVE-1..WAVE-8), strict file-lane isolation
(so Claude and Gemini never collide on Alembic migrations, model_registry.py, or main.py),
automated Git worktree preparation, copy-paste prompt generation, and pre-merge lane audits.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GLOBAL_GEMINI_FORBIDDEN: tuple[str, ...] = (
    "alembic/versions/",
    "app/model_registry.py",
    "app/main.py",
    ".importlinter",
    "CLAUDE.md",
    "docs/architecture/chapters/",
)

GLOBAL_CLAUDE_FORBIDDEN: tuple[str, ...] = (
    "frontend/",
    ".importlinter",
    "docs/architecture/chapters/",
)


@dataclass(frozen=True)
class AgentLaneSpec:
    agent: str
    recommended_model: str
    branch: str
    tasks: tuple[str, ...]
    allowed_prefixes: tuple[str, ...]
    forbidden_prefixes: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class WaveSpec:
    wave_id: str
    title: str
    claude: AgentLaneSpec
    gemini: AgentLaneSpec


WAVES: tuple[WaveSpec, ...] = (
    WaveSpec(
        wave_id="WAVE-1",
        title="Wave 0 PDF Engine PR C (ADR-010) + API Routers for Existing DB Modules (G03, B04, B06)",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Sonnet (TASK-01, TASK-03) + Claude Opus (TASK-02 redaction)",
            branch="feat/w1-claude-pdf-prc",
            tasks=("TASK-01", "TASK-02", "TASK-03"),
            allowed_prefixes=(
                "app/infrastructure/pdf_engine.py",
                "app/application/pdf_preview.py",
                "app/application/exam_pdf.py",
                "app/pipeline/harvest/anthologies.py",
                "app/pipeline/use_cases/library_split.py",
                "app/ai/figures.py",
                "app/ai/essay_exam.py",
                "app/ai/normalize.py",
                "app/cli.py",
                "universal_ingestor.py",
                "requirements.txt",
                "requirements.lock",
                "app/static/fonts/",
                "docs/architecture/PDF_ENGINE_MIGRATION.md",
                "tests/",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN
            + (
                "app/modules/outbox/",
                "app/modules/taxonomy/",
                "app/modules/curriculum/",
            ),
            summary="Complete PDF Engine PR C, remove pymupdf/fitz from all 8 app files + 9 tests + requirements.txt.",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w1-gemini-outbox-tax-curr-api",
            tasks=("TASK-04", "TASK-10"),
            allowed_prefixes=(
                "app/modules/outbox/",
                "app/modules/taxonomy/",
                "app/modules/curriculum/",
                "tests/test_outbox_api.py",
                "tests/test_taxonomy_curriculum_api.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build DB service & /api/v1 routers for G03 outbox, B04 taxonomy, and B06 curriculum.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-2",
        title="Sprint 1 Security/Governance DB (G01, F05, F04) + Sprint 1 Assets/Ingestion (B03, A01)",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Opus / Fable (High-security RBAC, RLS, legacy_users & Vault Transit)",
            branch="feat/w2-claude-s1-security-db",
            tasks=("TASK-05", "TASK-06", "TASK-07"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/models.py",
                "app/modules/config/",
                "app/modules/access/",
                "app/modules/privacy/",
                "tests/test_config_db.py",
                "tests/test_access_db.py",
                "tests/test_privacy_db.py",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN
            + (
                "app/modules/assets/",
                "app/modules/ingestion/",
            ),
            summary="Alembic migrations + ORM models + services + routers for G01 config, F05 access, F04 privacy.",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w2-gemini-s1-assets-ingestion",
            tasks=("TASK-08", "TASK-09"),
            allowed_prefixes=(
                "app/modules/assets/",
                "app/modules/ingestion/",
                "tests/test_assets_db.py",
                "tests/test_ingestion_db.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build ORM models.py, service.py, and router.py for B03 assets and A01 ingestion.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-3",
        title="Sprint 2 Content, Authoring, AI Gateway, Audit DB + Next.js Workspace 2 (Exam Room)",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Sonnet (TASK-11..13) + Claude Opus (TASK-14 partitioned audit F-01)",
            branch="feat/w3-claude-s2-content-ai-audit",
            tasks=("TASK-11", "TASK-12", "TASK-13", "TASK-14"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/modules/assets/",
                "app/modules/ingestion/",
                "app/modules/content/",
                "app/modules/authoring/",
                "app/modules/ai_gateway/",
                "app/modules/audit/",
                "tests/test_content_db.py",
                "tests/test_authoring_db.py",
                "tests/test_ai_gateway_db.py",
                "tests/test_audit_db.py",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN
            + (
                "app/modules/parsers/",
                "app/modules/layout/",
            ),
            summary="Wire B03/A01 migration + build B01 content, B02 authoring, E01 ai_gateway, G02 audit.",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w3-gemini-ws2-and-s3-parsers",
            tasks=("TASK-24", "TASK-15"),
            allowed_prefixes=(
                "frontend/",
                "app/modules/parsers/",
                "app/modules/layout/",
                "tests/test_parsers_layout_db.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build Next.js Workspace 2 (/phong-thi/[attempt_id]) + ORM/service/router for A05 & A06.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-4",
        title="Sprint 3 & 4 Extraction, Review, Quality, Questions (C01 F-02) + Next.js Workspace 1 (Study Hub)",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Sonnet (TASK-15, TASK-16) + Claude Opus (TASK-17 C01 answer-key revoke & F-02)",
            branch="feat/w4-claude-s3-s4-questions",
            tasks=("TASK-15", "TASK-16", "TASK-17"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/models.py",
                "app/modules/parsers/",
                "app/modules/layout/",
                "app/modules/extraction/",
                "app/modules/review/",
                "app/modules/quality/",
                "app/modules/questions/",
                "tests/test_parsers_layout_db.py",
                "tests/test_extraction_review_quality_db.py",
                "tests/test_questions_db.py",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN
            + (
                "app/modules/dedup/",
                "app/modules/exam_import/",
                "app/modules/search/",
                "app/modules/frameworks/",
            ),
            summary="Alembic migrations & DB wiring for A05, A06, A07, A09, C02, and C01 questions (legacy_questions).",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w4-gemini-ws1-and-s4-modules",
            tasks=("TASK-25", "TASK-18", "TASK-19"),
            allowed_prefixes=(
                "frontend/",
                "app/modules/dedup/",
                "app/modules/exam_import/",
                "app/modules/search/",
                "app/modules/frameworks/",
                "tests/test_dedup_exam_import_db.py",
                "tests/test_search_frameworks_db.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build Next.js Workspace 1 (Study Hub) + ORM/service/router for A08, A04, B07, C03.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-5",
        title="Sprint 4-6 Assembly, Delivery, Fast Autosave (H-04), Grading + Next.js Workspaces 3 & 4 + Backfill CLI",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Sonnet (TASK-18, 19, 20, 22) + Claude Opus (TASK-21 C06 FOR UPDATE Autosave)",
            branch="feat/w5-claude-s5-s6-exam-engine",
            tasks=("TASK-18", "TASK-19", "TASK-20", "TASK-21", "TASK-22"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/modules/dedup/",
                "app/modules/exam_import/",
                "app/modules/search/",
                "app/modules/frameworks/",
                "app/modules/assembly/",
                "app/modules/delivery/",
                "app/modules/attempts/",
                "app/modules/grading/",
                "tests/test_dedup_exam_import_db.py",
                "tests/test_search_frameworks_db.py",
                "tests/test_assembly_delivery_db.py",
                "tests/test_attempts_autosave_db.py",
                "tests/test_grading_e2e_db.py",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN + ("app/ops/run_backfill.py",),
            summary="Complete Track 1 MVP DB & APIs: A08, A04, B07, C03, C04, C05, C06 (Fast Autosave), C07 (Grading).",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w5-gemini-ws3-ws4-and-backfill",
            tasks=("TASK-26", "TASK-23"),
            allowed_prefixes=(
                "frontend/",
                "app/ops/run_backfill.py",
                "tests/test_backfill_execution.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build Next.js Workspaces 3 & 4 (BBox Reviewer & Block Studio) + idempotent Backfill CLI runner.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-6",
        title="Track 2 Sprint 7-8 (Knowledge Graph, LMS, Notifications) + Next.js Workspaces 5 & 6",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Sonnet",
            branch="feat/w6-claude-s7-s8-lms",
            tasks=("TASK-28", "TASK-29", "TASK-30"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/modules/knowledge_graph/",
                "app/modules/dashboard/",
                "app/modules/learning_path/",
                "app/modules/practice/",
                "app/modules/error_notebook/",
                "app/modules/classes/",
                "app/modules/assignments/",
                "app/modules/gradebook/",
                "app/modules/notifications/",
                "tests/",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN
            + (
                "app/modules/flashcards/",
                "app/modules/tutor/",
                "app/modules/explanations/",
            ),
            summary="Alembic migrations + DB services + routers for B05, D01, D02, D03, D05, F01, F02, F03, F06.",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w6-gemini-ws5-ws6-and-s9",
            tasks=("TASK-27", "TASK-31"),
            allowed_prefixes=(
                "frontend/",
                "app/modules/flashcards/",
                "app/modules/tutor/",
                "app/modules/explanations/",
                "tests/test_sprint9_modules_db.py",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build Next.js Workspaces 5 & 6 (Matrix Builder & Admin Ops) + ORM/service/router for Sprint 9.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-7",
        title="Sprint 9 (GATE-08) & Track 3 Sprint 10-12 (Crawler, Drive, IRT, Billing) + Gate 2b Alembic Verifier",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Opus (TASK-31 Tutor Exam Lock & TASK-34 Billing) + Claude Sonnet (TASK-32, 33)",
            branch="feat/w7-claude-s9-s12-expansion",
            tasks=("TASK-31", "TASK-32", "TASK-33", "TASK-34"),
            allowed_prefixes=(
                "alembic/versions/",
                "app/model_registry.py",
                "app/main.py",
                "app/modules/flashcards/",
                "app/modules/tutor/",
                "app/modules/explanations/",
                "app/modules/crawler/",
                "app/modules/drive_sync/",
                "app/modules/generation/",
                "app/modules/mastery/",
                "app/modules/recommendations/",
                "app/modules/teacher_copilot/",
                "app/modules/analytics/",
                "app/modules/billing/",
                "app/modules/vertical_packs/",
                "tests/",
            ),
            forbidden_prefixes=GLOBAL_CLAUDE_FORBIDDEN + ("tests/security/test_alembic_matches_chapter4.py",),
            summary="Wire migrations + DB services + routers for all remaining Sprint 9-12 modules (47/47 live).",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen",
            branch="feat/w7-gemini-gate2b-alembic-audit",
            tasks=("TASK-35",),
            allowed_prefixes=(
                "tests/security/test_alembic_matches_chapter4.py",
                "app/ops/",
            ),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Build Gate 2b full Alembic-to-Chapter-4 RLS/Grant/Trigger verification suite.",
        ),
    ),
    WaveSpec(
        wave_id="WAVE-8",
        title="Final Gate 2b Verification on Application DB & Release Freeze (GATE-01..GATE-10)",
        claude=AgentLaneSpec(
            agent="claude",
            recommended_model="Claude Fable / Antigravity Orchestrator",
            branch="feat/w8-release-gates-freeze",
            tasks=("TASK-35", "TASK-36"),
            allowed_prefixes=("app/", "tests/", "docs/architecture/"),
            forbidden_prefixes=(".importlinter", "docs/architecture/chapters/"),
            summary="Run full Alembic upgrade head, Gate 2b verification, mypy, SBOM audit, and GATE-01..10 sign-off.",
        ),
        gemini=AgentLaneSpec(
            agent="gemini",
            recommended_model="Gemini 4 Hydrogen (Frontend E2E & Polish)",
            branch="feat/w8-gemini-frontend-polish",
            tasks=("TASK-36",),
            allowed_prefixes=("frontend/",),
            forbidden_prefixes=GLOBAL_GEMINI_FORBIDDEN,
            summary="Run full frontend typecheck, Vitest suite, and 6-Workspace UI state verification.",
        ),
    ),
)

WAVE_BY_ID: dict[str, WaveSpec] = {w.wave_id: w for w in WAVES}


def get_wave(wave_id: str) -> WaveSpec:
    norm = wave_id.strip().upper()
    if not norm.startswith("WAVE-"):
        norm = f"WAVE-{norm.lstrip('W')}"
    if norm not in WAVE_BY_ID:
        valid = ", ".join(WAVE_BY_ID)
        raise ValueError(f"Unknown wave '{wave_id}'. Valid waves: {valid}")
    return WAVE_BY_ID[norm]


def check_paths_against_lane(lane: AgentLaneSpec, changed_paths: list[str]) -> tuple[bool, list[str]]:
    """Verify every modified path is allowed by `lane.allowed_prefixes` and not in `lane.forbidden_prefixes`."""
    violations: list[str] = []
    for raw in changed_paths:
        norm = raw.replace("\\", "/").lstrip("./")
        if not norm:
            continue
        for forbidden in lane.forbidden_prefixes:
            if norm == forbidden.rstrip("/") or norm.startswith(forbidden):
                violations.append(f"FORBIDDEN_PATH ({lane.agent}): '{norm}' matches forbidden prefix '{forbidden}'")
                break
        else:
            if not any(norm == allowed.rstrip("/") or norm.startswith(allowed) for allowed in lane.allowed_prefixes):
                violations.append(f"OUT_OF_LANE_PATH ({lane.agent}): '{norm}' is outside allowed prefixes")
    return (len(violations) == 0, violations)


def build_agent_prompt(wave: WaveSpec, agent: str) -> str:
    lane = wave.claude if agent == "claude" else wave.gemini
    tasks_str = " -> ".join(lane.tasks)
    allowed_lines = "\n".join(f"  - `{p}`" for p in lane.allowed_prefixes)
    forbidden_lines = "\n".join(f"  - `{p}`" for p in lane.forbidden_prefixes)
    return (
        f"=== PROMPT GIAO VIỆC CHO {lane.agent.upper()} ({wave.wave_id}: {wave.title}) ===\n"
        f"Model khuyến nghị: {lane.recommended_model}\n"
        f"Nhánh làm việc: `{lane.branch}`\n\n"
        f"Hãy đọc `CLAUDE.md`, `docs/architecture/CLAUDE_CODE_SEQUENTIAL_PLAN.md` và "
        f"`docs/architecture/PARALLEL_CLAUDE_GEMINI_ORCHESTRATION.md`.\n"
        f"Trong đợt `{wave.wave_id}`, bạn phụ trách luồng `{lane.agent.upper()}` "
        f"với chuỗi task tuần tự: **{tasks_str}**.\n"
        f"Mục tiêu: {lane.summary}\n\n"
        f"QUY TẮC BẮT BUỘC (KHÔNG ĐƯỢC LÀM TRÁI):\n"
        f"1. GIAO THEO LÔ CÙNG MODEL (SAME-MODEL WAVE BATCHING): Các task dùng CÙNG MỘT MODEL trong đợt này "
        f"thì thực hiện LIÊN TỤC theo đúng thứ tự ({tasks_str}) trong một lượt (xong mỗi task vẫn tự chạy test "
        f"và commit riêng từng task cho sạch lịch sử Git); CHỈ DỪNG LẠI khi chuyển sang task yêu cầu model khác "
        f"(ví dụ chuyển giữa Sonnet và Opus) hoặc khi đã hoàn thành toàn bộ các task cùng model của đợt.\n"
        f"2. Bám sát 100% đặc tả trong `docs/architecture/chapters/` và tái sử dụng các hàm Pure Core đã có sẵn "
        f"trong `app/modules/<tên>/internal/`. Tuyệt đối không tự đặt tên module/bảng/cột lệch đặc tả.\n"
        f"3. PHẠM VI FILE ĐƯỢC PHÉP SỬA/TẠO (Strict Lane Ownership):\n{allowed_lines}\n"
        f"4. PHẠM VI FILE CẤM TUYỆT ĐỐI (để không đụng độ với luồng song song):\n{forbidden_lines}\n"
        f"5. Trước khi báo cáo hoàn thành mỗi Task, bắt buộc chạy:\n"
        f"   - `ruff check .`\n"
        f"   - `lint-imports`\n"
        f"   - `bash ci/check_gate_ids.sh`\n"
        f"   - `pytest -q <file_test_của_task>`\n"
        f"   - `python scripts/orchestrate.py audit {wave.wave_id} --agent {lane.agent}`\n"
    )


def _cmd_list() -> int:
    print("ULP V11.3 — PARALLEL ORCHESTRATION WAVES (CLAUDE + GEMINI 4 HYDROGEN)")
    print("=" * 100)
    for w in WAVES:
        print(f"\n[{w.wave_id}] {w.title}")
        print(f"  • CLAUDE Lane ({w.claude.recommended_model})")
        print(f"      Branch : {w.claude.branch}")
        print(f"      Tasks  : {', '.join(w.claude.tasks)} — {w.claude.summary}")
        print(f"  • GEMINI Lane ({w.gemini.recommended_model})")
        print(f"      Branch : {w.gemini.branch}")
        print(f"      Tasks  : {', '.join(w.gemini.tasks)} — {w.gemini.summary}")
    return 0


def _cmd_prompt(wave_id: str, agent: str) -> int:
    wave = get_wave(wave_id)
    if agent in ("claude", "both"):
        print(build_agent_prompt(wave, "claude"))
    if agent == "both":
        print("\n" + ("-" * 100) + "\n")
    if agent in ("gemini", "both"):
        print(build_agent_prompt(wave, "gemini"))
    return 0


def _cmd_prepare(wave_id: str, wt_root: Path) -> int:
    wave = get_wave(wave_id)
    wt_root.mkdir(parents=True, exist_ok=True)
    num = wave.wave_id.split("-")[1].lower()
    for lane in (wave.claude, wave.gemini):
        wt_dir = wt_root / f"{lane.agent}-w{num}"
        print(f"[prepare] Setting up {lane.agent} worktree at {wt_dir} on branch {lane.branch}...")
        if wt_dir.exists():
            print(f"  Worktree {wt_dir} already exists; skipping git worktree add.")
        else:
            subprocess.run(
                ["git", "worktree", "add", "-B", lane.branch, str(wt_dir), "HEAD"],
                check=True,
            )
    print(f"[prepare] Done. Run `python scripts/orchestrate.py prompt {wave.wave_id} --agent both` to get prompts.")
    return 0


def _cmd_audit(wave_id: str, agent: str, worktree: Path, base_ref: str) -> int:
    wave = get_wave(wave_id)
    lane = wave.claude if agent == "claude" else wave.gemini
    diff_proc = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    status_proc = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    changed: set[str] = set()
    for line in diff_proc.stdout.splitlines():
        if line.strip():
            changed.add(line.strip())
    for line in status_proc.stdout.splitlines():
        if len(line) > 3:
            path_part = line[3:].strip().split(" -> ")[-1]
            changed.add(path_part)

    ok, violations = check_paths_against_lane(lane, sorted(changed))
    if not ok:
        print(f"[FAIL] {wave.wave_id} ({lane.agent}) lane boundary audit failed:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(
        f"[OK] {wave.wave_id} ({lane.agent}) lane boundary audit passed "
        f"({len(changed)} modified file(s), 0 lane violations)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ULP V11.3 Parallel Wave Orchestrator (Claude + Gemini)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all 8 parallel waves and model assignments")

    p_prompt = sub.add_parser("prompt", help="Generate copy-paste prompt for a wave")
    p_prompt.add_argument("wave", help="Wave ID (e.g. WAVE-1 or 1)")
    p_prompt.add_argument("--agent", choices=("claude", "gemini", "both"), default="both")

    p_prep = sub.add_parser("prepare", help="Create isolated git worktrees for Claude and Gemini")
    p_prep.add_argument("wave", help="Wave ID (e.g. WAVE-1 or 1)")
    p_prep.add_argument("--wt-root", type=Path, default=Path("../exam-wt"))

    p_audit = sub.add_parser("audit", help="Verify modified files in a worktree respect wave lane boundaries")
    p_audit.add_argument("wave", help="Wave ID (e.g. WAVE-1 or 1)")
    p_audit.add_argument("--agent", choices=("claude", "gemini"), required=True)
    p_audit.add_argument("--worktree", type=Path, default=Path("."))
    p_audit.add_argument("--base-ref", default="origin/main")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "prompt":
        return _cmd_prompt(args.wave, args.agent)
    if args.cmd == "prepare":
        return _cmd_prepare(args.wave, args.wt_root)
    if args.cmd == "audit":
        return _cmd_audit(args.wave, args.agent, args.worktree, args.base_ref)
    return 1


if __name__ == "__main__":
    sys.exit(main())
