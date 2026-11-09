import { ArrowUpRight, Check, ChevronDown, CircleAlert } from "lucide-react";
import { notFound } from "next/navigation";

import { pnpmReplay } from "@/core/diagnoses/replays";

export default async function DiagnosisPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ step?: string }>;
}) {
  const [{ id }, { step }] = await Promise.all([params, searchParams]);
  if (id !== pnpmReplay.id) notFound();

  const replay = pnpmReplay;
  const initialStep = replay.events.some(({ seq }) => String(seq) === step)
    ? step
    : "5";

  return (
    <main className="mx-auto max-w-4xl px-5 pt-16 pb-24 sm:px-8 sm:pt-24">
      <div className="trace-enter">
        <p className="font-mono text-xs text-[#91d6ff]">
          {replay.repository} / {replay.workflow}
        </p>
        <h1 className="mt-4 text-4xl font-semibold tracking-tight text-balance sm:text-6xl">
          WASI SDK 下载失败
        </h1>
        <p className="mt-5 max-w-xl leading-relaxed text-pretty text-[#aabbd6]">
          {replay.report.summary}
        </p>
        <p className="mt-5 text-xs text-[#aabbd6]">
          历史案例回放 · 第 {replay.attempt} 次运行
        </p>
      </div>
      <section aria-labelledby="trace-heading" className="mt-14">
        <h2 id="trace-heading" className="mb-5 text-xl font-semibold">
          运行轨迹
        </h2>
        <ol className="border-y border-[#25334b]">
          {replay.events.map((event) => {
            const Icon = event.status === "finding" ? CircleAlert : Check;
            const evidence = replay.evidence.filter(({ id }) =>
              event.evidenceIds.includes(id),
            );
            return (
              <li
                key={event.seq}
                className="border-b border-[#25334b] last:border-b-0"
              >
                <details open={String(event.seq) === initialStep}>
                  <summary className="group flex min-h-16 cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
                    <span className="w-5 shrink-0 font-mono text-xs text-[#aabbd6]">
                      {String(event.seq).padStart(2, "0")}
                    </span>
                    <Icon
                      size={16}
                      className={
                        event.status === "finding"
                          ? "shrink-0 text-[#ffb4a5]"
                          : "shrink-0 text-[#91d6ff]"
                      }
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">
                        {event.title}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-[#aabbd6]">
                        {event.summary}
                      </span>
                    </span>
                    <ChevronDown
                      size={16}
                      className="shrink-0 text-[#aabbd6] transition-transform group-open:rotate-180 motion-reduce:transition-none"
                      aria-hidden="true"
                    />
                  </summary>
                  <div className="trace-enter space-y-5 pb-5 pl-8 sm:pl-12">
                    {evidence.map((item) => (
                      <div
                        key={item.id}
                        className="min-w-0 rounded-lg border border-[#25334b] bg-[#101b2c] p-3 sm:p-4"
                      >
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1.5 text-sm font-medium text-[#91d6ff] hover:underline"
                        >
                          {item.title}{" "}
                          <ArrowUpRight size={14} aria-hidden="true" />
                        </a>
                        <p className="mt-1 font-mono text-xs break-all text-[#aabbd6]">
                          {item.location}
                        </p>
                        {item.excerpts && (
                          <pre className="mt-3 max-h-64 overflow-auto rounded bg-[#0a111c] p-3 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
                            <code>
                              {item.excerpts
                                .map(({ line, text }) => `${line}  ${text}`)
                                .join("\n")}
                            </code>
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              </li>
            );
          })}
        </ol>
      </section>
      <section
        aria-labelledby="report-heading"
        className="mt-12 grid gap-5 sm:grid-cols-2"
      >
        <div>
          <h2 id="report-heading" className="text-lg font-semibold">
            证据边界
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-[#aabbd6]">
            {replay.report.limits}
          </p>
        </div>
        <div>
          <h2 className="text-lg font-semibold">修复验证</h2>
          <p className="mt-2 text-sm leading-relaxed text-[#aabbd6]">
            {replay.report.validation}
          </p>
        </div>
      </section>
    </main>
  );
}
