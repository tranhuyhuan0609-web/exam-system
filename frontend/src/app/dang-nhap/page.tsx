import React, { Suspense } from "react";
import { LoginShell } from "@/components/auth/LoginShell";

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-screen max-w-2xl items-center justify-center px-4 py-12 text-sm text-slate-600">
          Đang tải trang đăng nhập H2Edu...
        </main>
      }
    >
      <LoginShell />
    </Suspense>
  );
}
