"""Safe public-page retrieval and exact evidence excerpt extraction."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

try:
    from trafilatura import extract as _trafilatura_extract
except ImportError:  # pragma: no cover - exercised only in minimal source trees
    _trafilatura_extract = None

from bili_fact_checker.config import UA
from bili_fact_checker.models import (
    EvidenceDocument,
    EvidenceExcerpt,
    SearchCandidate,
    SourceQuality,
    utc_now,
)
from bili_fact_checker.evidence.rank import (
    EvidenceReranker,
    LexicalEvidenceReranker,
    split_passages,
)


class UnsafeUrlError(ValueError):
    pass


class PageFetchError(RuntimeError):
    pass


Resolver = Callable[..., list[tuple]]


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_public_url(url: str, resolver: Resolver = socket.getaddrinfo) -> str:
    """Reject unsafe evidence destinations before making a request."""

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("evidence URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeUrlError("evidence URL has an invalid authority")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeUrlError("evidence URL has an invalid port") from exc
    if port not in {80, 443}:
        raise UnsafeUrlError("evidence URL uses a disallowed port")

    host = parsed.hostname
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            records = resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UnsafeUrlError(f"cannot resolve evidence host: {host}") from exc
        addresses = list({record[4][0] for record in records})
    if not addresses or any(not _is_public_address(value) for value in addresses):
        raise UnsafeUrlError("evidence URL resolves to a non-public address")
    return url


class _ReadableHTMLParser(HTMLParser):
    SKIP = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "nav",
        "footer",
        "form",
        "button",
    }
    BLOCK = {
        "article",
        "main",
        "section",
        "div",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "blockquote",
        "br",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._parts: list[str] = []
        self.title = ""
        self.canonical_url = ""
        self.author = ""
        self.publisher = ""
        self.published_at = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value or "" for key, value in attrs}
        if tag in self.SKIP:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key in {"og:title", "twitter:title"} and content:
                self.title = self.title or content
            elif key in {"author", "article:author"} and content:
                self.author = content
            elif key in {"og:site_name", "application-name"} and content:
                self.publisher = content
            elif key in {"article:published_time", "date", "datepublished"}:
                self.published_at = content
        if tag == "link" and "canonical" in values.get("rel", "").lower():
            self.canonical_url = values.get("href", "").strip()
        if tag in self.BLOCK and not self._skip_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK and not self._skip_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        if self._in_title and not self.title:
            self.title = clean
        self._parts.append(clean + " ")

    def readable_text(self) -> str:
        lines: list[str] = []
        previous = ""
        for raw in "".join(self._parts).splitlines():
            line = " ".join(raw.split()).strip()
            if len(line) < 20 or line == previous:
                continue
            lines.append(line)
            previous = line
        return "\n".join(lines)


@dataclass(frozen=True)
class FetchedPage:
    document: EvidenceDocument
    text: str


@dataclass(frozen=True)
class ExtractedArticle:
    text: str
    title: str = ""
    canonical_url: str = ""
    publisher: str = ""
    author: str = ""
    published_at: str = ""


_CREDIBLE_DOMAINS = {
    "apnews.com",
    "bbc.com",
    "caixin.com",
    "reuters.com",
    "thepaper.cn",
    "who.int",
    "wikipedia.org",
    "xinhuanet.com",
}


def classify_source_quality(url: str) -> SourceQuality:
    """Transparent, conservative domain signal; not a truth score."""

    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host.endswith((".gov", ".gov.cn", ".edu", ".edu.cn")):
        return SourceQuality.PRIMARY
    if any(host == domain or host.endswith("." + domain) for domain in _CREDIBLE_DOMAINS):
        return SourceQuality.CREDIBLE
    return SourceQuality.UNKNOWN


def _decode_response(response: httpx.Response, body: bytes) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _fallback_extract_html(html: str) -> ExtractedArticle:
    parser = _ReadableHTMLParser()
    parser.feed(html)
    return ExtractedArticle(
        text=parser.readable_text(),
        title=parser.title,
        canonical_url=parser.canonical_url,
        publisher=parser.publisher,
        author=parser.author,
        published_at=parser.published_at,
    )


def extract_html_article(html: str, *, url: str) -> ExtractedArticle:
    """Extract auditable main text with Trafilatura and a minimal fallback."""

    if _trafilatura_extract is not None:
        try:
            raw = _trafilatura_extract(
                html,
                url=url,
                output_format="json",
                with_metadata=True,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            data = json.loads(raw) if raw else {}
            text = str(data.get("text") or "").strip()
            if text:
                return ExtractedArticle(
                    text=text,
                    title=str(data.get("title") or "").strip(),
                    canonical_url=str(data.get("url") or "").strip(),
                    publisher=str(
                        data.get("sitename") or data.get("hostname") or ""
                    ).strip(),
                    author=str(data.get("author") or "").strip(),
                    published_at=str(data.get("date") or "").strip(),
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return _fallback_extract_html(html)


def fetch_candidate(
    candidate: SearchCandidate,
    *,
    document_id: str,
    proxy: str = "",
    timeout: float = 20,
    max_bytes: int = 2_000_000,
    max_redirects: int = 3,
    client: httpx.Client | None = None,
) -> FetchedPage:
    """Fetch one search candidate while retaining exact provenance."""

    owned_client = client is None
    if client is None:
        client = httpx.Client(
            proxy=proxy or None,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": UA, "Accept": "text/html,text/plain;q=0.8"},
        )

    current = str(candidate.url)
    try:
        for _ in range(max_redirects + 1):
            validate_public_url(current)
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise PageFetchError("redirect response has no location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if not any(value in content_type for value in ("text/html", "text/plain")):
                    raise PageFetchError(f"unsupported content type: {content_type or 'unknown'}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise PageFetchError("page exceeds configured byte limit")
                    chunks.append(chunk)
                body = b"".join(chunks)
                final_url = str(response.url)
                break
        else:
            raise PageFetchError("too many redirects")
    except (httpx.HTTPError, UnsafeUrlError) as exc:
        raise PageFetchError(str(exc)) from exc
    finally:
        if owned_client:
            client.close()

    decoded = _decode_response(response, body)
    if "text/html" in content_type:
        article = extract_html_article(decoded, url=final_url)
        text = article.text
        canonical_url = (
            urljoin(final_url, article.canonical_url)
            if article.canonical_url
            else final_url
        )
        try:
            validate_public_url(canonical_url)
        except UnsafeUrlError:
            canonical_url = final_url
        title = article.title or candidate.title
        publisher = article.publisher
        author = article.author
        published_at = article.published_at
    else:
        text = "\n".join(
            line for line in (" ".join(raw.split()) for raw in decoded.splitlines()) if len(line) >= 20
        )
        canonical_url = final_url
        title = candidate.title
        publisher = author = published_at = ""

    if len(text) < 80:
        raise PageFetchError("page did not contain enough readable text")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return FetchedPage(
        document=EvidenceDocument(
            id=document_id,
            candidate_id=candidate.id,
            url=final_url,
            canonical_url=canonical_url,
            title=title,
            publisher=publisher,
            author=author,
            published_at=published_at,
            retrieved_at=utc_now(),
            content_sha256=digest,
            char_count=len(text),
            source_quality=classify_source_quality(canonical_url),
        ),
        text=text,
    )


def extract_relevant_excerpts(
    page: FetchedPage,
    *,
    claim_text: str,
    quote: str = "",
    entities: list[str] | None = None,
    first_id: int = 1,
    limit: int = 3,
    reranker: EvidenceReranker | None = None,
) -> list[EvidenceExcerpt]:
    """Select exact retained passages; return none when relevance is absent."""

    query = " ".join(
        value.strip()
        for value in [claim_text, quote, *(entities or [])]
        if value.strip()
    )
    if not query:
        return []
    ranker = reranker or LexicalEvidenceReranker()
    ranked = ranker.rank(query, split_passages(page.text), limit=limit)
    selected: list[EvidenceExcerpt] = []
    for item in ranked:
        passage = item.passage
        text = passage.text[:1600]
        selected.append(
            EvidenceExcerpt(
                id=f"excerpt_{first_id + len(selected):05d}",
                document_id=page.document.id,
                text=text,
                start_char=passage.start_char,
                end_char=passage.start_char + len(text),
            )
        )
    return selected
