"""OpenAI-compatible LLM client (z.ai / OpenAI / local)."""

from __future__ import annotations

import json
import re
from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.httputil import post_json


def chat(
    settings: Settings,
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    temperature: float = 0.1,
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError(
            "缺少 LLM API key：设置 OPENAI_API_KEY 或 GLM_API_KEY（或 ~/.hermes/.env）"
        )

    url = f"{settings.openai_api_base}/chat/completions"
    payload = {
        "model": settings.openai_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    resp = post_json(
        url,
        payload,
        proxy=settings.proxy,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        timeout=120,
    )
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM 响应异常: {resp}") from e


def extract_json_array(text: str) -> list[Any]:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not m:
        return []
    return json.loads(m.group())


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return {}
    return json.loads(m.group())
