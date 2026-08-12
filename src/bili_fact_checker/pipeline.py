"""End-to-end pipeline."""

from __future__ import annotations

from typing import Any, Callable

from bili_fact_checker.analyze import extract_claims, summarize
from bili_fact_checker.cache import (
    CachedPageFetcher,
    CachedSearchProvider,
    JsonDiskCache,
)
from bili_fact_checker.config import Settings
from bili_fact_checker.evidence import EvidenceService
from bili_fact_checker.ingest import fetch_transcript
from bili_fact_checker.models import AnalysisEvent, AtomicClaim, EventLevel
from bili_fact_checker.providers.search import (
    BudgetedSearchProvider,
    build_search_provider,
)
from bili_fact_checker.report import build_report


LogFn = Callable[[str], None]


def run_pipeline(
    settings: Settings,
    url_or_bvid: str,
    *,
    tasks: list[str] | None = None,
    lang: str = "zh-CN",
    asr: bool = True,
    transcript_file: str | None = None,
    page: int = 1,
    log: LogFn | None = None,
) -> dict[str, Any]:
    tasks = tasks or ["summary", "verify"]
    _log = log or (lambda _m: None)

    if transcript_file:
        _log(f"ingest: loading external transcript {transcript_file}…")
        from bili_fact_checker.ingest import load_transcript_file

        transcript = load_transcript_file(
            settings, url_or_bvid, transcript_file, page=page
        )
    else:
        _log("ingest: fetching transcript…")
        transcript = fetch_transcript(
            settings, url_or_bvid, lang=lang, asr=asr, page=page
        )
    _log(f"ingest: {transcript.bvid} · {transcript.title} · {transcript.source} · {len(transcript.text)} chars")

    summary_text: str | None = None
    raw_claims: list[dict[str, Any]] = []

    if "summary" in tasks:
        _log("analyze: summarizing…")
        summary_text = summarize(settings, transcript)

    need_claims = "verify" in tasks or "claims" in tasks
    if need_claims:
        _log("analyze: extracting claims…")
        raw_claims = extract_claims(settings, transcript)
        _log(f"analyze: {len(raw_claims)} claims")

    claims = []
    events: list[AnalysisEvent] = []
    usages = []
    base_provider = build_search_provider(settings)
    budgeted_provider = BudgetedSearchProvider(
        base_provider, settings.max_searches_per_run
    )
    search_provider = CachedSearchProvider(
        budgeted_provider,
        JsonDiskCache(
            settings.cache_dir, "search", settings.search_cache_ttl_seconds
        ),
        endpoint_namespace=settings.effective_search_api_base,
    )
    page_fetcher = CachedPageFetcher(
        JsonDiskCache(
            settings.cache_dir, "pages", settings.page_cache_ttl_seconds
        )
    )
    evidence_service = EvidenceService(
        settings, search_provider, page_fetcher=page_fetcher
    )

    for raw_claim in raw_claims:
        try:
            claim = AtomicClaim.model_validate(raw_claim)
        except Exception as exc:
            events.append(
                AnalysisEvent(
                    stage="claims",
                    level=EventLevel.ERROR,
                    code="claim_validation_failed",
                    message=str(exc),
                    details={"claim": raw_claim.get("id", "unknown")},
                )
            )
            continue
        if "verify" in tasks:
            _log(f"evidence: verifying {claim.id}…")
            result = evidence_service.analyze_claim(claim)
            claims.append(result.analysis)
            events.extend(result.events)
            usages.extend(result.usage)
        else:
            from bili_fact_checker.evidence.core import aggregate_verdict
            from bili_fact_checker.models import ClaimAnalysis

            claims.append(
                ClaimAnalysis(claim=claim, verdict=aggregate_verdict([], [], []))
            )

    report_model = build_report(
        transcript=transcript.to_dict(),
        summary=summary_text,
        claims=claims,
        model=settings.openai_model,
        search_providers=[base_provider.name],
        search_capabilities=[base_provider.capabilities],
        search_usage=usages,
        events=events,
    )
    return report_model.model_dump(mode="json")
