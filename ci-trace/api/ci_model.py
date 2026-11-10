"""OpenAI-compatible model transport and operator-approved endpoints."""

import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

MODEL_ENDPOINTS = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "siliconflow": "https://api.siliconflow.cn/v1/chat/completions",
}
MAX_RESPONSE_BYTES = 256 * 1024


def model_endpoint(provider: str, base_url: str | None = None) -> str:
    if provider != "custom":
        if base_url:
            raise HTTPException(422, "Base URL is only supported for custom providers")
        return MODEL_ENDPOINTS[provider]
    base = (base_url or "").strip().rstrip("/")
    try:
        parts = urlsplit(base)
        valid = parts.scheme in {"http", "https"} and parts.hostname and parts.username is None and parts.port != 0 and not parts.query and not parts.fragment and not any(char.isspace() or char in "\\?#" for char in base)
    except ValueError:
        valid = False
    allowed = {entry.strip().rstrip("/") for entry in os.getenv("CI_TRACE_MODEL_BASE_URLS", "").split(",")}
    if not valid or base not in allowed:
        raise HTTPException(422, "Custom Base URL is not on the server allowlist")
    return base + "/chat/completions"


async def model_message(provider: str, model: str, key: str, messages: list[dict], *, base_url: str | None = None, tool: dict | None = None) -> dict:
    endpoint = model_endpoint(provider, base_url)
    payload = {"model": model, "messages": messages}
    if tool:
        payload.update(tools=[tool], tool_choice="auto" if tool["function"]["name"] == "read_log_excerpt" else "required")
    try:
        async with asyncio.timeout(20), httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async with client.stream("POST", endpoint, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("Model response exceeds the byte limit")
                    content.extend(chunk)
            message = json.loads(content)["choices"][0]["message"]
            if tool and (message.get("tool_calls") or payload["tool_choice"] == "required"):
                return {"tool_calls": message.get("tool_calls")}
            text = message["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty model response")
            return {"content": text.replace(key, "[redacted]")[:8000]}
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        raise HTTPException(502, "Model provider could not complete the request") from error
