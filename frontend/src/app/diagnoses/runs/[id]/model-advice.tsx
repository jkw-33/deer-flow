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
  const [findings, setFindings] = useState<Finding[]>([]);
  const [coverage, setCoverage] = useState<Coverage>();
  const [retryAfter, setRetryAfter] = useState<number>();
  const [text, setText] = useState("");
  const [investigations, setInvestigations] = useState<Investigation[]>([]);
  const [toolCalls, setToolCalls] = useState<Investigation[]>([]);
  const [notice, setNotice] = useState("");
  const [traceAvailable, setTraceAvailable] = useState(false);
  const [pending, setPending] = useState(false);

  const analyze = useCallback(
    async (signal?: AbortSignal, retry?: number) => {
      setTraceAvailable(false);
      setPending(true);
      setText("");
      setFindings([]);
      setCoverage(undefined);
      setRetryAfter(undefined);
      setInvestigations([]);
      setToolCalls([]);
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
              const event = JSON.parse(line) as
                | { kind: "started" | "error"; analysis_attempt?: number }
                | { kind: "investigation"; item: Investigation }
                | {
                    kind: "result";
                    text: string;
                    investigations: Investigation[];
                    findings?: Finding[];
                    coverage?: Coverage;
                  };
              setTraceAvailable(true);
              if (event.kind === "error") {
                if ((event.analysis_attempt ?? 1) < 10)
                  setRetryAfter(event.analysis_attempt ?? 1);
                throw new Error("Investigation failed");
              }
              if (event.kind === "investigation") {
                if (event.item.tool)
                  setToolCalls((calls) => [...calls, event.item]);
                setInvestigations((items) => [
                  ...items.filter((item) => item.job_id !== event.item.job_id),
                  event.item,
                ]);
              }
              if (event.kind === "result") {
                setText(event.text);
                setFindings(event.findings ?? []);
                setCoverage(event.coverage);
                setInvestigations(event.investigations);
                return;
              }
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
      className="mt-10 border-t border-[#25334b] pt-6"
      aria-labelledby="model-advice"
    >
      <h2 id="model-advice" className="text-lg font-semibold">
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
          {pending ? "正在分析…" : retryAfter ? "重试分析" : "继续深度分析"}
        </button>
        <Link
          href="/diagnoses/model"
          className="text-sm text-[#91d6ff] underline underline-offset-4"
        >
          模型设置
        </Link>
      </div>
      {traceAvailable && (
        <a
          href={`/api/ci-diagnoses/${id}/artifacts/model-trace.json`}
          className="mt-3 min-h-11 text-sm text-[#91d6ff] underline"
        >
          下载本次模型分析轨迹
        </a>
      )}
      <p role="status" className="mt-3 text-sm text-[#aabbd6]">
        {notice}
      </p>
      {toolCalls.length > 0 && (
        <ul
          aria-label="工具调用"
          className="mt-3 space-y-1 text-sm text-[#aabbd6]"
        >
          {toolCalls.map((call) => (
            <li key={`${call.evidence_id}-${call.tool}`}>
              <code>{call.tool}</code> · {call.evidence_id}
            </li>
          ))}
        </ul>
      )}
      {investigations.map((item) => (
        <details
          key={item.job_id}
          className="mt-3 rounded-md border border-[#25334b] p-4 text-sm"
        >
          <summary className="cursor-pointer">
            {item.evidence_id} ·{" "}
            {item.tool
              ? item.tool === "read_log_excerpt"
                ? "已读取日志摘录"
                : "已读取 Job 证据"
              : item.running
                ? "调查中"
                : item.error
                  ? "调查受阻"
                  : "调查完成"}
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
      )}
      {text && findings.length === 0 && (
        <p className="mt-3 rounded-md border border-[#25334b] p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">
          {text}
        </p>
      )}
    </section>
  );
}
