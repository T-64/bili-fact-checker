"""One auditable claim-to-evidence vertical slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from bili_fact_checker.config import Settings
from bili_fact_checker.evidence.core import aggregate_verdict, validate_assessments
from bili_fact_checker.evidence.fetch import (
    FetchedPage,
    PageFetchError,
    extract_relevant_excerpts,
    fetch_candidate,
)
from bili_fact_checker.evidence.rank import (
    EvidenceReranker,
    build_evidence_reranker,
)
from bili_fact_checker.models import (
    AnalysisEvent,
    AtomicClaim,
    ClaimAnalysis,
    EventLevel,
    EvidenceAssessment,
    EvidenceExcerpt,
    SearchCandidate,
    SearchQuery,
    SearchUsage,
    Verdict,
)
from bili_fact_checker.providers import chat, extract_json_array
from bili_fact_checker.providers.search import (
    SearchProvider,
    SearchProviderError,
    SearchRequest,
)


PageFetcher = Callable[..., FetchedPage]
ExcerptAssessor = Callable[
    [Settings, AtomicClaim, list[EvidenceExcerpt]], list[EvidenceAssessment]
]


@dataclass(frozen=True)
class ClaimEvidenceResult:
    analysis: ClaimAnalysis
    events: list[AnalysisEvent] = field(default_factory=list)
    usage: list[SearchUsage] = field(default_factory=list)


def build_search_queries(
    claim: AtomicClaim, *, limit: int
) -> list[SearchQuery]:
    """Build bounded exact, primary-source, and cross-language search queries."""

    claim_number = claim.id.removeprefix("claim_")
    source_query = " ".join(
        value
        for value in [
            "官方 原始来源",
            *claim.entities[:3],
            claim.temporal_context,
            claim.claim_zh,
        ]
        if value.strip()
    )
    values = [
        ("zh", claim.claim_zh),
        ("zh", source_query),
        ("en", claim.claim_en),
    ]
    queries: list[SearchQuery] = []
    seen: set[str] = set()
    for language, value in values:
        clean = " ".join(value.split()).strip()
        key = clean.casefold()
        if len(clean) < 2 or key in seen:
            continue
        seen.add(key)
        queries.append(
            SearchQuery(
                id=f"query_{claim_number}_{len(queries) + 1:02d}",
                language=language,
                text=clean[:300],
            )
        )
        if len(queries) >= limit:
            break
    return queries


def assess_excerpts_with_llm(
    settings: Settings,
    claim: AtomicClaim,
    excerpts: list[EvidenceExcerpt],
) -> list[EvidenceAssessment]:
    """Classify fixed excerpt IDs; this function cannot create sources."""

    if not excerpts:
        return []
    rendered = "\n\n".join(
        f"[{excerpt.id}]\n{excerpt.text}" for excerpt in excerpts
    )
    prompt = f"""逐条判断证据摘录与声明的关系。

声明：{claim.claim_zh}
限定语境：{claim.temporal_context}

证据摘录：
{rendered}

只输出 JSON 数组，每个输入 excerpt_id 恰好一项：
{{"excerpt_id":"excerpt_XXXXX",
  "stance":"supports|refutes|context|irrelevant|unclear",
  "rationale":"仅说明该摘录与声明的直接关系，不补充外部知识"}}

