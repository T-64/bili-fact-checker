"""LLM-provider contracts with native Gemini and Anthropic transports."""

from __future__ import annotations

from typing import Any, Callable, Protocol
from urllib.parse import quote, urlsplit

from bili_fact_checker.config import Settings
from bili_fact_checker.httputil import post_json


class LlmProviderError(RuntimeError):
    pass


class LlmProvider(Protocol):
    name: str

    def complete(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float,
    ) -> str: ...


PostJson = Callable[..., Any]


def _endpoint(base: str, suffix: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(suffix) else f"{base}/{suffix.lstrip('/')}"


class OpenAICompatibleLlmProvider:
    name = "openai-compatible"

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def complete(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float,
    ) -> str:
        if not self._settings.openai_api_key:
            raise LlmProviderError(
                "缺少 LLM API key：设置 OPENAI_API_KEY 或 GLM_API_KEY"
            )
        try:
            data = self._transport(
                _endpoint(self._settings.openai_api_base, "chat/completions"),
                {
                    "model": self._settings.openai_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                },
                proxy=self._settings.proxy,
                headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}"
                },
                timeout=120,
            )
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LlmProviderError(
                "OpenAI-compatible LLM request failed or returned an invalid response"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmProviderError("OpenAI-compatible LLM returned empty text")
        return content


class GeminiLlmProvider:
    name = "gemini"

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def complete(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float,
    ) -> str:
        if not self._settings.openai_api_key:
            raise LlmProviderError("Gemini requires an API key")
        base = self._settings.openai_api_base.rstrip("/")
        model = quote(self._settings.openai_model.removeprefix("models/"), safe="-._")
        endpoint = f"{base}/models/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        try:
            data = self._transport(
                endpoint,
                payload,
                proxy=self._settings.proxy,
                headers={"x-goog-api-key": self._settings.openai_api_key},
                timeout=120,
            )
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(
                str(part.get("text") or "")
                for part in parts
                if isinstance(part, dict)
            )
        except Exception as exc:
            raise LlmProviderError(
                "Gemini LLM request failed or returned an invalid response"
            ) from exc
        if not text.strip():
            raise LlmProviderError("Gemini returned empty text")
        return text


class AnthropicLlmProvider:
    name = "anthropic"

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def complete(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float,
    ) -> str:
        if not self._settings.openai_api_key:
            raise LlmProviderError("Anthropic requires an API key")
        payload = {
            "model": self._settings.openai_model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        try:
            data = self._transport(
                _endpoint(self._settings.openai_api_base, "messages"),
                payload,
                proxy=self._settings.proxy,
                headers={
                    "x-api-key": self._settings.openai_api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=120,
            )
            text = "".join(
                str(block.get("text") or "")
                for block in data["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except Exception as exc:
            raise LlmProviderError(
                "Anthropic LLM request failed or returned an invalid response"
            ) from exc
        if not text.strip():
            raise LlmProviderError("Anthropic returned empty text")
        return text


def detect_llm_provider(settings: Settings) -> str:
    host = (urlsplit(settings.openai_api_base).hostname or "").lower()
    if host in {"generativelanguage.googleapis.com", "ai.google.dev"}:
        return "gemini"
    if host == "api.anthropic.com":
        return "anthropic"
    return "openai-compatible"


def build_llm_provider(
    settings: Settings, *, transport: PostJson = post_json
) -> LlmProvider:
    requested = settings.llm_provider.strip().lower() or "auto"
    aliases = {
        "openai": "openai-compatible",
        "zai": "openai-compatible",
        "z.ai": "openai-compatible",
        "zhipu": "openai-compatible",
        "glm": "openai-compatible",
        "claude": "anthropic",
    }
    requested = aliases.get(requested, requested)
    if requested == "auto":
        requested = detect_llm_provider(settings)
    if requested == "gemini":
        return GeminiLlmProvider(settings, transport=transport)
    if requested == "anthropic":
        return AnthropicLlmProvider(settings, transport=transport)
    if requested == "openai-compatible":
        return OpenAICompatibleLlmProvider(settings, transport=transport)
    raise LlmProviderError(f"unsupported LLM provider: {requested}")
