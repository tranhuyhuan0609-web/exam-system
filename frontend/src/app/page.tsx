import Link from "next/link";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center px-6 py-16">
      <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
          H2Edu • Học thông minh - Thi bứt phá
        </div>
        <h1 className="mt-4 text-3xl font-bold tracking-tight text-slate-900">
          Cổng luyện thi THPTQG 2025
        </h1>
        <p className="mt-3 text-slate-600">
          Chọn phòng thi trực tuyến để bắt đầu làm bài theo cấu trúc 3 phần: Trắc nghiệm nhiều lựa chọn,
          Đúng/Sai 4 ý và Trả lời ngắn 4 ô.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
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
