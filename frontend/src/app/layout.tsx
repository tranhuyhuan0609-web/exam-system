import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "H2Edu — Học thông minh - Thi bứt phá",
  description: "Nền tảng luyện thi THPTQG 2025 chuẩn hóa — Không gian phòng thi trực tuyến H2Edu.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">{children}</body>
    </html>
  );
}
