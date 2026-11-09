"use client";

import Link from "next/link";
import { useState } from "react";

export function ModelAdvice({ id }: { id: string }) {
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);

  async function analyze() {
    setPending(true);
    setNotice("");
    try {
      const response = await fetch("/api/ci-diagnoses/model/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: id }),
      });
      if (response.status === 409) {
        setNotice("请先配置模型，然后回到此处运行分析。");
        return;
      }
      if (!response.ok) throw new Error("Model unavailable");
      const result = (await response.json()) as { text: string };
      setText(result.text);
    } catch {
      setNotice("模型分析暂不可用，仍可查看上方的基础建议。");
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      className="mt-10 border-t border-[#25334b] pt-6"
      aria-labelledby="model-advice"
    >
      <h2 id="model-advice" className="text-lg font-semibold">
        模型补充建议
      </h2>
      <p className="mt-2 text-sm text-[#aabbd6]">
        开启后会将本次失败 Job 和错误摘要发给你选的模型。建议需自行验证。
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        <button
          type="button"
          disabled={pending}
          onClick={() => void analyze()}
          className="min-h-10 rounded-md bg-[#91d6ff] px-3 text-sm font-semibold text-[#0a111c] disabled:opacity-60"
        >
          {pending ? "正在分析…" : "使用模型分析"}
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
      {text && (
        <p className="mt-3 rounded-md border border-[#25334b] p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">
          {text}
        </p>
      )}
    </section>
  );
}
