"""Versioned domain models for auditable fact-check reports.

LLM responses are untrusted input. These models validate shape; reference
integrity is enforced separately by the pipeline before a report is emitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    DISPUTED = "disputed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceStrength(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXT = "context"
    IRRELEVANT = "irrelevant"
    UNCLEAR = "unclear"


class SourceQuality(StrEnum):
    PRIMARY = "primary"
    CREDIBLE = "credible"
    UNKNOWN = "unknown"
    LOW = "low"


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TranscriptSegment(StrictModel):
    id: str = Field(pattern=r"^seg_[0-9]{5}$")
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("segment text cannot be blank")
        return value


class VideoInfo(StrictModel):
    bvid: str = Field(pattern=r"^BV[0-9A-Za-z]+$")
    title: str
    url: HttpUrl
    aid: str = ""
    cid: str = ""
    page: int = Field(default=1, ge=1)
    part_title: str = ""


class TranscriptInfo(StrictModel):
    source: Literal["cc", "asr", "file"]
    language: str
    segments: list[TranscriptSegment]
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AtomicClaim(StrictModel):
    id: str = Field(pattern=r"^claim_[0-9]{4}$")
    claim_zh: str = Field(min_length=4)
    claim_en: str = ""
    claim_type: str
    quote: str = Field(min_length=1)
    anchor_segment_ids: list[str] = Field(min_length=1)
    timestamp_sec: int = Field(ge=0)
    checkability_reason: str = ""
    entities: list[str] = Field(default_factory=list)
    temporal_context: str = ""


class SearchQuery(StrictModel):
    id: str = Field(pattern=r"^query_[0-9]{4}_[0-9]{2}$")
    language: Literal["zh", "en"]
    text: str = Field(min_length=2, max_length=300)


class SearchCandidate(StrictModel):
    id: str = Field(pattern=r"^candidate_[0-9]{5}$")
    query_id: str
    provider: str
    rank: int = Field(ge=1)
    title: str
    url: HttpUrl
    snippet: str = ""
    published_at: str = ""
    retrieved_at: datetime = Field(default_factory=utc_now)
    raw_reference: str = ""


class SearchProviderCapabilities(StrictModel):
    provider: str
    native_to_llm: bool = False
    returns_source_urls: bool = True
    returns_cited_text: bool = False
    supports_domain_filter: bool = False
    supports_recency_filter: bool = False
    reports_usage: bool = False


class SearchUsage(StrictModel):
    provider: str
    request_count: int = Field(default=0, ge=0)
    result_count: int = Field(default=0, ge=0)
    provider_request_id: str = ""
    billable_uses: int | None = Field(default=None, ge=0)


class EvidenceDocument(StrictModel):
    id: str = Field(pattern=r"^doc_[0-9]{5}$")
    candidate_id: str
    url: HttpUrl
    canonical_url: HttpUrl
    title: str
    publisher: str = ""
    author: str = ""
    published_at: str = ""
    retrieved_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    char_count: int = Field(ge=1)
    source_quality: SourceQuality = SourceQuality.UNKNOWN


class EvidenceExcerpt(StrictModel):
    id: str = Field(pattern=r"^excerpt_[0-9]{5}$")
    document_id: str
    text: str = Field(min_length=20)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)


class EvidenceAssessment(StrictModel):
    excerpt_id: str
    stance: EvidenceStance
    rationale: str = Field(min_length=1, max_length=500)
    model: str


class ClaimVerdict(StrictModel):
    verdict: Verdict
    strength: EvidenceStrength
    supporting_excerpt_ids: list[str] = Field(default_factory=list)
    refuting_excerpt_ids: list[str] = Field(default_factory=list)
    context_excerpt_ids: list[str] = Field(default_factory=list)
    reason: str
    needs_human_review: bool = False


class ClaimAnalysis(StrictModel):
    claim: AtomicClaim
    queries: list[SearchQuery] = Field(default_factory=list)
    candidates: list[SearchCandidate] = Field(default_factory=list)
    documents: list[EvidenceDocument] = Field(default_factory=list)
    excerpts: list[EvidenceExcerpt] = Field(default_factory=list)
    assessments: list[EvidenceAssessment] = Field(default_factory=list)
    verdict: ClaimVerdict


class AnalysisEvent(StrictModel):
    at: datetime = Field(default_factory=utc_now)
    stage: str
    level: EventLevel = EventLevel.INFO
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RunInfo(StrictModel):
    software_version: str
    model: str
    search_providers: list[str]
    search_capabilities: list[SearchProviderCapabilities] = Field(
        default_factory=list
    )
    search_usage: list[SearchUsage] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)


class ReportStats(StrictModel):
    claim_count: int = 0
    supported: int = 0
    refuted: int = 0
    disputed: int = 0
    insufficient_evidence: int = 0


class AnalysisReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    disclaimer: str = (
        "这是基于当前检索证据生成的辅助审阅报告，不是权威裁决。"
        "请核对视频原话、证据引文和来源页面。"
    )
    run: RunInfo
    video: VideoInfo
    transcript: TranscriptInfo
    summary: str = ""
    claims: list[ClaimAnalysis]
    events: list[AnalysisEvent] = Field(default_factory=list)
    stats: ReportStats
