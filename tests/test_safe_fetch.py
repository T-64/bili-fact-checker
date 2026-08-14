from __future__ import annotations

import socket
import json

import httpx
import pytest

from bili_fact_checker.evidence.fetch import (
    FetchedPage,
    UnsafeUrlError,
    extract_html_article,
    extract_relevant_excerpts,
    fetch_candidate,
    validate_public_url,
)
from bili_fact_checker.models import (
    EvidenceDocument,
    SearchCandidate,
    SourceQuality,
    utc_now,
)


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


def test_trafilatura_metadata_and_main_text_are_preferred(monkeypatch):
    def fake_extract(html, **kwargs):
        assert "navigation noise" in html
        assert kwargs["with_metadata"] is True
        assert kwargs["favor_precision"] is True
        return json.dumps(
            {
                "text": "这是 Trafilatura 提取出的完整正文，长度足够用于后续证据定位。",
                "title": "报告标题",
                "url": "https://example.com/canonical",
                "sitename": "示例机构",
                "author": "作者",
                "date": "2026-08-01",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "bili_fact_checker.evidence.fetch._trafilatura_extract", fake_extract
    )
    article = extract_html_article(
        "<html><nav>navigation noise</nav><article>body</article></html>",
        url="https://example.com/input",
    )
    assert article.text.startswith("这是 Trafilatura")
    assert article.title == "报告标题"
    assert article.canonical_url == "https://example.com/canonical"
    assert article.publisher == "示例机构"


def test_fetch_candidate_keeps_fetched_page_provenance(monkeypatch):
    monkeypatch.setattr(
        "bili_fact_checker.evidence.fetch._trafilatura_extract", None
    )
    html = """<html><head><title>完整报告</title>
    <link rel="canonical" href="/canonical"></head><body>
    <nav>这里是不会进入正文的导航信息，而且故意写得比较长。</nav>
    <article><p>世界卫生组织在完整报告中说明，该指标在2024年下降10%，
    统计范围包含全部参与成员国，并在附件中公开了计算方法与数据表；
    报告还逐项解释了样本选择、缺失值处理、年度比较基线和修订记录，
    以便研究者能够复核这项统计结果。</p></article>
    </body></html>"""

    def handler(request):
        assert str(request.url) == "https://93.184.216.34/report"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html.encode(),
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidate = SearchCandidate(
        id="candidate_00001",
        query_id="query_0001_01",
        provider="fixture",
        rank=1,
        title="搜索标题",
        url="https://93.184.216.34/report",
        snippet="搜索摘要不算证据",
    )
    page = fetch_candidate(candidate, document_id="doc_00001", client=client)
    client.close()

    assert str(page.document.canonical_url) == "https://93.184.216.34/canonical"
    assert page.document.title == "完整报告"
    assert "世界卫生组织" in page.text
    assert "导航信息" not in page.text
    assert page.document.content_sha256 != "a" * 64


def test_redirect_to_private_network_is_blocked_before_second_request():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidate = SearchCandidate(
        id="candidate_00001",
        query_id="query_0001_01",
        provider="fixture",
        rank=1,
        title="redirect",
        url="https://93.184.216.34/report",
    )
    with pytest.raises(UnsafeUrlError, match="non-public"):
        fetch_candidate(candidate, document_id="doc_00001", client=client)
    client.close()
    assert calls == 1
