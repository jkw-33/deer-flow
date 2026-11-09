import type { Metadata } from "next";
import Link from "next/link";

import "./trace.css";

export const metadata: Metadata = {
  title: "CI Trace",
  description: "GitHub Actions 故障诊断",
};

export default function DiagnosisLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      lang="zh-CN"
      className="trace-site min-h-dvh bg-[#0a111c] text-[#ecf2ff]"
    >
      <header className="relative z-10 mx-auto flex h-16 w-full max-w-6xl items-center justify-between border-b border-[#25334b] px-5 sm:px-8">
        <Link
          href="/diagnoses"
          className="flex items-center gap-2.5 font-semibold tracking-tight"
        >
          <svg
            viewBox="0 0 28 24"
            width="28"
            height="24"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M2 17h6l4-10 5 13 4-9h5"
              stroke="#91d6ff"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          CI Trace
        </Link>
        <nav aria-label="CI Trace" className="flex items-center gap-4">
          <Link
            href="/diagnoses/pnpm-wasi-504"
            className="text-sm text-[#aabbd6] hover:text-white"
          >
            pnpm 案例
          </Link>
        </nav>
      </header>
      {children}
    </div>
  );
}