规则：
- supports：摘录在相同对象、时间和口径下直接支持声明；
- refutes：摘录在相同对象、时间和口径下直接否定声明；
- context：相关但不足以支持或反驳；
- irrelevant：没有实质关系；
- unclear：语境、时间或统计口径不明确。
不得输出 URL、来源等级、最终真假或输入中不存在的 ID。
"""
    raw = chat(
        settings,
        prompt,
        system="你是证据关系分类器，只处理给定摘录并输出 JSON。",
    )
    assessments: list[EvidenceAssessment] = []
    for item in extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        try:
            assessments.append(
                EvidenceAssessment(
                    excerpt_id=str(item.get("excerpt_id") or ""),
                    stance=str(item.get("stance") or "unclear"),
                    rationale=str(item.get("rationale") or "无法判断证据关系")[:500],
                    model=settings.openai_model,
                )
            )
        except ValidationError:
            continue
    return assessments


class EvidenceService:
    def __init__(
        self,
        settings: Settings,
        search_provider: SearchProvider,
        *,
        page_fetcher: PageFetcher = fetch_candidate,
        excerpt_assessor: ExcerptAssessor = assess_excerpts_with_llm,
        reranker: EvidenceReranker | None = None,
    ) -> None:
        self.settings = settings
        self.search_provider = search_provider
        self.page_fetcher = page_fetcher
        self.excerpt_assessor = excerpt_assessor
        self.reranker = reranker or build_evidence_reranker(settings)

    def analyze_claim(self, claim: AtomicClaim) -> ClaimEvidenceResult:
        events: list[AnalysisEvent] = []
        usages: list[SearchUsage] = []
        planned_queries = build_search_queries(
            claim, limit=self.settings.max_searches_per_claim
        )
        executed_queries: list[SearchQuery] = []
        candidates: list[SearchCandidate] = []
        documents = []
        excerpts: list[EvidenceExcerpt] = []
        assessments: list[EvidenceAssessment] = []
        seen_urls: set[str] = set()
        verdict = aggregate_verdict([], [], [])

        for query_index, query in enumerate(planned_queries):
            executed_queries.append(query)
            try:
                batch = self.search_provider.search(
                    SearchRequest(
                        query_id=query.id,
                        text=query.text,
                        language=query.language,
                        limit=self.settings.search_results_per_query,
                        first_candidate_number=len(candidates) + 1,
                    )
                )
            except SearchProviderError as exc:
                events.append(
                    AnalysisEvent(
                        stage="search",
                        level=EventLevel.ERROR,
                        code="search_failed",
                        message=str(exc),
                        details={"claim_id": claim.id, "query_id": query.id},
                    )
                )
                continue
            if batch.usage:
                usages.append(batch.usage)
            if batch.cache_hit:
                events.append(
                    AnalysisEvent(
                        stage="search",
                        code="search_cache_hit",
                        message="复用了仍在有效期内的搜索结果。",
                        details={"claim_id": claim.id, "query_id": query.id},
                    )
                )
            for warning in batch.warnings:
                events.append(
                    AnalysisEvent(
                        stage="search",
                        level=EventLevel.WARNING,
                        code="search_result_rejected",
                        message=warning,
                        details={"claim_id": claim.id, "query_id": query.id},
                    )
                )
            new_candidates: list[SearchCandidate] = []
            for candidate in batch.candidates:
                key = str(candidate.url)
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                # Renumber after cross-query URL deduplication.
                normalized = candidate.model_copy(
                    update={"id": f"candidate_{len(candidates) + 1:05d}"}
                )
                candidates.append(normalized)
                new_candidates.append(normalized)

            new_excerpts: list[EvidenceExcerpt] = []
            for candidate in new_candidates:
                try:
                    page = self.page_fetcher(
                        candidate,
                        document_id=f"doc_{len(documents) + 1:05d}",
                        proxy=self.settings.proxy,
                        timeout=self.settings.fetch_timeout_seconds,
                        max_bytes=self.settings.fetch_max_bytes,
                    )
                except (PageFetchError, OSError, ValueError) as exc:
                    events.append(
                        AnalysisEvent(
                            stage="fetch",
                            level=EventLevel.WARNING,
                            code="page_fetch_failed",
                            message=str(exc),
                            details={
                                "claim_id": claim.id,
                                "candidate_id": candidate.id,
                                "url": str(candidate.url),
                            },
                        )
                    )
                    continue
                documents.append(page.document)
                if page.cache_hit:
                    events.append(
                        AnalysisEvent(
                            stage="fetch",
                            code="page_cache_hit",
                            message="复用了仍在有效期内的页面正文。",
                            details={
                                "claim_id": claim.id,
                                "candidate_id": candidate.id,
                                "url": str(candidate.url),
                            },
                        )
                    )
                selected = extract_relevant_excerpts(
                    page,
                    claim_text=claim.claim_zh,
                    quote=claim.quote,
                    entities=claim.entities,
                    first_id=len(excerpts) + len(new_excerpts) + 1,
                    limit=3,
                    reranker=self.reranker,
                )
                new_excerpts.extend(selected)

            excerpts.extend(new_excerpts)
            if new_excerpts:
                try:
                    untrusted = self.excerpt_assessor(
                        self.settings, claim, new_excerpts
                    )
                    accepted, rejected = validate_assessments(
                        untrusted, new_excerpts
                    )
                    assessments.extend(accepted)
                    if rejected:
                        events.append(
                            AnalysisEvent(
                                stage="assessment",
                                level=EventLevel.WARNING,
                                code="assessment_reference_rejected",
                                message="模型返回了未知或重复的证据 ID，已丢弃。",
                                details={
                                    "claim_id": claim.id,
                                    "excerpt_ids": rejected,
                                },
                            )
                        )
                except Exception as exc:
                    events.append(
                        AnalysisEvent(
                            stage="assessment",
                            level=EventLevel.ERROR,
                            code="assessment_failed",
                            message=str(exc),
                            details={"claim_id": claim.id},
                        )
                    )

            verdict = aggregate_verdict(assessments, excerpts, documents)
            if verdict.verdict != Verdict.INSUFFICIENT_EVIDENCE:
                if query_index + 1 < len(planned_queries):
                    events.append(
                        AnalysisEvent(
                            stage="search",
                            code="search_stopped_evidence_sufficient",
                            message="证据已达到判定门槛，未继续消耗搜索预算。",
                            details={
                                "claim_id": claim.id,
                                "unused_queries": len(planned_queries)
                                - query_index
                                - 1,
                            },
                        )
                    )
                break
            if query_index + 1 < len(planned_queries):
                events.append(
                    AnalysisEvent(
                        stage="search",
                        code="evidence_gap",
                        message="当前证据未达到判定门槛，继续下一轮检索。",
                        details={
                            "claim_id": claim.id,
                            "documents": len(documents),
                            "directional_excerpts": len(
                                verdict.supporting_excerpt_ids
                                + verdict.refuting_excerpt_ids
                            ),
                            "strength": verdict.strength.value,
                        },
                    )
                )

        analysis = ClaimAnalysis(
            claim=claim,
            queries=executed_queries,
            candidates=candidates,
            documents=documents,
            excerpts=excerpts,
            assessments=assessments,
            verdict=verdict,
        )
        return ClaimEvidenceResult(analysis=analysis, events=events, usage=usages)
