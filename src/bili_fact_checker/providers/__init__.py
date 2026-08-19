"""Provider facades and strict-enough JSON extraction helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.providers.llm import build_llm_provider


def chat(
    settings: Settings,
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    temperature: float = 0.1,
) -> str:
    provider = build_llm_provider(settings)
    return provider.complete(
        prompt,
        system=system,
        temperature=temperature,
    )


def extract_json_array(text: str) -> list[Any]:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []
    try:
        value = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return {}
    value = json.loads(match.group())
    return value if isinstance(value, dict) else {}
