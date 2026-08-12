from __future__ import annotations

import socket

import pytest

from bili_fact_checker.evidence.fetch import (
    FetchedPage,
    UnsafeUrlError,
    extract_relevant_excerpts,
    validate_public_url,
)
from bili_fact_checker.models import EvidenceDocument, SourceQuality, utc_now


def resolver_for(address: str):
    def resolve(_host, port, *, type):
        assert type == socket.SOCK_STREAM
        return [(socket.AF_INET, type, 6, "", (address, port))]

    return resolve


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://user:pass@example.com/",
        "https://example.com:8443/private",
    ],
)
def test_unsafe_evidence_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_url(url, resolver=resolver_for("93.184.216.34"))


def test_hostname_resolving_to_private_address_is_rejected():
    with pytest.raises(UnsafeUrlError):
        validate_public_url("https://example.com/a", resolver=resolver_for("10.0.0.2"))


def test_public_destination_is_accepted():
    assert validate_public_url(
        "https://example.com/a", resolver=resolver_for("93.184.216.34")
    ) == "https://example.com/a"


def test_excerpt_is_exact_and_requires_term_overlap():
    text = (
        "这是一个与问题无关但足够长的介绍段落，用来确认程序不会把任意正文当作证据。\n"
        "世界卫生组织在报告中说明，该指标在二〇二四年下降了百分之十，统计范围仅覆盖成员国。\n"
    )
    doc = EvidenceDocument(
        id="doc_00001",
        candidate_id="candidate_00001",
        url="https://who.int/report",
        canonical_url="https://who.int/report",
        title="报告",
        retrieved_at=utc_now(),
        content_sha256="a" * 64,
        char_count=len(text),
        source_quality=SourceQuality.CREDIBLE,
    )
    page = FetchedPage(document=doc, text=text)
    excerpts = extract_relevant_excerpts(
        page,
        claim_text="世界卫生组织称该指标在2024年下降10%",
        entities=["世界卫生组织"],
    )
    assert len(excerpts) == 1
    assert excerpts[0].text in text
    assert "世界卫生组织" in excerpts[0].text
    assert extract_relevant_excerpts(page, claim_text="完全不存在的火星人口") == []
