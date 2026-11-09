"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export function DiagnosisForm() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const fields = new FormData(event.currentTarget);
    const url = fields.get("run");
    setPending(true);
    setError("");
    try {
      const response = await fetch("/api/ci-diagnoses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, private: fields.has("private") }),
      });
      if (!response.ok) {
        setError(
          response.status === 422
            ? "请输入有效的 GitHub Actions 运行链接。"
            : response.status === 429
              ? "已有多个诊断在运行，请稍后再试。"
              : response.status === 403
                ? "私有仓库需要先连接 GitHub。"
                : response.status === 404
                  ? "无法访问这个运行，请检查链接和仓库授权。"
                  : "无法创建诊断，请重试。",
        );
        return;
      }
      const task = (await response.json()) as { id: string };
      router.push(`/diagnoses/runs/${encodeURIComponent(task.id)}`);
    } catch {
      setError("无法创建诊断，请重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <form
        id="ci-diagnosis-form"
        onSubmit={submit}
        className="mx-auto mt-9 flex w-full max-w-xl flex-col gap-2 rounded-xl border border-[#344763] bg-[#101b2c] p-2 text-left sm:flex-row"
      >
        <label htmlFor="diagnosis-run" className="sr-only">
          GitHub Actions 运行链接
        </label>
        <input
          id="diagnosis-run"
          name="run"
          type="url"
          required
          maxLength={400}
          autoComplete="off"
          spellCheck={false}
          aria-describedby={error ? "run-error" : "run-help"}
          aria-invalid={Boolean(error)}
          placeholder="https://github.com/owner/repo/actions/runs/…"
          className="min-h-11 min-w-0 flex-1 rounded-md px-3 font-mono text-xs text-white placeholder:text-[#aabbd6]"
        />
        <button
          type="submit"
          disabled={pending}
          className="min-h-11 shrink-0 rounded-md bg-[#91d6ff] px-4 text-sm font-semibold text-[#0a111c] hover:bg-[#b5e4ff] disabled:opacity-60"
        >
          {pending ? "正在提交…" : "开始诊断"}
        </button>
      </form>
      {error && (
        <p id="run-error" role="status" className="mt-3 text-sm text-[#ffb4a5]">
          {error}
        </p>
      )}
      <p id="run-help" className="mt-3 text-xs text-[#aabbd6]">
        无需登录，支持公开仓库。日志受限时仍会展示已取得的失败证据。
      </p>
      <div className="mt-3 flex flex-wrap items-center justify-center gap-4 text-xs text-[#aabbd6]">
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" form="ci-diagnosis-form" name="private" />
          私有仓库
        </label>
        <Link
          href="/diagnoses/github"
          className="text-[#91d6ff] underline underline-offset-4"
        >
          连接 GitHub
        </Link>
      </div>
    </>
  );
}
