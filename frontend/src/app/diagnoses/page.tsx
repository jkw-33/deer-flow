import { ArrowDown, ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { pnpmReplay } from "@/core/diagnoses/replays";

import { DiagnosisForm } from "./diagnosis-form";

export default function DiagnosesPage() {
  return (
    <main>
      <section className="trace-hero relative isolate flex min-h-[calc(100svh-4rem)] flex-col items-center justify-center overflow-hidden px-5 py-20 text-center">
        <div
          className="trace-grid pointer-events-none absolute inset-0 -z-20"
          aria-hidden="true"
        />
        <svg
          className="pointer-events-none absolute top-[17%] left-1/2 -z-10 w-[120%] max-w-6xl -translate-x-1/2 opacity-50"
          viewBox="0 0 960 360"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M0 215h130l51-18h85l37-62h90l34 80h104l46-138h62l44 138h91l42-31h144"
            stroke="#253d60"
            strokeWidth="1"
          />
          <path
            className="trace-signal"
            pathLength="1"
            d="M0 215h130l51-18h85l37-62h90l34 80h104l46-138h62l44 138h91l42-31h144"
            stroke="#91d6ff"
            strokeWidth="2"
          />
        </svg>
        <div className="trace-enter mx-auto w-full max-w-3xl">
          <p className="mb-5 font-mono text-xs tracking-[0.18em] text-[#91d6ff]">
            GitHub Actions 失败诊断
          </p>
          <h1 className="text-5xl leading-[1.12] font-semibold tracking-tight text-balance sm:text-7xl">
            CI 红了。
            <br />
            <span className="text-[#91d6ff]">沿着证据查下去。</span>
          </h1>
          <p className="mx-auto mt-6 max-w-lg text-base leading-relaxed text-pretty text-[#aabbd6] sm:text-lg">
            从失败步骤追到原始日志，再回到那次运行真正执行的源码。
          </p>
          <DiagnosisForm />
        </div>
        <a
          href="#case"
          className="mt-10 inline-flex items-center gap-2 rounded px-2 py-1 text-sm text-[#aabbd6] hover:text-white"
        >
          看一次真实失败 <ArrowDown size={15} aria-hidden="true" />
        </a>
      </section>
      <section
        id="case"
        className="mx-auto max-w-5xl scroll-mt-8 px-5 pt-10 pb-20 sm:px-8"
      >
        <Link
          href={`/diagnoses/${pnpmReplay.id}`}
          className="group block rounded-2xl border border-[#344763] bg-[#101b2c] p-6 hover:border-[#91d6ff] sm:p-9"
        >
          <p className="font-mono text-sm text-[#91d6ff]">
            pnpm / pnpm · 第 1 次运行
          </p>
          <h2 className="mt-3 flex items-center justify-between gap-3 text-2xl font-semibold sm:text-3xl">
            WASI SDK 下载返回 HTTP 504{" "}
            <ArrowUpRight className="shrink-0" aria-hidden="true" />
          </h2>
          <p className="mt-3 text-sm text-[#aabbd6]">
            打开案例，查看四次失败请求和对应源码。
          </p>
        </Link>
      </section>
    </main>
  );
}
