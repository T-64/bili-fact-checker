from __future__ import annotations

from dataclasses import replace

import pytest

from bili_fact_checker.config import Settings
from bili_fact_checker.providers.search import (
    AnthropicSearchProvider,
    BudgetedSearchProvider,
    GeminiSearchProvider,
    OpenAISearchProvider,
    SearchRequest,
    SearchUnavailableError,
    SearxngSearchProvider,
    UnavailableSearchProvider,
    ZaiSearchProvider,
    build_search_provider,
    detect_native_search_provider,
)


def settings(**changes) -> Settings:
    return replace(Settings.from_env(), **changes)


def test_auto_reuses_zai_llm_credentials_for_search():
    configured = settings(
        openai_api_base="https://api.z.ai/api/paas/v4",
        openai_api_key="same-account-key",
        search_api_base="",
        search_api_key="",
        search_provider="auto",
        searxng_url="",
        tavily_api_key="",
    )
    assert detect_native_search_provider(configured) == "zai"
    assert isinstance(build_search_provider(configured), ZaiSearchProvider)
    assert configured.effective_search_api_key == "same-account-key"


def test_unknown_openai_compatible_endpoint_is_not_assumed_to_have_search():
    configured = settings(
        openai_api_base="https://llm.example/v1",
        search_provider="auto",
        searxng_url="",
        tavily_api_key="",
    )
    provider = build_search_provider(configured)
    assert isinstance(provider, UnavailableSearchProvider)
    with pytest.raises(SearchUnavailableError, match="no supported native"):
        provider.search(SearchRequest(query_id="query_0001_01", text="测试查询"))


def test_auto_can_fall_back_to_explicit_searxng():
    configured = settings(
        openai_api_base="https://llm.example/v1",
        search_provider="auto",
        searxng_url="https://search.example",
    )
    assert isinstance(build_search_provider(configured), SearxngSearchProvider)


@pytest.mark.parametrize(
    ("base", "provider_type"),
    [
        ("https://api.openai.com/v1", OpenAISearchProvider),
        ("https://generativelanguage.googleapis.com/v1beta", GeminiSearchProvider),
        ("https://api.anthropic.com/v1", AnthropicSearchProvider),
    ],
)
def test_auto_routes_known_native_provider_without_live_probe(base, provider_type):
    configured = settings(
        openai_api_base=base,
        search_api_base="",
        search_provider="auto",
        searxng_url="",
        tavily_api_key="",
    )
    assert isinstance(build_search_provider(configured), provider_type)


def test_zai_structured_results_are_normalized_and_malformed_urls_rejected():
    calls = []

    def transport(url, payload, **kwargs):
        calls.append((url, payload, kwargs))
        return {
            "request_id": "request-123",
            "search_result": [
                {
                    "title": "官方统计",
                    "content": "这是用于发现网页的摘要，不是最终证据。",
                    "link": "https://stats.example/report",
                    "media": "统计机构",
                    "publish_date": "2026-08-01",
                    "refer": "ref_1",
                },
                {"title": "bad", "link": "javascript:alert(1)"},
                "not-an-object",
            ],
        }

    configured = settings(
        openai_api_base="https://api.z.ai/api/paas/v4",
        openai_api_key="secret",
        search_provider="zai",
        search_api_base="",
        search_api_key="",
    )
    provider = ZaiSearchProvider(configured, transport=transport)
    batch = provider.search(
        SearchRequest(
            query_id="query_0001_01",
            text="某项官方统计",
            language="zh",
            limit=5,
            first_candidate_number=7,
        )
    )

    assert calls[0][0] == "https://api.z.ai/api/paas/v4/web_search"
    assert calls[0][1]["search_query"] == "某项官方统计"
    assert calls[0][2]["headers"] == {"Authorization": "Bearer secret"}
    assert len(batch.candidates) == 1
    candidate = batch.candidates[0]
    assert candidate.id == "candidate_00007"
    assert candidate.provider == "zai"
    assert str(candidate.url) == "https://stats.example/report"
    assert candidate.published_at == "2026-08-01"
    assert batch.usage is not None
    assert batch.usage.provider_request_id == "request-123"
    assert batch.usage.billable_uses is None
    assert batch.warnings == ["rejected 2 malformed search result(s)"]


def test_zai_missing_key_fails_before_network_call():
    configured = settings(
        openai_api_base="https://api.z.ai/api/paas/v4",
        openai_api_key="",
        search_api_key="",
    )
    provider = ZaiSearchProvider(configured, transport=lambda *_a, **_k: {})
    with pytest.raises(SearchUnavailableError, match="requires"):
        provider.search(SearchRequest(query_id="query_0001_01", text="测试查询"))


