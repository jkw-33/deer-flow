"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { DiagnosisForm } from "../diagnosis-form";

const endpoint = "/api/ci-diagnoses/github";
type Connection = { available: boolean; login: string | null };

export default function GitHubConnectionPage() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(endpoint, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Status unavailable");
        setConnection((await response.json()) as Connection);
        if (
          new URLSearchParams(window.location.search).get("result") === "failed"
        ) {
          setNotice("GitHub 授权未完成，请重试。");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("无法读取 GitHub 连接状态。");
      });
    return () => controller.abort();
  }, []);

  async function update(method: "POST" | "DELETE") {
    setPending(true);
    setNotice("");
    try {
      const response = await fetch(endpoint, { method });
      if (!response.ok) throw new Error("Connection unavailable");
      if (method === "POST") {
        const { url } = (await response.json()) as { url: string };
        window.location.assign(url);
        return;
      }
      setConnection((previous) => previous && { ...previous, login: null });
      setNotice("GitHub 已断开，私有诊断已不可访问。");
    } catch {
      setNotice("操作失败，请稍后重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="trace-enter mx-auto max-w-xl px-5 py-20 sm:px-8">
      <h1 className="text-4xl font-semibold tracking-tight">连接 GitHub</h1>
      <p className="mt-4 text-sm leading-relaxed text-[#aabbd6]">
        诊断私有仓库需要 GitHub 授权，并要求目标仓库已安装 CI Trace 使用的
        GitHub App。
      </p>
      <p className="mt-6 text-sm text-[#91d6ff]">
        {connection?.login
          ? `已连接 · ${connection.login}`
          : connection?.available
            ? "尚未连接"
            : connection
              ? "当前部署尚未配置 GitHub 用户授权。"
              : "正在读取连接状态…"}
      </p>
      <div className="mt-6 flex flex-wrap items-center gap-4">
        {connection?.available && (
          <button
            type="button"
            disabled={pending}
            onClick={() => void update(connection.login ? "DELETE" : "POST")}
            className="min-h-11 rounded-md bg-[#91d6ff] px-4 text-sm font-semibold text-[#0a111c] disabled:opacity-60"
          >
            {connection.login ? "断开 GitHub" : "前往 GitHub 授权"}
          </button>
        )}
        <Link
          href="/diagnoses"
          className="text-sm text-[#91d6ff] underline underline-offset-4"
        >
          返回诊断首页
        </Link>
      </div>
      {connection?.login && <DiagnosisForm privateRun />}
      <p role="status" className="mt-4 text-sm text-[#aabbd6]">
        {notice}
      </p>
    </main>
  );
}
