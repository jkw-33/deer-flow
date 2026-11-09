"use client";

import { useEffect, useState } from "react";

import { ModelAdvice } from "./model-advice";

type RunEvent =
  | { seq: number; kind: "queued" }
  | { seq: number; kind: "attempt"; attempt: number; conclusion: string | null }
  | {
      seq: number;
      kind: "job";
      job_id: number;
      name: string;
      steps: string[];
      log_state: string;
      checkout_shas: string[];
      annotation_count: number;
      advice?: string[];
      http_errors: string[];
    }
  | { seq: number; kind: "completed"; failed_jobs: number }
  | { seq: number; kind: "failed"; reason: string };

type Task = {
  private?: boolean;
  url: string;
  repository: string;
  status: "queued" | "running" | "completed" | "failed";
  events: RunEvent[];
};

const failures: Record<string, string> = {
  github_app_configuration: "GitHub App 授权配置不可用，请检查服务端配置。",
  rate_limited: "GitHub 请求已限流。请稍后重新诊断。",
  repository_unavailable: "无法访问这个运行。当前支持可读取的公开仓库。",
  network_unavailable: "连接 GitHub 超时或中断。请重试。",
  unexpected_response: "GitHub 返回了无法处理的数据。",
};

const logStates: Record<string, string> = {
  available: "已读取日志",
  unavailable: "完整日志不可用",
  rate_limited: "日志读取已限流",
  unsupported_redirect: "日志下载地址无法验证",
  too_large: "日志超过读取上限",
};

function eventTitle(event: RunEvent) {
  switch (event.kind) {
    case "queued":
      return "收到诊断请求";
    case "attempt":
      return `固定为第 ${event.attempt} 次运行`;
    case "job":
      return event.name;
    case "completed":
      return "证据采集完成";
    case "failed":
      return "采集受阻";
  }
}

export function LiveDiagnosis({ id }: { id: string }) {
  const [task, setTask] = useState<Task | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const endpoint = `/api/ci-diagnoses/${id}`;
    let stream: EventSource | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const reconnect = () => {
      stream?.close();
      if (controller.signal.aborted) return;
      setNotice("连接中断，正在恢复…");
      if (retry) clearTimeout(retry);
      retry = setTimeout(() => void connect(), 1800);
    };
    async function connect() {
      try {
        const response = await fetch(endpoint, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (response.status === 404) {
          setTask(null);
          setNotice("这个诊断已过期，或属于另一个浏览器会话。");
          return;
        }
        if (!response.ok) throw new Error("Snapshot unavailable");
        const snapshot = (await response.json()) as Task;
        if (controller.signal.aborted) return;
        setTask(snapshot);
        setNotice("");
        if (snapshot.status === "failed" || snapshot.status === "completed")
          return;
        stream = new EventSource(
          `${endpoint}/events?after=${snapshot.events.at(-1)?.seq ?? 0}`,
        );
        stream.addEventListener(
          "diagnosis",
          (message: MessageEvent<string>) => {
            const entry = JSON.parse(message.data) as RunEvent;
            const status =
              entry.kind === "completed" || entry.kind === "failed"
                ? entry.kind
                : "running";
            setTask((previous) => {
              if (!previous || entry.seq <= (previous.events.at(-1)?.seq ?? 0))
                return previous;
              return {
                ...previous,
                status,
                events: [...previous.events, entry],
              };
            });
            if (status !== "running") stream?.close();
          },
        );
        stream.addEventListener("recover", reconnect);
        stream.addEventListener("access-lost", () => {
          stream?.close();
          setTask(null);
          setNotice("当前会话无法继续访问这个诊断。");
        });
        stream.onerror = reconnect;
      } catch {
        reconnect();
      }
    }
    void connect();
    return () => {
      controller.abort();
      stream?.close();
      if (retry) clearTimeout(retry);
    };
  }, [id]);

  return (
    <main className="mx-auto max-w-4xl px-5 pt-16 pb-24 sm:px-8 sm:pt-24">
      <p className="font-mono text-xs text-[#91d6ff]">实时诊断</p>
      <h1 className="mt-4 text-4xl font-semibold tracking-tight break-words sm:text-6xl">
        {task?.repository ?? "读取诊断记录"}
      </h1>
      <p role="status" className="mt-4 text-sm text-[#aabbd6]">
        {notice ||
          (!task
            ? "正在读取…"
            : task.status === "completed"
              ? "证据采集完成"
              : task.status === "failed"
                ? "采集受阻"
                : "正在采集证据…")}
      </p>
      {task && (
        <section aria-labelledby="live-trace" className="mt-12">
          <h2 id="live-trace" className="mb-5 text-xl font-semibold">
            运行轨迹
          </h2>
          <ol className="divide-y divide-[#25334b] border-y border-[#25334b]">
            {task.events.map((event) => (
              <li key={event.seq} className="trace-enter min-w-0 py-4">
                <h3 className="flex gap-3 text-sm font-medium">
                  <span className="font-mono text-[#91d6ff]">
                    {String(event.seq).padStart(2, "0")}
                  </span>
                  {eventTitle(event)}
                </h3>
                {event.kind === "attempt" && (
                  <p className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    运行结论 · {event.conclusion ?? "尚未结束"}
                  </p>
                )}
                {event.kind === "failed" && (
                  <p className="mt-2 pl-8 text-sm text-[#ffb4a5]">
                    {failures[event.reason] ?? "采集暂不可用。"}
                  </p>
                )}
                {event.kind === "completed" && (
                  <p className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    已定位 {event.failed_jobs} 个失败 Job。
                  </p>
                )}
                {event.kind === "job" && (
                  <details className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    <summary className="cursor-pointer">
                      {logStates[event.log_state] ?? "日志不可用"}
                    </summary>
                    <div className="mt-3 space-y-2">
                      {event.steps.map((step, index) => (
                        <p key={index}>失败步骤 · {step}</p>
                      ))}
                      {event.http_errors.length > 0 && (
                        <p>
                          日志中的 HTTP 错误 · {event.http_errors.join("、")}
                        </p>
                      )}
                      {event.checkout_shas.map((sha) => (
                        <p key={sha} className="font-mono break-all">
                          Checkout 候选 · {sha}
                        </p>
                      ))}
                      {event.annotation_count > 0 && (
                        <p>GitHub 检查注释 · {event.annotation_count} 条</p>
                      )}
                      <a
                        className="inline-block text-[#91d6ff] underline underline-offset-4"
                        href={`${task.url}/job/${event.job_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        查看 GitHub Job
                      </a>
                    </div>
                  </details>
                )}
                {event.kind === "job" &&
                  event.advice &&
                  event.advice.length > 0 && (
                    <div className="mt-3 pl-8 text-sm">
                      <p className="font-medium">建议的下一步</p>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-[#aabbd6]">
                        {event.advice.map((advice) => (
                          <li key={advice}>{advice}</li>
                        ))}
                      </ul>
                    </div>
                  )}
              </li>
            ))}
          </ol>
        </section>
      )}
      {!task?.private &&
        task?.status === "completed" &&
        task.events.some((event) => event.kind === "job") && (
          <ModelAdvice id={id} />
        )}
    </main>
  );
}
