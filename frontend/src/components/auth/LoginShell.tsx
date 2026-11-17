"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import React, { useEffect, useState } from "react";
import {
  ApiRequestError,
  apiFetch,
  buildSessionFromLoginResponse,
  buildSessionFromRawToken,
  clearAuthSession,
  DEMO_ACCOUNTS,
  getAuthSession,
  hasDemoQueryFlag,
  isDevAuthShortcutsEnabled,
  sanitizeRedirectTarget,
  setAuthSession,
  type AuthSession,
  type DemoAccountPreset,
  type LoginPayloadLike,
} from "@/lib/auth-client";

export interface LoginShellProps {
  fetchFn?: typeof fetch;
  onAuthenticated?: (session: AuthSession, redirectTarget: string) => void;
  demoMode?: boolean;
}

export function LoginShell({ fetchFn, onAuthenticated, demoMode }: LoginShellProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextRaw = searchParams.get("next");
  const nextTarget = sanitizeRedirectTarget(nextRaw);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [rawJwtInput, setRawJwtInput] = useState("");
  const [showJwtPanel, setShowJwtPanel] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [endpointMissing404, setEndpointMissing404] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [currentSession, setCurrentSession] = useState<AuthSession | null>(null);
  const [showDemoShortcuts, setShowDemoShortcuts] = useState(Boolean(demoMode));

  useEffect(() => {
    setCurrentSession(getAuthSession());
    const search = typeof window !== "undefined" ? window.location.search : "";
    setShowDemoShortcuts(Boolean(demoMode) || hasDemoQueryFlag(search) || isDevAuthShortcutsEnabled(search));
  }, [demoMode]);

  const completeLogin = (session: AuthSession, message: string) => {
    setAuthSession(session);
    setCurrentSession(session);
    setSuccessMsg(message);
    setErrorMsg(null);
    setEndpointMissing404(false);
    if (onAuthenticated) {
      onAuthenticated(session, nextTarget);
      return;
    }
    router.push(nextTarget);
  };

  const handlePasswordLogin = async (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);
    setEndpointMissing404(false);

    if (!email.trim() || !password) {
      setErrorMsg("Vui lòng nhập đầy đủ email và mật khẩu.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await apiFetch("/api/v1/auth/login", {
        method: "POST",
        fetchFn,
        redirectOn401: false,
        json: {
          email: email.trim(),
          password,
          ...(tenantSlug.trim() ? { tenant_slug: tenantSlug.trim() } : {}),
        },
      });
      const payload = (await response.json()) as LoginPayloadLike;
      const session = buildSessionFromLoginResponse(payload, email.trim());
      if (!session) {
        setErrorMsg("Máy chủ không trả về access_token hợp lệ.");
        return;
      }
      completeLogin(session, `Đăng nhập thành công (${session.roles.join(", ")}). Đang chuyển hướng...`);
    } catch (error) {
      if (error instanceof ApiRequestError) {
        if (error.status === 404) {
          setEndpointMissing404(true);
          setShowJwtPanel(true);
          setErrorMsg(
            "Backend hiện chưa kích hoạt route POST /api/v1/auth/login (HTTP 404). Vui lòng dùng JWT Dev/Test bên dưới hoặc bật chế độ ?demo=1.",
          );
        } else {
          setErrorMsg(error.message);
        }
      } else {
        setErrorMsg("Không thể kết nối tới máy chủ xác thực (/api/v1/auth/login).");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleJwtPasteLogin = (event: React.FormEvent) => {
    event.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);

    const session = buildSessionFromRawToken(rawJwtInput);
    if (!session) {
      setErrorMsg("Token JWT không hợp lệ (cần dạng header.payload.signature base64url).");
      return;
    }
    completeLogin(session, `Đã nạp phiên JWT (${session.roles.join(", ")} · Tenant ${session.tenantId}).`);
  };

  const handleQuickFillPreset = (preset: DemoAccountPreset) => {
    setEmail(preset.email);
    setPassword(preset.password);
    setTenantSlug(preset.tenantSlug);
    setErrorMsg(null);
  };

  const handleDemoSessionSignIn = (preset: DemoAccountPreset) => {
    const headerB64 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9";
    const claims = {
      sub: preset.fallbackSession.userId,
      tenant_id: preset.fallbackSession.tenantId,
      membership_id: preset.fallbackSession.membershipId,
      roles: preset.fallbackSession.roles,
      entitlement_tier: preset.fallbackSession.entitlementTier,
      email: preset.email,
      name: preset.fallbackSession.displayName,
    };
    const jsonClaims = JSON.stringify(claims);
    const encodedClaims =
      typeof window !== "undefined" && typeof window.btoa === "function"
        ? window.btoa(unescape(encodeURIComponent(jsonClaims))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
        : "demo_payload";
    const demoToken = `${headerB64}.${encodedClaims}.demo_local_signature`;
    completeLogin(
      {
        accessToken: demoToken,
        ...preset.fallbackSession,
      },
      `Đã kích hoạt phiên demo ${preset.roleBadge}.`,
    );
  };

  const handleLogout = () => {
    clearAuthSession();
    setCurrentSession(null);
    setSuccessMsg("Đã đăng xuất khỏi phiên hiện tại.");
    setErrorMsg(null);
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-12">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-xs font-semibold text-blue-700 hover:underline"
          >
            ← Quay lại Trang chủ H2Edu
          </Link>
          <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
            Auth Bridge V11.3 · JWT + Tenant RLS
          </span>
        </div>

        <h1 className="mt-4 text-2xl font-bold tracking-tight text-slate-900">
          Đăng nhập Hệ thống H2Edu
        </h1>
        <p className="mt-1.5 text-sm text-slate-600">
          Xác thực phiên JWT để đồng bộ bài thi, lộ trình học, Studio và Quản trị qua{" "}
          <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">Authorization: Bearer</code>.
        </p>

        {currentSession && (
          <div
            data-testid="active-session-card"
            className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm"
          >
            <div>
              <p className="font-semibold text-emerald-950">
                Đang đăng nhập: {currentSession.displayName || currentSession.email || currentSession.userId}
              </p>
              <p className="text-xs text-emerald-800">
                Vai trò: {currentSession.roles.join(", ")} · Tenant: {currentSession.tenantId}
                {currentSession.membershipId ? ` · Membership: ${currentSession.membershipId}` : ""}
              </p>
            </div>
            <button
              type="button"
              data-testid="login-page-logout-btn"
              onClick={handleLogout}
              className="rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-xs font-semibold text-emerald-900 hover:bg-emerald-100"
            >
              Đăng xuất
            </button>
          </div>
        )}

        {errorMsg && (
          <div
            role="alert"
            data-testid="login-error-banner"
            className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            <p className="font-semibold">Không thể đăng nhập</p>
            <p className="mt-0.5">{errorMsg}</p>
            {endpointMissing404 && (
              <p className="mt-1 text-xs text-rose-800">
                Gợi ý dev: Tạo JWT HS256 bằng secret backend và dán vào mục &ldquo;Dán JWT Dev/Test&rdquo; bên dưới.
              </p>
            )}
          </div>
        )}

        {successMsg && (
          <div
            data-testid="login-success-banner"
            className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
          >
            {successMsg}
          </div>
        )}

        <form onSubmit={handlePasswordLogin} className="mt-6 space-y-4">
          <div>
            <label htmlFor="login-email" className="block text-sm font-semibold text-slate-800">
              Email tài khoản
            </label>
            <input
              id="login-email"
              data-testid="login-email-input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="hocsinh@h2edu.vn"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none"
            />
          </div>

          <div>
            <label htmlFor="login-password" className="block text-sm font-semibold text-slate-800">
              Mật khẩu
            </label>
            <input
              id="login-password"
              data-testid="login-password-input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none"
            />
          </div>

          <div>
            <label htmlFor="login-tenant" className="block text-sm font-semibold text-slate-800">
              Mã trường / Tenant (tùy chọn)
            </label>
            <input
              id="login-tenant"
              data-testid="login-tenant-input"
              type="text"
              value={tenantSlug}
              onChange={(e) => setTenantSlug(e.target.value)}
              placeholder="h2edu-demo"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-600 focus:outline-none"
            />
          </div>

          <button
            type="submit"
            data-testid="login-submit-btn"
            disabled={submitting}
            className="w-full rounded-lg bg-blue-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-800 disabled:opacity-60"
          >
            {submitting ? "Đang xác thực..." : "Đăng nhập qua /api/v1/auth/login"}
          </button>
        </form>

        <div className="mt-6 border-t border-slate-200 pt-5">
          <button
            type="button"
            data-testid="toggle-jwt-panel-btn"
            onClick={() => setShowJwtPanel((prev) => !prev)}
            className="flex w-full items-center justify-between text-left text-sm font-semibold text-slate-800 hover:text-blue-700"
          >
            <span>Dán JWT Dev/Test (từ backend HS256 / test harness)</span>
            <span className="text-xs text-slate-500">{showJwtPanel ? "Thu gọn ▲" : "Mở rộng ▼"}</span>
          </button>

          {showJwtPanel && (
            <form onSubmit={handleJwtPasteLogin} className="mt-3 space-y-3">
              <textarea
                data-testid="raw-jwt-input"
                rows={3}
                value={rawJwtInput}
                onChange={(e) => setRawJwtInput(e.target.value)}
                placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs text-slate-900 focus:border-blue-600 focus:outline-none"
              />
              <button
                type="submit"
                data-testid="raw-jwt-submit-btn"
                className="rounded-lg border border-slate-300 bg-slate-50 px-3.5 py-2 text-xs font-semibold text-slate-800 hover:bg-slate-100"
              >
                Lưu phiên từ Access Token JWT
              </button>
            </form>
          )}
        </div>

        {showDemoShortcuts && (
          <section
            data-testid="demo-accounts-panel"
            className="mt-6 rounded-xl border border-amber-200 bg-amber-50/70 p-4"
          >
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-bold text-amber-950">
                Tài khoản Demo nhanh (Môi trường Dev / ?demo=1)
              </h2>
              <span className="rounded bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-900">
                Quick Fill
              </span>
            </div>
            <div className="mt-3 grid gap-2.5">
              {DEMO_ACCOUNTS.map((preset) => (
                <div
                  key={preset.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-white p-3"
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-bold text-white">
                        {preset.roleBadge}
                      </span>
                      <span className="text-sm font-semibold text-slate-900">{preset.label}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-slate-600">
                      {preset.email} · {preset.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      data-testid={`demo-fill-${preset.id}`}
                      onClick={() => handleQuickFillPreset(preset)}
                      className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      Điền form
                    </button>
                    <button
                      type="button"
                      data-testid={`demo-instant-${preset.id}`}
                      onClick={() => handleDemoSessionSignIn(preset)}
                      className="rounded-md bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-amber-700"
                    >
                      Vào nhanh
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
