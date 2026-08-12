from __future__ import annotations

from dataclasses import replace

import pytest

from bili_fact_checker.config import Settings
from bili_fact_checker.providers.search import (
    BudgetedSearchProvider,
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
