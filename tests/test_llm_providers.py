from __future__ import annotations

from dataclasses import replace

import pytest

from bili_fact_checker.config import Settings
from bili_fact_checker.providers.llm import (
    AnthropicLlmProvider,
    GeminiLlmProvider,
    LlmProviderError,
    OpenAICompatibleLlmProvider,
    build_llm_provider,
)


def settings(**changes) -> Settings:
    return replace(Settings.from_env(), **changes)


@pytest.mark.parametrize(
    ("base", "provider_type"),
    [
        ("https://api.z.ai/api/paas/v4", OpenAICompatibleLlmProvider),
        ("https://api.openai.com/v1", OpenAICompatibleLlmProvider),
        ("https://compatible.example/v1", OpenAICompatibleLlmProvider),
        ("https://generativelanguage.googleapis.com/v1beta", GeminiLlmProvider),
        ("https://api.anthropic.com/v1", AnthropicLlmProvider),
    ],
)
def test_auto_routes_llm_protocol_by_known_host(base, provider_type):
    configured = settings(openai_api_base=base, llm_provider="auto")
    assert isinstance(build_llm_provider(configured), provider_type)


def test_openai_compatible_transport():
    def transport(url, payload, **kwargs):
        assert url == "https://compatible.example/v1/chat/completions"
        assert payload["messages"][0] == {"role": "system", "content": "system"}
        assert kwargs["headers"] == {"Authorization": "Bearer secret"}
        return {"choices": [{"message": {"content": "result"}}]}

    configured = settings(
        openai_api_base="https://compatible.example/v1",
        openai_api_key="secret",
        openai_model="model",
        llm_provider="auto",
    )
    provider = build_llm_provider(configured, transport=transport)
    assert provider.complete("prompt", system="system", temperature=0.1) == "result"


def test_gemini_native_transport():
    def transport(url, payload, **kwargs):
        assert url == (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-fixture:generateContent"
        )
        assert payload["systemInstruction"]["parts"][0]["text"] == "system"
        assert kwargs["headers"] == {"x-goog-api-key": "secret"}
        return {
            "candidates": [
                {"content": {"parts": [{"text": "first "}, {"text": "second"}]}}
            ]
        }

    configured = settings(
        openai_api_base="https://generativelanguage.googleapis.com/v1beta",
        openai_api_key="secret",
        openai_model="gemini-fixture",
        llm_provider="auto",
    )
    provider = build_llm_provider(configured, transport=transport)
    assert provider.complete("prompt", system="system", temperature=0.1) == "first second"


def test_anthropic_native_transport():
    def transport(url, payload, **kwargs):
        assert url == "https://api.anthropic.com/v1/messages"
        assert payload["system"] == "system"
        assert kwargs["headers"]["x-api-key"] == "secret"
        return {
            "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": "result"},
            ]
        }

    configured = settings(
        openai_api_base="https://api.anthropic.com/v1",
        openai_api_key="secret",
        openai_model="claude-fixture",
        llm_provider="auto",
    )
    provider = build_llm_provider(configured, transport=transport)
    assert provider.complete("prompt", system="system", temperature=0.1) == "result"


def test_invalid_native_response_is_redacted_from_public_error():
    configured = settings(
        openai_api_base="https://api.anthropic.com/v1",
        openai_api_key="secret",
        openai_model="claude-fixture",
        llm_provider="auto",
    )
    provider = build_llm_provider(
        configured,
        transport=lambda *_args, **_kwargs: {"secret_response": "do not expose"},
    )
    with pytest.raises(LlmProviderError) as error:
        provider.complete("prompt", system="system", temperature=0.1)
    assert "secret_response" not in str(error.value)
