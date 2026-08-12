from __future__ import annotations

from bili_fact_checker.cache import (
    CachedPageFetcher,
    CachedSearchProvider,
    JsonDiskCache,
)
from bili_fact_checker.evidence.fetch import FetchedPage
from bili_fact_checker.models import (
    EvidenceDocument,
    SearchCandidate,
    SearchProviderCapabilities,
    SearchUsage,
    utc_now,
)
from bili_fact_checker.providers.search import SearchBatch, SearchRequest


class CountingSearchProvider:
    name = "fixture"
    capabilities = SearchProviderCapabilities(provider=name)

    def __init__(self) -> None:
        self.calls = 0

    def search(self, request: SearchRequest) -> SearchBatch:
        self.calls += 1
        candidate = SearchCandidate(
            id=f"candidate_{request.first_candidate_number:05d}",
            query_id=request.query_id,
            provider=self.name,
            rank=1,
            title="来源",
            url="https://source.example/report",
        )
        return SearchBatch(
            provider=self.name,
            candidates=[candidate],
            usage=SearchUsage(
                provider=self.name, request_count=1, result_count=1
            ),
        )


def candidate(identifier: str = "candidate_00001") -> SearchCandidate:
    return SearchCandidate(
        id=identifier,
        query_id="query_0001_01",
        provider="fixture",
        rank=1,
        title="来源",
        url="https://source.example/report",
    )


def test_search_cache_avoids_second_provider_call_and_rebinds_ids(tmp_path):
    backend = CountingSearchProvider()
    provider = CachedSearchProvider(
        backend,
        JsonDiskCache(tmp_path, "search", 60),
        endpoint_namespace="fixture-v1",
    )
    first = provider.search(
        SearchRequest(query_id="query_0001_01", text="同一个 查询")
    )
    second = provider.search(
        SearchRequest(
            query_id="query_0002_01",
            text="  同一个   查询 ",
            first_candidate_number=7,
        )
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert backend.calls == 1
    assert second.candidates[0].id == "candidate_00007"
    assert second.candidates[0].query_id == "query_0002_01"
    assert second.usage is not None
    assert second.usage.request_count == 0
    assert second.usage.billable_uses == 0


def test_page_cache_avoids_second_fetch_and_rebinds_ids(tmp_path):
    calls = 0

    def fetcher(item, *, document_id, **_kwargs):
        nonlocal calls
        calls += 1
        text = "这是一段已经从公开网页提取出的足够长正文，用于验证页面缓存不会再次请求远端。"
        return FetchedPage(
            document=EvidenceDocument(
                id=document_id,
                candidate_id=item.id,
                url=item.url,
                canonical_url=item.url,
                title=item.title,
                retrieved_at=utc_now(),
                content_sha256="a" * 64,
                char_count=len(text),
            ),
            text=text,
        )

    cached_fetch = CachedPageFetcher(
        JsonDiskCache(tmp_path, "pages", 60), fetcher=fetcher
    )
    first = cached_fetch(candidate(), document_id="doc_00001")
    second = cached_fetch(
        candidate("candidate_00009"), document_id="doc_00003"
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert calls == 1
    assert second.document.id == "doc_00003"
    assert second.document.candidate_id == "candidate_00009"
