import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center px-6 py-16">
      <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
          H2Edu • Học thông minh - Thi bứt phá
        </div>
        <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900">
          Cổng học liệu &amp; Luyện thi THPTQG 2025
        </h1>
        <p className="mt-3 text-slate-600">
          Chọn không gian học tập chuẩn hóa: Học chuyên đề 4 cấp kèm 4 tab luyện tập và Gia sư AI
          (Workspace 1), hoặc Phòng thi trực tuyến chống mất bài (Workspace 2).
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/hoc-lieu/cong-thuc-bayes"
            className="inline-flex items-center rounded-lg bg-indigo-700 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-800"
          >
            Mở Học liệu Bayes (Workspace 1)
          </Link>
          <Link
            href="/lo-trinh"
            className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-slate-50"
          >
            Lộ trình học
          </Link>
          <Link
            href="/so-tay-cau-sai"
            className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-800 transition hover:bg-slate-50"
          >
            Sổ tay câu sai
          </Link>
          <Link
            href="/phong-thi/demo-attempt?demo=1"
            className="inline-flex items-center rounded-lg bg-blue-700 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-800"
          >
            Mở Phòng thi mẫu (Workspace 2)
          </Link>
        </div>
      </div>
    </main>
  );
}
