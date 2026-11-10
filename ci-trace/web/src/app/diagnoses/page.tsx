import { ArrowDown, ArrowUpRight } from "lucide-react";
import Link from "next/link";

import { pnpmReplay } from "@/core/diagnoses/replays";

import { DiagnosisForm } from "./diagnosis-form";
import { HomeHero } from "./home-hero";

export default function DiagnosesPage() {
  return (
    <main>
      <HomeHero>
        <div
          className="trace-grid pointer-events-none absolute inset-0 -z-20"
          aria-hidden="true"
        />
        <div className="trace-home-plane" aria-hidden="true" />
        <svg
          className="trace-home-signal pointer-events-none absolute top-[25%] left-1/2 -z-10 w-[120%] max-w-6xl -translate-x-1/2"
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
            className="trace-home-pulse"
            pathLength="1"
            d="M0 215h130l51-18h85l37-62h90l34 80h104l46-138h62l44 138h91l42-31h144"
            stroke="#9cdcff"
            strokeWidth="2"
          />
          <path
            d="M0 280h165l65-19h98l48 29h93l52-66h109l44 37h105l54-18h127"
            stroke="#3a526f"
          />
          <path
            className="trace-home-pulse trace-home-pulse-secondary"
            pathLength="1"
            d="M0 280h165l65-19h98l48 29h93l52-66h109l44 37h105l54-18h127"
            stroke="#b9a7ff"
            strokeWidth="2"
          />
        </svg>
        <div className="mx-auto w-full max-w-3xl">
          <p className="trace-home-intro mb-5 text-sm font-medium text-[#b3d9f2]">
            GitHub Actions 失败诊断
          </p>
          <h1 className="trace-display trace-home-title text-balance">
            <span className="trace-title-line"><span>CI 红了。</span></span>
            <span className="trace-title-line"><span>沿着证据查下去。</span></span>
          </h1>
          <p className="trace-home-caption mx-auto mt-6 max-w-lg text-base leading-relaxed text-pretty text-[#aabbd6] sm:text-lg">
            查看失败步骤、可取得的证据和下一步建议。无需登录或配置模型。
          </p>
          <div className="trace-home-form">
            <DiagnosisForm />
          </div>
        </div>
        <a
          href="#case"
          className="trace-jump mt-8 inline-flex min-h-11 items-center gap-2 rounded px-2 text-sm text-[#b3d9f2] hover:text-white"
        >
          看一次真实失败 <ArrowDown size={15} aria-hidden="true" />
        </a>
      </HomeHero>
      <section
        id="case"
        className="mx-auto max-w-5xl scroll-mt-8 px-5 pt-10 pb-20 sm:px-8"
      >
        <Link
          href={`/diagnoses/${pnpmReplay.id}`}
          className="trace-case-card trace-reveal group grid items-center gap-6 rounded-[2rem] border border-[#344763] p-7 sm:grid-cols-[1fr_auto] sm:p-12"
        >
          <div>
            <p className="font-mono text-sm text-[#91d6ff]">
              pnpm / pnpm · 第 1 次运行
            </p>
            <h2 className="mt-3 flex items-center gap-3 text-2xl font-semibold tracking-tight sm:text-4xl">
              WASI SDK 下载返回 HTTP 504{" "}
              <ArrowUpRight
                className="trace-case-arrow shrink-0"
                aria-hidden="true"
              />
            </h2>
            <p className="mt-3 text-sm text-[#aabbd6]">
              打开案例，查看四次失败请求和对应源码。
            </p>
          </div>
          <span className="trace-case-number" aria-hidden="true">
            504
          </span>
        </Link>
      </section>
    </main>
  );
}
