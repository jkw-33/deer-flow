"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

type Investigation = {
  evidence_id: string;
  job_id: number;
  text?: string;
  error?: boolean;
  running?: boolean;
  tool?: "read_job_evidence" | "read_log_excerpt";
};

type Finding = {
  kind: "observation" | "inference" | "unknown" | "next_step";
  text: string;
  evidence_ids: string[];
};

type Coverage = {
  analyzed_job_ids: number[];
  failed_job_ids: number[];
  unexamined_job_ids: number[];
};

type AnalysisEvent = { analysis_attempt?: number; recorded_at?: number } & (
  | { kind: "started"; provider?: string; model?: string }
  | { kind: "investigation"; item: Investigation }
  | {
      kind: "result";
      text: string;
      investigations: Investigation[];
      findings?: Finding[];
      coverage?: Coverage;
    }
  | { kind: "error"; reason?: string }
);

function activityLabel(event: AnalysisEvent) {
  switch (event.kind) {
    case "started":
      return `第 ${event.analysis_attempt ?? 1} 次分析 · ${[event.provider, event.model].filter(Boolean).join(" / ") || "模型已启动"}`;
    case "investigation":
      if (event.item.tool === "read_job_evidence") return "读取 Job 证据";
      if (event.item.tool === "read_log_excerpt") return "读取日志摘录";
      if (event.item.error) return "Job 调查受阻";
      return event.item.running ? "开始调查 Job" : "完成 Job 调查";
    case "result":
      return "生成分析结论";
    case "error":
      return event.reason === "timeout"
        ? "模型分析超时"
        : event.reason === "interrupted"
          ? "模型分析中断"
          : "模型分析受阻";
  }
}

const findingLabels: Record<Finding["kind"], string> = {
  observation: "观察",
  inference: "推断",
  unknown: "未知",
  next_step: "下一步",
};