def test_openai_sources_and_annotations_normalize_to_unique_urls():
    def transport(url, payload, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        assert payload["tools"] == [{"type": "web_search"}]
        assert payload["include"] == ["web_search_call.action.sources"]
        assert kwargs["headers"] == {"Authorization": "Bearer secret"}
        return {
            "id": "resp_123",
            "output": [
                {
                    "type": "web_search_call",
                    "id": "ws_1",
                    "action": {
                        "type": "search",
                        "sources": [
                            {"title": "Official source", "url": "https://official.example/a"},
                            {"title": "bad", "url": "not-a-url"},
                        ],
                    },
                },
                {
                    "type": "message",
                    "id": "msg_1",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Official source supports the statement.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://official.example/a",
                                    "title": "Official source",
                                    "start_index": 0,
                                    "end_index": 15,
                                },
                                {
                                    "type": "url_citation",
                                    "url": "https://second.example/b",
                                    "title": "Second source",
                                    "start_index": 16,
                                    "end_index": 38,
                                },
                            ],
                        }
                    ],
                },
            ],
        }

    configured = settings(
        openai_api_base="https://api.openai.com/v1",
        openai_api_key="secret",
        search_api_base="",
        search_api_key="",
        openai_model="gpt-fixture",
    )
    batch = OpenAISearchProvider(configured, transport=transport).search(
        SearchRequest(query_id="query_0001_01", text="测试查询")
    )
    assert [str(item.url) for item in batch.candidates] == [
        "https://official.example/a",
        "https://second.example/b",
    ]
    assert batch.usage is not None
    assert batch.usage.billable_uses == 1
    assert batch.warnings == ["rejected 1 malformed search result(s)"]


def test_gemini_grounding_annotations_are_normalized():
    def transport(url, payload, **kwargs):
        assert url == "https://generativelanguage.googleapis.com/v1beta/interactions"
        assert payload["tools"] == [{"type": "google_search"}]
        assert kwargs["headers"] == {"x-goog-api-key": "secret"}
        return {
            "id": "interaction_123",
            "steps": [
                {"type": "google_search_call", "arguments": {"queries": ["q"]}},
                {
                    "type": "model_output",
                    "content": [
                        {
                            "type": "text",
                            "text": "A source-backed statement.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://gemini-source.example/report",
                                    "title": "Gemini source",
                                    "start_index": 0,
                                    "end_index": 15,
                                }
                            ],
                        }
                    ],
                },
            ],
        }

    configured = settings(
        openai_api_base="https://generativelanguage.googleapis.com/v1beta",
        openai_api_key="secret",
        search_api_base="",
        search_api_key="",
        openai_model="gemini-fixture",
    )
    batch = GeminiSearchProvider(configured, transport=transport).search(
        SearchRequest(query_id="query_0001_01", text="测试查询")
    )
    assert str(batch.candidates[0].url) == "https://gemini-source.example/report"
    assert batch.candidates[0].snippet == "A source-backed"
    assert batch.usage is not None
    assert batch.usage.billable_uses == 1


def test_anthropic_result_blocks_and_citations_are_normalized():
    def transport(url, payload, **kwargs):
        assert url == "https://api.anthropic.com/v1/messages"
        assert payload["tools"][0]["type"] == "web_search_20250305"
        assert payload["tools"][0]["max_uses"] == 1
        assert kwargs["headers"]["x-api-key"] == "secret"
        return {
            "id": "msg_123",
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtool_1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://anthropic-source.example/report",
                            "title": "Anthropic source",
                            "page_age": "August 1, 2026",
                        }
                    ],
                },
                {
                    "type": "text",
                    "text": "The cited answer.",
                    "citations": [
                        {
                            "type": "web_search_result_location",
                            "url": "https://anthropic-source.example/report",
                            "title": "Anthropic source",
                            "cited_text": "Exact provider citation text",
                            "encrypted_index": "encrypted-ref",
                        }
                    ],
                },
            ],
            "usage": {"server_tool_use": {"web_search_requests": 1}},
        }

    configured = settings(
        openai_api_base="https://api.anthropic.com/v1",
        openai_api_key="secret",
        search_api_base="",
        search_api_key="",
        openai_model="claude-fixture",
    )
    batch = AnthropicSearchProvider(configured, transport=transport).search(
        SearchRequest(query_id="query_0001_01", text="测试查询")
    )
    assert len(batch.candidates) == 1
    assert str(batch.candidates[0].url) == "https://anthropic-source.example/report"
    assert batch.candidates[0].published_at == "August 1, 2026"
    assert batch.usage is not None
    assert batch.usage.billable_uses == 1


@pytest.mark.parametrize("limit", [0, 51])
def test_search_request_enforces_result_budget(limit):
    with pytest.raises(ValueError, match="between 1 and 50"):
        SearchRequest(query_id="query_0001_01", text="测试查询", limit=limit)


def test_run_wide_search_budget_is_enforced_before_extra_provider_call():
    calls = 0

    class Provider:
        name = "fixture"
        capabilities = ZaiSearchProvider.capabilities

        def search(self, request):
            nonlocal calls
            calls += 1
            from bili_fact_checker.providers.search import SearchBatch

            return SearchBatch(provider=self.name)

    budgeted = BudgetedSearchProvider(Provider(), max_calls=1)
    request = SearchRequest(query_id="query_0001_01", text="测试查询")
    budgeted.search(request)
    with pytest.raises(SearchUnavailableError, match="budget exhausted"):
        budgeted.search(request)
    assert calls == 1
