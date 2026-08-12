from __future__ import annotations

from dataclasses import replace

from bili_fact_checker.config import Settings
from bili_fact_checker.evidence.fetch import FetchedPage, PageFetchError
from bili_fact_checker.evidence.service import EvidenceService
from bili_fact_checker.models import (
    AtomicClaim,
    EvidenceAssessment,
    EvidenceDocument,
    EvidenceStance,
    SearchCandidate,
    SearchProviderCapabilities,
    SearchUsage,
    SourceQuality,
    Verdict,
    utc_now,
)
from bili_fact_checker.providers.search import SearchBatch


def configured_settings(**changes) -> Settings:
    values = {
        "max_searches_per_claim": 1,
        "search_results_per_query": 5,
        **changes,
    }
    return replace(Settings.from_env(), **values)


def claim() -> AtomicClaim:
    return AtomicClaim(
        id="claim_0001",
        claim_zh="世界卫生组织称该指标在2024年下降10%",
        claim_en="WHO said the indicator fell 10 percent in 2024",
        claim_type="统计数据",
        quote="该指标下降了百分之十",
        anchor_segment_ids=["seg_00001"],
        timestamp_sec=8,
        entities=["世界卫生组织"],
        temporal_context="2024年",
    )


class FakeSearchProvider:
    name = "fixture"
    capabilities = SearchProviderCapabilities(provider=name)

    def __init__(self, urls):
        self.urls = urls

    def search(self, request):
        candidates = [
            SearchCandidate(
                id=f"candidate_{index:05d}",
                query_id=request.query_id,
                provider=self.name,
                rank=index,
                title=f"来源 {index}",
                url=url,
                snippet="该摘要只能用于发现 URL。",
            )
            for index, url in enumerate(self.urls, start=1)
        ]
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
            ),
        )


class RoundSearchProvider:
    name = "fixture"
    capabilities = SearchProviderCapabilities(provider=name)

    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = 0

    def search(self, request):
        urls = self.rounds[self.calls]
        self.calls += 1
        candidates = [
            SearchCandidate(
                id=f"candidate_{request.first_candidate_number + index - 1:05d}",
                query_id=request.query_id,
                provider=self.name,
                rank=index,
                title=f"来源 {index}",
                url=url,
            )
            for index, url in enumerate(urls, start=1)
        ]
        return SearchBatch(provider=self.name, candidates=candidates)


def fetched_page(candidate, document_id, **_kwargs):
    text = (
        "世界卫生组织在完整报告中说明，该指标在2024年下降10%，"
        "统计范围包含全部参与成员国，数据表和计算方法见报告附件。"
    )
    return FetchedPage(
        document=EvidenceDocument(
            id=document_id,
            candidate_id=candidate.id,
            url=candidate.url,
            canonical_url=candidate.url,
            title=candidate.title,
            retrieved_at=utc_now(),
            content_sha256="a" * 64,
            char_count=len(text),
            source_quality=SourceQuality.CREDIBLE,
        ),
        text=text,
    )


def supporting_assessor(_settings, _claim, excerpts):
    return [
        EvidenceAssessment(
            excerpt_id=item.id,
            stance=EvidenceStance.SUPPORTS,
            rationale="摘录以相同机构、年份和口径直接支持声明。",
            model="fixture-model",
        )
        for item in excerpts
    ]


def test_two_fetched_independent_sources_can_reach_supported():
    service = EvidenceService(
        configured_settings(),
        FakeSearchProvider(
            ["https://one.example/report", "https://two.example/report"]
        ),
        page_fetcher=fetched_page,
        excerpt_assessor=supporting_assessor,
    )
    result = service.analyze_claim(claim())
    assert result.analysis.verdict.verdict == Verdict.SUPPORTED
    assert len(result.analysis.documents) == 2
    assert len(result.analysis.excerpts) == 2
    assert len(result.analysis.assessments) == 2
    assert result.usage[0].request_count == 1


def test_search_snippets_do_not_count_when_pages_cannot_be_fetched():
    def fail_fetch(*_args, **_kwargs):
        raise PageFetchError("blocked by publisher")

    service = EvidenceService(
        configured_settings(),
        FakeSearchProvider(["https://one.example/report"]),
        page_fetcher=fail_fetch,
        excerpt_assessor=supporting_assessor,
    )
    result = service.analyze_claim(claim())
    assert result.analysis.candidates
    assert result.analysis.documents == []
    assert result.analysis.verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert result.events[0].code == "page_fetch_failed"


def test_fabricated_assessment_id_is_rejected_before_aggregation():
    def dishonest_assessor(_settings, _claim, _excerpts):
        return [
            EvidenceAssessment(
                excerpt_id="excerpt_99999",
                stance=EvidenceStance.SUPPORTS,
                rationale="模型编造了一个证据标识。",
                model="fixture-model",
            )
        ]

    service = EvidenceService(
        configured_settings(),
        FakeSearchProvider(["https://one.example/report"]),
        page_fetcher=fetched_page,
        excerpt_assessor=dishonest_assessor,
    )
    result = service.analyze_claim(claim())
    assert result.analysis.assessments == []
    assert result.analysis.verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert result.events[0].code == "assessment_reference_rejected"


def test_iterative_search_stops_when_first_round_reaches_threshold():
    provider = RoundSearchProvider(
        [["https://one.example/report", "https://two.example/report"]]
    )
    service = EvidenceService(
        configured_settings(max_searches_per_claim=2),
        provider,
        page_fetcher=fetched_page,
        excerpt_assessor=supporting_assessor,
    )
    result = service.analyze_claim(claim())

    assert result.analysis.verdict.verdict == Verdict.SUPPORTED
    assert provider.calls == 1
    assert len(result.analysis.queries) == 1
    assert result.events[-1].code == "search_stopped_evidence_sufficient"


def test_iterative_search_records_gap_before_second_round():
    provider = RoundSearchProvider(
        [
            ["https://one.example/report"],
            ["https://two.example/report"],
        ]
    )
    service = EvidenceService(
        configured_settings(max_searches_per_claim=2),
        provider,
        page_fetcher=fetched_page,
        excerpt_assessor=supporting_assessor,
    )
    result = service.analyze_claim(claim())

    assert result.analysis.verdict.verdict == Verdict.SUPPORTED
    assert provider.calls == 2
    assert len(result.analysis.queries) == 2
    assert any(event.code == "evidence_gap" for event in result.events)
