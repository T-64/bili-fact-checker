"""Reference validation and deterministic verdict aggregation."""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit

from bili_fact_checker.models import (
    ClaimVerdict,
    EvidenceAssessment,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceStance,
    EvidenceStrength,
    SourceQuality,
    Verdict,
)


def validate_assessments(
    assessments: list[EvidenceAssessment],
    excerpts: list[EvidenceExcerpt],
) -> tuple[list[EvidenceAssessment], list[str]]:
    """Discard references an LLM invented instead of silently trusting them."""

    allowed = {excerpt.id for excerpt in excerpts}
    valid: list[EvidenceAssessment] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for assessment in assessments:
        if assessment.excerpt_id not in allowed:
            rejected.append(assessment.excerpt_id)
            continue
        if assessment.excerpt_id in seen:
            rejected.append(assessment.excerpt_id)
            continue
        seen.add(assessment.excerpt_id)
        valid.append(assessment)
    return valid, rejected


def validate_source_urls(
    selected_urls: list[str], documents: list[EvidenceDocument]
) -> tuple[list[str], list[str]]:
    """Return only URLs that belong to fetched documents."""

    allowed = {
        str(url)
        for document in documents
        for url in (document.url, document.canonical_url)
    }
    valid: list[str] = []
    rejected: list[str] = []
    for url in selected_urls:
        if url in allowed and url not in valid:
            valid.append(url)
        else:
            rejected.append(url)
    return valid, rejected


def _source_key(document: EvidenceDocument) -> str:
    host = (urlsplit(str(document.canonical_url)).hostname or "").lower()
    host = host.removeprefix("www.").rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2 or all(part.isdigit() for part in labels):
        return host
    compound_suffixes = {
        "ac.uk",
        "co.jp",
        "co.uk",
        "com.au",
        "com.cn",
        "edu.cn",
        "gov.cn",
        "net.cn",
        "org.cn",
        "org.uk",
    }
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in compound_suffixes else suffix


def _stance_strength(
    assessments: list[EvidenceAssessment],
    excerpt_map: dict[str, EvidenceExcerpt],
    document_map: dict[str, EvidenceDocument],
    stance: EvidenceStance,
) -> EvidenceStrength:
    matching = [item for item in assessments if item.stance == stance]
    if not matching:
        return EvidenceStrength.NONE

    qualities_by_source: dict[str, set[SourceQuality]] = defaultdict(set)
    for item in matching:
        excerpt = excerpt_map.get(item.excerpt_id)
        document = document_map.get(excerpt.document_id) if excerpt else None
        if not document:
            continue
        qualities_by_source[_source_key(document)].add(document.source_quality)

    if not qualities_by_source:
        return EvidenceStrength.NONE

    qualities = {q for values in qualities_by_source.values() for q in values}
    if SourceQuality.PRIMARY in qualities:
        return EvidenceStrength.HIGH

    credible_sources = sum(
        1
        for values in qualities_by_source.values()
        if SourceQuality.CREDIBLE in values or SourceQuality.PRIMARY in values
    )
    if credible_sources >= 2:
        return EvidenceStrength.HIGH
    if credible_sources == 1:
        return EvidenceStrength.MEDIUM
    return EvidenceStrength.LOW


_STRENGTH_ORDER = {
    EvidenceStrength.NONE: 0,
    EvidenceStrength.LOW: 1,
    EvidenceStrength.MEDIUM: 2,
    EvidenceStrength.HIGH: 3,
}


def aggregate_verdict(
    assessments: list[EvidenceAssessment],
    excerpts: list[EvidenceExcerpt],
    documents: list[EvidenceDocument],
) -> ClaimVerdict:
    """Compute a verdict without asking a model to choose the final label."""

    valid, rejected = validate_assessments(assessments, excerpts)
    excerpt_map = {item.id: item for item in excerpts}
    document_map = {item.id: item for item in documents}

    support_ids = [
        item.excerpt_id for item in valid if item.stance == EvidenceStance.SUPPORTS
    ]
    refute_ids = [
        item.excerpt_id for item in valid if item.stance == EvidenceStance.REFUTES
    ]
    context_ids = [
        item.excerpt_id for item in valid if item.stance == EvidenceStance.CONTEXT
    ]

    support_strength = _stance_strength(
        valid, excerpt_map, document_map, EvidenceStance.SUPPORTS
    )
    refute_strength = _stance_strength(
        valid, excerpt_map, document_map, EvidenceStance.REFUTES
    )
    strongest = max(
        (support_strength, refute_strength), key=lambda item: _STRENGTH_ORDER[item]
    )

    support_passes = support_strength == EvidenceStrength.HIGH
    refute_passes = refute_strength == EvidenceStrength.HIGH
    needs_review = bool(rejected)

    if support_passes and refute_passes:
        return ClaimVerdict(
            verdict=Verdict.DISPUTED,
            strength=EvidenceStrength.HIGH,
            supporting_excerpt_ids=support_ids,
            refuting_excerpt_ids=refute_ids,
            context_excerpt_ids=context_ids,
            reason="存在达到证据门槛的相互冲突材料，需要人工核对语境。",
            needs_human_review=True,
        )
    if support_passes:
        return ClaimVerdict(
            verdict=Verdict.SUPPORTED,
            strength=EvidenceStrength.HIGH,
            supporting_excerpt_ids=support_ids,
            refuting_excerpt_ids=refute_ids,
            context_excerpt_ids=context_ids,
            reason="支持方向的证据达到预设门槛，且没有同等级反向证据。",
            needs_human_review=needs_review,
        )
    if refute_passes:
        return ClaimVerdict(
            verdict=Verdict.REFUTED,
            strength=EvidenceStrength.HIGH,
            supporting_excerpt_ids=support_ids,
            refuting_excerpt_ids=refute_ids,
            context_excerpt_ids=context_ids,
            reason="反驳方向的证据达到预设门槛，且没有同等级支持证据。",
            needs_human_review=needs_review,
        )

    if strongest == EvidenceStrength.NONE:
        reason = "没有取得带精确引文且方向明确的外部证据。"
    else:
        reason = "现有材料未达到自动给出支持或反驳结论的证据门槛。"
    return ClaimVerdict(
        verdict=Verdict.INSUFFICIENT_EVIDENCE,
        strength=strongest,
        supporting_excerpt_ids=support_ids,
        refuting_excerpt_ids=refute_ids,
        context_excerpt_ids=context_ids,
        reason=reason,
        needs_human_review=needs_review or bool(support_ids or refute_ids),
        basis="evidence",
    )