export function ModelAdvice({
  id,
  autoStart,
}: {
  id: string;
  autoStart?: boolean;
}) {
  const [events, setEvents] = useState<AnalysisEvent[]>([]);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const latest = events.at(-1);
  const result = latest?.kind === "result" ? latest : undefined;
  const findings = result?.findings ?? [];
  const coverage = result?.coverage;
  const failedAttempt =
    latest?.kind === "error" ? (latest.analysis_attempt ?? 1) : undefined;
  const retryAfter =
    failedAttempt && failedAttempt < 10 ? failedAttempt : undefined;

  const analyze = useCallback(
    async (signal?: AbortSignal, retry?: number) => {
      setEvents([]);
      setPending(true);
      setNotice("");
      try {
        const response = await fetch("/api/ci-diagnoses/model/analyze", {
          method: "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            Accept: "application/x-ndjson",
          },
          body: JSON.stringify({ task_id: id, retry_after: retry }),
        });
        if (response.status === 409) {
          setNotice("请先配置模型，然后回到此处运行分析。");
          return;
        }
        if (!response.ok || !response.body)
          throw new Error("Model unavailable");
        const reader = response.body
          .pipeThrough(new TextDecoderStream())
          .getReader();
        let buffer = "";
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) throw new Error("Incomplete investigation");
            buffer += value;
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";
            for (const line of lines) {
              if (!line) continue;
              const event = JSON.parse(line) as AnalysisEvent;
              if (signal?.aborted) return;
              setEvents((current) => [...current, event]);
              if (event.kind === "error")
                throw new Error("Investigation failed");
              if (event.kind === "result") return;
            }
          }
        } finally {
          await reader.cancel();
        }
      } catch {
        if (signal?.aborted) return;
        setNotice("模型分析暂不可用，仍可查看上方的基础建议。");
      } finally {
        if (!signal?.aborted) {
          setPending(false);
        }
      }
    },
    [id],
  );

  useEffect(() => {
    if (!autoStart) return;
    const controller = new AbortController();
    void analyze(controller.signal);
    return () => controller.abort();
  }, [analyze, autoStart]);

  return (
    <section
      className="trace-result-panel trace-result-agent mt-10"
      aria-labelledby="model-advice"
    >
      <h2 id="model-advice" className="trace-result-heading">
        CI 诊断 Agent
      </h2>
      <p className="mt-2 text-sm text-[#aabbd6]">
        模型会根据这次诊断的证据补充分析。首次使用时配置模型和 API Key。
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        <button
          type="button"
          disabled={pending}
          onClick={() => void analyze(undefined, retryAfter)}
          className="min-h-10 rounded-md bg-[#91d6ff] px-3 text-sm font-semibold text-[#0a111c] disabled:opacity-60"
        >
          {pending
            ? "正在分析…"
            : retryAfter
              ? "重试分析"
              : result
                ? "重载分析记录"
                : "继续深度分析"}
        </button>
        <Link
          href="/diagnoses/model"
          className="text-sm text-[#91d6ff] underline underline-offset-4"
        >
          模型设置
        </Link>
      </div>
      <p role="status" className="mt-3 text-sm text-[#aabbd6]">
        {notice}
      </p>
      {events.length > 0 && (
        <div className="mt-5">
          <h3 id="agent-timeline" className="text-sm font-semibold">
            Agent 运行轨迹
          </h3>
          <ol
            aria-labelledby="agent-timeline"
            className="mt-2 divide-y divide-[#25334b] rounded-md border border-[#25334b]"
          >
            {events.map((event, index) => (
              <li
                key={index}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2 text-sm"
              >
                <span className="font-mono text-[#91d6ff]" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="min-w-0 break-all">
                  {activityLabel(event)}
                </span>
                {event.kind === "investigation" && (
                  <>
                    {event.item.tool && (
                      <code className="break-all text-[#aabbd6]">
                        {event.item.tool}
                      </code>
                    )}
                    <a
                      href={`#${event.item.evidence_id}`}
                      className="break-all text-[#91d6ff] underline"
                    >
                      {event.item.evidence_id}
                    </a>
                  </>
                )}
                {event.recorded_at !== undefined && (
                  <time
                    className="ml-auto text-xs text-[#aabbd6]"
                    dateTime={new Date(event.recorded_at * 1000).toISOString()}
                  >
                    {new Date(event.recorded_at * 1000).toLocaleTimeString()}
                  </time>
                )}
              </li>
            ))}
          </ol>
          <a
            href={`/api/ci-diagnoses/${id}/artifacts/model-trace.json`}
            className="inline-flex min-h-11 items-center text-sm text-[#91d6ff] underline"
          >
            导出模型分析轨迹
          </a>
        </div>
      )}
      {result?.investigations.map((item) => (
        <details
          key={item.job_id}
          className="mt-3 rounded-md border border-[#25334b] p-4 text-sm"
        >
          <summary className="cursor-pointer">
            {item.evidence_id} · {item.error ? "调查受阻" : "分 Job 分析"}
          </summary>
          {item.text && (
            <p className="mt-2 break-words whitespace-pre-wrap">{item.text}</p>
          )}
        </details>
      ))}
      {coverage && (
        <div className="mt-3 grid gap-1 text-sm text-[#aabbd6]">
          <div>已分析 Job · {coverage.analyzed_job_ids.join("、") || "无"}</div>
          <div>调查失败 Job · {coverage.failed_job_ids.join("、") || "无"}</div>
          <div>
            未分析 Job · {coverage.unexamined_job_ids.join("、") || "无"}
          </div>
        </div>
      )}
      {findings.length > 0 && (
        <div className="mt-5">
          <h3 className="text-sm font-semibold">模型分析摘要</h3>
          <ul className="mt-3 space-y-2">
            {findings.map((finding, index) => (
              <li
                key={index}
                className="rounded border border-[#25334b] p-3 text-sm"
              >
                <p>
                  {findingLabels[finding.kind]} · {finding.text}
                </p>
                {finding.evidence_ids.map((id) => (
                  <a
                    key={id}
                    href={`#${id}`}
                    className="mr-3 inline-block text-[#91d6ff] underline"
                  >
                    {id}
                  </a>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result?.text && findings.length === 0 && (
        <p className="mt-3 rounded-md border border-[#25334b] p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">
          {result.text}
        </p>
      )}
    </section>
  );
}
