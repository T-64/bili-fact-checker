from __future__ import annotations

from datetime import datetime, timezone

from bili_fact_checker.evidence.core import (
    aggregate_verdict,
    validate_assessments,
    validate_source_urls,
)
from bili_fact_checker.models import (
    EvidenceAssessment,
    EvidenceDocument,
    EvidenceExcerpt,
    EvidenceStance,
    EvidenceStrength,
    SourceQuality,
    Verdict,
)


def document(
    number: int, host: str, quality: SourceQuality = SourceQuality.CREDIBLE
) -> EvidenceDocument:
    return EvidenceDocument(
        id=f"doc_{number:05d}",
        candidate_id=f"candidate_{number:05d}",
        url=f"https://{host}/article",
        canonical_url=f"https://{host}/article",
        title="Evidence",
        retrieved_at=datetime.now(timezone.utc),
        content_sha256="a" * 64,
        char_count=100,
        source_quality=quality,
    )


def excerpt(number: int, document_id: str) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        id=f"excerpt_{number:05d}",
        document_id=document_id,
        text="This is an exact retained evidence passage with enough content.",
        start_char=0,
        end_char=61,
    )


def assessment(number: int, stance: EvidenceStance) -> EvidenceAssessment:
    return EvidenceAssessment(
        excerpt_id=f"excerpt_{number:05d}",
        stance=stance,
        rationale="The passage directly addresses the claim.",
        model="fixture-model",
    )


def test_no_evidence_never_becomes_a_truth_guess():
    verdict = aggregate_verdict([], [], [])
    assert verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.strength == EvidenceStrength.NONE


def test_search_candidate_without_fetched_excerpt_is_not_evidence():
    verdict = aggregate_verdict([], [], [document(1, "source.example")])
    assert verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.strength == EvidenceStrength.NONE


def test_unknown_excerpt_reference_is_rejected():
    valid, rejected = validate_assessments(
        [assessment(99999, EvidenceStance.SUPPORTS)],
        [excerpt(1, "doc_00001")],
    )
    assert valid == []
    assert rejected == ["excerpt_99999"]


def test_model_cannot_mint_source_urls():
    valid, rejected = validate_source_urls(
        ["https://real.example/article", "https://invented.example/source"],
        [document(1, "real.example")],
    )
    assert valid == ["https://real.example/article"]
    assert rejected == ["https://invented.example/source"]


def test_one_secondary_source_is_visible_but_insufficient():
    docs = [document(1, "one.example")]
    excerpts = [excerpt(1, docs[0].id)]
    verdict = aggregate_verdict(
        [assessment(1, EvidenceStance.SUPPORTS)], excerpts, docs
    )
    assert verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.strength == EvidenceStrength.MEDIUM


def test_two_independent_credible_sources_can_support():
    docs = [document(1, "one.example"), document(2, "two.example")]
    excerpts = [excerpt(1, docs[0].id), excerpt(2, docs[1].id)]
    verdict = aggregate_verdict(
        [
            assessment(1, EvidenceStance.SUPPORTS),
            assessment(2, EvidenceStance.SUPPORTS),
        ],
        excerpts,
        docs,
    )
    assert verdict.verdict == Verdict.SUPPORTED
    assert verdict.strength == EvidenceStrength.HIGH


def test_duplicate_domain_does_not_fake_independence():
    docs = [document(1, "same.example"), document(2, "same.example")]
    excerpts = [excerpt(1, docs[0].id), excerpt(2, docs[1].id)]
    verdict = aggregate_verdict(
        [
            assessment(1, EvidenceStance.SUPPORTS),
            assessment(2, EvidenceStance.SUPPORTS),
        ],
        excerpts,
        docs,
    )
    assert verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.strength == EvidenceStrength.MEDIUM


def test_subdomains_of_same_organization_do_not_fake_independence():
    docs = [
        document(1, "news.example.com"),
        document(2, "research.example.com"),
    ]
    excerpts = [excerpt(1, docs[0].id), excerpt(2, docs[1].id)]
    verdict = aggregate_verdict(
        [
            assessment(1, EvidenceStance.SUPPORTS),
            assessment(2, EvidenceStance.SUPPORTS),
        ],
        excerpts,
        docs,
    )
    assert verdict.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.strength == EvidenceStrength.MEDIUM


def test_conflicting_high_strength_evidence_is_disputed():
    docs = [
        document(1, "support.example", SourceQuality.PRIMARY),
        document(2, "refute.example", SourceQuality.PRIMARY),
    ]
    excerpts = [excerpt(1, docs[0].id), excerpt(2, docs[1].id)]
    verdict = aggregate_verdict(
        [
            assessment(1, EvidenceStance.SUPPORTS),
            assessment(2, EvidenceStance.REFUTES),
        ],
        excerpts,
        docs,
    )
    assert verdict.verdict == Verdict.DISPUTED
    assert verdict.needs_human_review is True
