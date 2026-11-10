"use client";

import { type FormEvent, useEffect, useState } from "react";

import { ModelFields } from "../model-fields";

type Model = { provider: string; model: string };
const endpoint = "/api/ci-diagnoses/model";

export default function ModelSettingsPage() {
  const [model, setModel] = useState<Model | null>(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void fetch(endpoint, { signal: controller.signal })
      .then(async (response) => {
        if (response.ok) setModel((await response.json()) as Model | null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("无法读取当前模型配置。");
      });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setPending(true);
    setNotice("");
    try {
      const response = await fetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(fields)),
      });
      if (response.status === 422 && fields.get("provider") === "custom") {
        const { detail } = (await response.json()) as { detail?: unknown };
        if (detail === "Custom Base URL is not on the server allowlist") {
          setNotice("此 Base URL 未被服务端允许，请检查完整地址或联系部署者。");
          return;
        }
      }
      if (!response.ok) throw new Error("Model unavailable");
      setModel((await response.json()) as Model);
      form.reset();
      setNotice("连接测试成功，模型已保存。");
    } catch {
      setNotice("测试或保存失败，请检查模型名、API Key 和网络连接。");
    } finally {
      setPending(false);
    }
  }

  async function clear() {
    setPending(true);
    try {
      const response = await fetch(endpoint, { method: "DELETE" });
      if (!response.ok) throw new Error("Clear failed");
      setModel(null);
      setNotice("模型配置已清除。");
    } catch {
      setNotice("暂时无法清除模型配置，请重试。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-5 py-14 sm:px-8 sm:py-24">
      <header className="trace-enter">
        <h1 className="trace-display trace-display-small">模型设置</h1>
        <p className="mt-5 text-base leading-relaxed text-[#aabbd6]">
          模型用于补充修复建议。基础诊断和规则建议始终可用。API Key
          加密保存在服务端，当前浏览器会话可用 24 小时。
        </p>
      </header>
      <section className="trace-result-panel mt-8">
        <p className="text-sm font-medium break-words text-[#91d6ff]">
          {model
            ? `已配置 · ${model.provider} / ${model.model}`
            : "当前使用基础诊断"}
        </p>
        <form onSubmit={save} className="mt-6 grid gap-4">
          <ModelFields />
          <div className="flex flex-wrap gap-3">
            <button
              disabled={pending}
              className="min-h-11 rounded-md bg-[#91d6ff] px-4 text-sm font-semibold text-[#0a111c] disabled:opacity-60"
            >
              {pending ? "正在处理…" : "测试并保存"}
            </button>
            {model && (
              <button
                type="button"
                disabled={pending}
                onClick={() => void clear()}
                className="min-h-11 rounded-md border border-[#344763] px-4 text-sm disabled:opacity-60"
              >
                清除配置
              </button>
            )}
          </div>
        </form>
        <p role="status" className="mt-4 text-sm text-[#aabbd6]">
          {notice}
        </p>
      </section>
    </main>
  );
}
