"use client";

import { useEffect, useState } from "react";

import { ModelAdvice } from "./model-advice";

type RunEvent = { recorded_at?: number } & RunEventData;

type RunEventData =
  | { seq: number; kind: "queued" }
  | { seq: number; kind: "collection_started"; access: string }
  | { seq: number; kind: "verification_skipped"; reason: string }
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
      annotations?: {
        id: string;
        path: string;
        line: number;
        message: string;
      }[];
      log_excerpt?: string[];
      advice?: string[];
      http_errors: string[];
    }
  | {
      seq: number;
      kind: "verification_started";
      mode?: "node_lookup" | "bash_syntax";
      path: string;
      sha: string;
    }
  | {
      seq: number;
      kind: "verification";
      path: string;
      sha: string;
      mode?: "node_lookup" | "bash_syntax";
      status: "verified" | "unconfirmed" | "unavailable";
      source_sha256?: string;
      baseline?: number;
      error_line?: number;
      rerun?: number;
    }
  | { seq: number; kind: "verification_dispatched"; run_id: number }
  | { seq: number; kind: "completed"; failed_jobs: number }
  | { seq: number; kind: "failed"; reason: string };

type Task = {
  private?: boolean;
  deep?: boolean;
  analysis_started?: boolean;
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

const collectionAccess: Record<string, string> = {
  private_authorized: "使用已授权的 GitHub 账号访问私有仓库",
  public_authenticated: "使用服务端凭据读取公开仓库",
  public_anonymous: "匿名读取公开仓库",
};

const skipReasons: Record<string, string> = {
  private_run: "当前验证仅覆盖公开仓库。",
  disabled: "当前部署未为该仓库启用自动验证。",
  no_supported_candidate: "现有证据中未找到可自动验证的脚本。",
  no_failed_jobs: "此轮运行没有失败 Job。",
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
    case "collection_started":
      return "开始采集 GitHub 证据";
    case "verification_skipped":
      return "未执行自动验证";
    case "attempt":
      return `固定为第 ${event.attempt} 次运行`;
    case "job":
      return event.name;
    case "verification_started":
      return event.mode === "node_lookup"
        ? "开始 Node 模块查找验证"
        : "开始 Bash 语法验证";
    case "verification_dispatched":
      return `GitHub 验证任务 #${event.run_id} 已提交`;
    case "verification":
      if (event.status === "verified")
        return event.mode === "node_lookup"
          ? "已验证 · LF 副本通过 Node 模块查找"
          : "已验证 · LF 副本通过 Bash 语法检查";
      if (event.status === "unavailable") return "验证受限";
      return "换行原因未确认";
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
                : ["verification_started", "verification_dispatched"].includes(
                      task.events.at(-1)?.kind ?? "",
                    )
                  ? "正在验证 CRLF…"
                  : "正在采集证据…")}
      </p>
      {task && (
        <section aria-labelledby="live-trace" className="mt-12">
          <h2 id="live-trace" className="mb-5 text-xl font-semibold">
            运行轨迹
          </h2>
          <ol className="divide-y divide-[#25334b] border-y border-[#25334b]">
            {task.events.map((event) => (
              <li
                key={event.seq}
                id={event.kind === "job" ? `job-${event.job_id}` : undefined}
                className="trace-enter min-w-0 py-4"
              >
                <h3 className="flex gap-3 text-sm font-medium">
                  <span className="font-mono text-[#91d6ff]">
                    {String(event.seq).padStart(2, "0")}
                  </span>
                  {eventTitle(event)}
                  {event.recorded_at !== undefined && (
                    <time
                      className="ml-auto text-xs font-normal text-[#aabbd6]"
                      dateTime={new Date(
                        event.recorded_at * 1000,
                      ).toISOString()}
                    >
                      {new Date(event.recorded_at * 1000).toLocaleTimeString()}
                    </time>
                  )}
                </h3>
                {event.kind === "collection_started" && (
                  <p className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    {collectionAccess[event.access]}
                  </p>
                )}
                {event.kind === "verification_skipped" && (
                  <p className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    {skipReasons[event.reason]}
                  </p>
                )}
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
                {event.kind === "verification_started" && (
                  <p className="mt-2 pl-8 text-sm break-all text-[#aabbd6]">
                    {event.path} · {event.sha}
                  </p>
                )}
                {event.kind === "verification" && (
                  <details className="mt-2 pl-8 text-sm text-[#aabbd6]">
                    <summary className="cursor-pointer">查看验证和下载</summary>
                    <div className="mt-3 space-y-2">
                      <p className="font-mono break-all">
                        {event.sha}:{event.path}
                      </p>
                      {event.source_sha256 && (
                        <p className="font-mono break-all">
                          原始字节 SHA-256 · {event.source_sha256}
                        </p>
                      )}
                      <p>环境 · 隔离 Linux 容器</p>
                      <p>
                        检查 ·{" "}
                        <code>
                          {event.mode === "node_lookup"
                            ? "bash --noprofile --norc -e -s"
                            : "bash --noprofile --norc -n -s"}
                        </code>
                      </p>
                      {event.mode === "node_lookup" && (
                        <p>
                          原始脚本启动
                          Node，目标模块由空文件夹具提供。验证范围为模块路径解析。
                        </p>
                      )}
                      <p>原始字节退出码 · {event.baseline ?? "未记录"}</p>
                      {event.error_line != null && (
                        <p>语法错误行 · {event.error_line}</p>
                      )}
                      <p>仅替换 CRLF 后退出码 · {event.rerun ?? "未运行"}</p>
                      <ul className="flex flex-wrap gap-x-4 text-[#91d6ff]">
                        <li>
                          <a
                            className="inline-flex min-h-11 items-center underline"
                            href={`/api/ci-diagnoses/${id}/artifacts/verification.log`}
                          >
                            验证日志
                          </a>
                        </li>
                        {event.status === "verified" && (
                          <li>
                            <a
                              className="inline-flex min-h-11 items-center underline"
                              href={`/api/ci-diagnoses/${id}/artifacts/repair.diff`}
                            >
                              最小补丁
                            </a>
                          </li>
                        )}
                      </ul>
                    </div>
                  </details>
                )}
                {event.kind === "job" && (
                  <details
                    open={Boolean(event.annotations?.length)}
                    className="mt-2 pl-8 text-sm text-[#aabbd6]"
                  >
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
                      {event.annotations?.map((annotation) => (
                        <div
                          key={annotation.id}
                          id={annotation.id}
                          className="rounded border border-[#25334b] p-2 break-words"
                        >
                          <p className="font-mono">
                            {annotation.path}:{annotation.line}
                          </p>
                          <p>{annotation.message}</p>
                        </div>
                      ))}
                      {event.log_excerpt?.map((marker) => (
                        <p key={marker} className="font-mono">
                          {marker}
                        </p>
                      ))}
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
      {task && (
        <a
          href={`/api/ci-diagnoses/${id}/artifacts/trace.json`}
          className="mt-4 mr-4 inline-flex min-h-11 items-center text-sm text-[#91d6ff] underline"
        >
          下载运行轨迹（JSON）
        </a>
      )}
      {task?.status === "completed" && (
        <a
          href={`/api/ci-diagnoses/${id}/artifacts/report.txt`}
          className="mt-4 inline-flex min-h-11 items-center text-sm text-[#91d6ff] underline"
        >
          下载诊断报告
        </a>
      )}
      {!task?.private &&
        task?.status === "completed" &&
        task.events.some((event) => event.kind === "job") && (
          <ModelAdvice
            key={id}
            id={id}
            autoStart={task.deep === true || task.analysis_started === true}
          />
        )}
    </main>
  );
}
