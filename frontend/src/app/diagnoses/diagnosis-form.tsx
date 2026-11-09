"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

import { ModelFields } from "./model-fields";

type Model = { provider: string; model: string };

export function DiagnosisForm({
  privateRun = false,
}: {
  privateRun?: boolean;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [deep, setDeep] = useState(false);
  const [model, setModel] = useState<Model | null | undefined>();
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!deep) return;
    const controller = new AbortController();
    void fetch("/api/ci-diagnoses/model", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Model settings unavailable");
        setModel((await response.json()) as Model | null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setModel(null);
      });
    return () => controller.abort();
  }, [deep]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const fields = new FormData(event.currentTarget);
    const url = fields.get("run");
    setPending(true);
    setError("");
    try {
      if (deep && (!model || editing)) {
        fields.delete("run");
        fields.delete("diagnosis_mode");
        const saved = await fetch("/api/ci-diagnoses/model", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(Object.fromEntries(fields)),
        });
        if (!saved.ok) {
          setError("模型配置失败，请检查提供商、模型名、API Key 和地址。");
          return;
        }
        setModel((await saved.json()) as Model);
        setEditing(false);
      }
      const response = await fetch("/api/ci-diagnoses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, private: privateRun, deep }),
      });
      if (!response.ok) {
        if (response.status === 409) setEditing(true);
        setError(
          response.status === 409
            ? "模型配置已失效，请重新配置后提交。"
            : response.status === 422
              ? "请输入有效的 GitHub Actions 运行链接。"
              : response.status === 429
                ? "已有多个诊断在运行，请稍后再试。"
                : response.status === 403
                  ? "无法提交诊断，请检查仓库访问权限。"
                  : response.status === 404
                    ? "无法访问这个运行，请检查链接和仓库可见性。"
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
        className="mx-auto mt-9 flex w-full max-w-xl flex-col gap-3 rounded-xl border border-[#344763] bg-[#101b2c] p-2 text-left"
      >
        {!privateRun && (
          <fieldset
            className="flex flex-wrap gap-4 px-3 py-2 text-sm"
            disabled={pending}
          >
            <legend className="sr-only">诊断模式</legend>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="diagnosis_mode"
                value="basic"
                checked={!deep}
                onChange={() => setDeep(false)}
              />
              基础诊断
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="diagnosis_mode"
                value="deep"
                checked={deep}
                onChange={() => setDeep(true)}
              />
              深度分析
            </label>
          </fieldset>
        )}
        {deep && (
          <section
            className="grid gap-3 px-3 pb-2"
            aria-label="诊断 Agent 模型设置"
          >
            <p className="text-sm text-[#aabbd6]">
              证据采集完成后，CI 诊断 Agent 会自动开始分析。
            </p>
            {model === undefined ? (
              <p role="status" className="text-sm">
                正在读取模型配置…
              </p>
            ) : model && !editing ? (
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <span>
                  {model.provider} / {model.model}
                </span>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => setEditing(true)}
                  className="underline"
                >
                  修改模型
                </button>
              </div>
            ) : (
              <fieldset disabled={pending} className="grid gap-3">
                <legend className="sr-only">模型连接</legend>
                <ModelFields />
              </fieldset>
            )}
          </section>
        )}
        <div className="flex flex-col gap-2 sm:flex-row">
          <label htmlFor="diagnosis-run" className="sr-only">
            {privateRun
              ? "私有 GitHub Actions 运行链接"
              : "公开 GitHub Actions 运行链接"}
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
            disabled={pending || (deep && model === undefined)}
            className="min-h-11 shrink-0 rounded-md bg-[#91d6ff] px-4 text-sm font-semibold text-[#0a111c] hover:bg-[#b5e4ff] disabled:opacity-60"
          >
            {pending ? "正在提交…" : deep ? "开始深度分析" : "开始诊断"}
          </button>
        </div>
      </form>
      {error && (
        <p id="run-error" role="status" className="mt-3 text-sm text-[#ffb4a5]">
          {error}
        </p>
      )}
      <p id="run-help" className="mt-3 text-xs text-[#aabbd6]">
        {privateRun
          ? "使用已连接的 GitHub 账号读取运行。"
          : "粘贴公开仓库的运行链接。日志受限时，仍会展示已取得的失败证据。"}
      </p>
    </>
  );
}
