"use client";

import { useState } from "react";

export function ModelFields() {
  const [provider, setProvider] = useState("deepseek");
  return (
    <>
      <label className="grid gap-2 text-sm">
        提供商
        <select
          name="provider"
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
          className="min-h-11 rounded-md border border-[#344763] bg-[#101b2c] px-3"
        >
          <option value="deepseek">DeepSeek</option>
          <option value="openai">OpenAI</option>
          <option value="gemini">Gemini</option>
          <option value="openrouter">OpenRouter</option>
          <option value="siliconflow">硅基流动</option>
          <option value="custom">自定义 OpenAI 兼容服务</option>
        </select>
      </label>
      {provider === "custom" && (
        <label className="grid gap-2 text-sm">
          Base URL
          <input
            name="base_url"
            type="url"
            required
            maxLength={400}
            autoComplete="off"
            spellCheck={false}
            placeholder="https://gateway.example/v1"
            className="min-h-11 rounded-md border border-[#344763] bg-[#101b2c] px-3"
          />
          <span className="text-xs text-[#aabbd6]">
            填入服务端已允许的 API 基础地址，省略 /chat/completions。
          </span>
        </label>
      )}
      <label className="grid gap-2 text-sm">
        模型名
        <input
          name="model"
          required
          maxLength={120}
          autoComplete="off"
          spellCheck={false}
          className="min-h-11 rounded-md border border-[#344763] bg-[#101b2c] px-3"
        />
      </label>
      <label className="grid gap-2 text-sm">
        API Key
        <input
          name="api_key"
          type="password"
          required
          maxLength={512}
          autoComplete="off"
          className="min-h-11 rounded-md border border-[#344763] bg-[#101b2c] px-3"
        />
      </label>
    </>
  );
}
