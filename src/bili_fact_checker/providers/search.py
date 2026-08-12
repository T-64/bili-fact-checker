"""Search-provider contracts and deterministic provider routing.

Search providers discover candidate URLs. Their snippets, generated answers,
and citation annotations are never evidence by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from bili_fact_checker.config import Settings
from bili_fact_checker.httputil import get_json, post_json
from bili_fact_checker.models import (
    SearchCandidate,
    SearchProviderCapabilities,
    SearchUsage,
)


class SearchProviderError(RuntimeError):
    """A configured provider failed or returned an unusable response."""


class SearchUnavailableError(SearchProviderError):
    """No usable provider is configured for open-web discovery."""


@dataclass(frozen=True)
class SearchRequest:
    query_id: str
    text: str
    language: str = "zh"
    limit: int = 5
    first_candidate_number: int = 1
    allowed_domain: str = ""
    recency: str = "noLimit"

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("search query cannot be blank")
        if self.limit < 1 or self.limit > 50:
            raise ValueError("search result limit must be between 1 and 50")
        if self.first_candidate_number < 1:
            raise ValueError("candidate numbering must start at one")


@dataclass(frozen=True)
class SearchBatch:
    provider: str
    candidates: list[SearchCandidate] = field(default_factory=list)
    usage: SearchUsage | None = None
    warnings: list[str] = field(default_factory=list)
    cache_hit: bool = False


class SearchProvider(Protocol):
    name: str
    capabilities: SearchProviderCapabilities

    def search(self, request: SearchRequest) -> SearchBatch: ...


class BudgetedSearchProvider:
    """Enforce a run-wide search-call limit around any provider."""

    def __init__(self, provider: SearchProvider, max_calls: int) -> None:
        if max_calls < 0:
            raise ValueError("search call budget cannot be negative")
        self._provider = provider
        self.max_calls = max_calls
        self.used_calls = 0
        self.name = provider.name
        self.capabilities = provider.capabilities

    def search(self, request: SearchRequest) -> SearchBatch:
        if self.used_calls >= self.max_calls:
            raise SearchUnavailableError(
                f"run-wide search budget exhausted ({self.max_calls} calls)"
            )
        self.used_calls += 1
        return self._provider.search(request)


PostJson = Callable[..., Any]
GetJson = Callable[..., Any]


def _candidate(
    *,
    number: int,
    request: SearchRequest,
    provider: str,
    rank: int,
    title: Any,
    url: Any,
    snippet: Any = "",
    published_at: Any = "",
    raw_reference: Any = "",
) -> SearchCandidate | None:
    """Normalize one untrusted provider result; reject unusable URLs."""

    try:
        return SearchCandidate(
            id=f"candidate_{number:05d}",
            query_id=request.query_id,
            provider=provider,
            rank=rank,
            title=str(title or "").strip(),
            url=str(url or "").strip(),
            snippet=str(snippet or "").strip(),
            published_at=str(published_at or "").strip(),
            raw_reference=str(raw_reference or "").strip(),
        )
    except ValidationError:
        return None


def _endpoint(base: str, suffix: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(suffix) else f"{base}/{suffix.lstrip('/')}"


def _append_unique_candidate(
    candidates: list[SearchCandidate],
    seen_urls: set[str],
    *,
    request: SearchRequest,
    provider: str,
    title: Any,
    url: Any,
    snippet: Any = "",
    published_at: Any = "",
    raw_reference: Any = "",
) -> bool:
    normalized = _candidate(
        number=request.first_candidate_number + len(candidates),
        request=request,
        provider=provider,
        rank=len(candidates) + 1,
        title=title,
        url=url,
        snippet=snippet,
        published_at=published_at,
        raw_reference=raw_reference,
    )
    if normalized is None:
        return False
    key = str(normalized.url)
    if key in seen_urls:
        return True
    seen_urls.add(key)
    candidates.append(normalized)
    return True


class ZaiSearchProvider:
    name = "zai"
    capabilities = SearchProviderCapabilities(
        provider=name,
        native_to_llm=True,
        returns_source_urls=True,
        supports_domain_filter=True,
        supports_recency_filter=True,
        reports_usage=False,
    )

    def __init__(
        self,
        settings: Settings,
        *,
        transport: PostJson = post_json,
        search_engine: str = "search-prime",
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._search_engine = search_engine

    def search(self, request: SearchRequest) -> SearchBatch:
        api_key = self._settings.effective_search_api_key
        if not api_key:
            raise SearchUnavailableError("Z.AI search requires the configured AI API key")

        base = self._settings.effective_search_api_base.rstrip("/")
        payload: dict[str, Any] = {
            "search_engine": self._search_engine,
            "search_query": request.text.strip(),
            "count": request.limit,
            "search_recency_filter": request.recency,
        }
        if request.allowed_domain:
            payload["search_domain_filter"] = request.allowed_domain

        try:
            data = self._transport(
                f"{base}/web_search",
                payload,
                proxy=self._settings.proxy,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"Z.AI search request failed: {exc}") from exc

        if not isinstance(data, dict):
            raise SearchProviderError("Z.AI search returned a non-object response")
        raw_results = data.get("search_result") or []
        if not isinstance(raw_results, list):
            raise SearchProviderError("Z.AI search_result is not a list")

        candidates: list[SearchCandidate] = []
        rejected = 0
        for source_rank, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                rejected += 1
                continue
            normalized = _candidate(
                number=request.first_candidate_number + len(candidates),
                request=request,
                provider=self.name,
                rank=source_rank,
                title=item.get("title"),
                url=item.get("link"),
                snippet=item.get("content"),
                published_at=item.get("publish_date"),
                raw_reference=item.get("refer"),
            )
            if normalized is None:
                rejected += 1
                continue
            candidates.append(normalized)
            if len(candidates) >= request.limit:
                break

        warnings = []
        if rejected:
            warnings.append(f"rejected {rejected} malformed search result(s)")
        usage = SearchUsage(
            provider=self.name,
            request_count=1,
            result_count=len(candidates),
            provider_request_id=str(data.get("request_id") or data.get("id") or ""),
            billable_uses=None,
        )
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=usage,
            warnings=warnings,
        )


class OpenAISearchProvider:
    name = "openai"
    capabilities = SearchProviderCapabilities(
        provider=name,
        native_to_llm=True,
        returns_source_urls=True,
        returns_cited_text=True,
        supports_domain_filter=True,
        reports_usage=True,
    )

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def search(self, request: SearchRequest) -> SearchBatch:
        api_key = self._settings.effective_search_api_key
        if not api_key:
            raise SearchUnavailableError("OpenAI search requires an API key")
        prompt = (
            "Search the public web for source pages that can verify or refute "
            f"this claim. Return grounded results with citations: {request.text}"
        )
        tool: dict[str, Any] = {"type": "web_search"}
        if request.allowed_domain:
            tool["filters"] = {"allowed_domains": [request.allowed_domain]}
        payload = {
            "model": self._settings.openai_model,
            "tools": [tool],
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "input": prompt,
        }
        try:
            data = self._transport(
                _endpoint(self._settings.effective_search_api_base, "responses"),
                payload,
                proxy=self._settings.proxy,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"OpenAI web search request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise SearchProviderError("OpenAI web search returned a non-object response")

        candidates: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        rejected = 0
        search_calls = 0
        for output in data.get("output") or []:
            if not isinstance(output, dict):
                continue
            if output.get("type") == "web_search_call":
                action = output.get("action") or {}
                if isinstance(action, dict) and action.get("type") == "search":
                    search_calls += 1
                sources = action.get("sources") if isinstance(action, dict) else []
                for source in sources or []:
                    if not isinstance(source, dict):
                        rejected += 1
                        continue
                    if not _append_unique_candidate(
                        candidates,
                        seen_urls,
                        request=request,
                        provider=self.name,
                        title=source.get("title"),
                        url=source.get("url"),
                        snippet=source.get("snippet"),
                        published_at=source.get("published_at"),
                        raw_reference=output.get("id"),
                    ):
                        rejected += 1
            if output.get("type") != "message":
                continue
            for content in output.get("content") or []:
                if not isinstance(content, dict):
                    continue
                text = str(content.get("text") or "")
                for annotation in content.get("annotations") or []:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    start = annotation.get("start_index")
                    end = annotation.get("end_index")
                    snippet = ""
                    if isinstance(start, int) and isinstance(end, int):
                        snippet = text[max(start, 0) : max(end, 0)]
                    if not _append_unique_candidate(
                        candidates,
                        seen_urls,
                        request=request,
                        provider=self.name,
                        title=annotation.get("title"),
                        url=annotation.get("url"),
                        snippet=snippet,
                        raw_reference=output.get("id"),
                    ):
                        rejected += 1
                if len(candidates) >= request.limit:
                    break
            if len(candidates) >= request.limit:
                break
        candidates = candidates[: request.limit]
        warnings = [f"rejected {rejected} malformed search result(s)"] if rejected else []
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
                provider_request_id=str(data.get("id") or ""),
                billable_uses=search_calls or None,
            ),
            warnings=warnings,
        )


class GeminiSearchProvider:
    name = "gemini"
    capabilities = SearchProviderCapabilities(
        provider=name,
        native_to_llm=True,
        returns_source_urls=True,
        returns_cited_text=True,
        reports_usage=True,
    )

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def search(self, request: SearchRequest) -> SearchBatch:
        api_key = self._settings.effective_search_api_key
        if not api_key:
            raise SearchUnavailableError("Gemini search requires an API key")
        payload = {
            "model": self._settings.openai_model.removeprefix("models/"),
            "input": (
                "Search for public source pages that can verify or refute this "
                f"claim, and cite every source: {request.text}"
            ),
            "tools": [{"type": "google_search"}],
        }
        try:
            data = self._transport(
                _endpoint(self._settings.effective_search_api_base, "interactions"),
                payload,
                proxy=self._settings.proxy,
                headers={"x-goog-api-key": api_key},
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"Gemini Google Search request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise SearchProviderError("Gemini search returned a non-object response")

        candidates: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        rejected = 0
        search_calls = 0
        for step in data.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "google_search_call":
                search_calls += 1
            if step.get("type") != "model_output":
                continue
            for content in step.get("content") or []:
                if not isinstance(content, dict) or content.get("type") != "text":
                    continue
                text = str(content.get("text") or "")
                for annotation in content.get("annotations") or []:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    start = annotation.get("start_index")
                    end = annotation.get("end_index")
                    snippet = ""
                    if isinstance(start, int) and isinstance(end, int):
                        snippet = text[max(start, 0) : max(end, 0)]
                    if not _append_unique_candidate(
                        candidates,
                        seen_urls,
                        request=request,
                        provider=self.name,
                        title=annotation.get("title"),
                        url=annotation.get("url"),
                        snippet=snippet,
                        raw_reference=step.get("id"),
                    ):
                        rejected += 1
                if len(candidates) >= request.limit:
                    break
        candidates = candidates[: request.limit]
        warnings = [f"rejected {rejected} malformed search result(s)"] if rejected else []
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
                provider_request_id=str(data.get("id") or ""),
                billable_uses=search_calls or None,
            ),
            warnings=warnings,
        )


class AnthropicSearchProvider:
    name = "anthropic"
    capabilities = SearchProviderCapabilities(
        provider=name,
        native_to_llm=True,
        returns_source_urls=True,
        returns_cited_text=True,
        supports_domain_filter=True,
        reports_usage=True,
    )

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def search(self, request: SearchRequest) -> SearchBatch:
        api_key = self._settings.effective_search_api_key
        if not api_key:
            raise SearchUnavailableError("Anthropic search requires an API key")
        tool: dict[str, Any] = {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 1,
        }
        if request.allowed_domain:
            tool["allowed_domains"] = [request.allowed_domain]
        payload = {
            "model": self._settings.openai_model,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Search for public source pages that can verify or refute "
                        f"this claim. Cite the sources: {request.text}"
                    ),
                }
            ],
            "tools": [tool],
        }
        try:
            data = self._transport(
                _endpoint(self._settings.effective_search_api_base, "messages"),
                payload,
                proxy=self._settings.proxy,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"Anthropic web search request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise SearchProviderError("Anthropic search returned a non-object response")

        candidates: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        rejected = 0
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "web_search_tool_result":
                result_content = block.get("content") or []
                if not isinstance(result_content, list):
                    continue
                for result in result_content:
                    if not isinstance(result, dict) or result.get("type") != "web_search_result":
                        continue
                    if not _append_unique_candidate(
                        candidates,
                        seen_urls,
                        request=request,
                        provider=self.name,
                        title=result.get("title"),
                        url=result.get("url"),
                        published_at=result.get("page_age"),
                        raw_reference=block.get("tool_use_id"),
                    ):
                        rejected += 1
            if block.get("type") == "text":
                for citation in block.get("citations") or []:
                    if not isinstance(citation, dict) or citation.get("type") != "web_search_result_location":
                        continue
                    if not _append_unique_candidate(
                        candidates,
                        seen_urls,
                        request=request,
                        provider=self.name,
                        title=citation.get("title"),
                        url=citation.get("url"),
                        snippet=citation.get("cited_text"),
                        raw_reference=citation.get("encrypted_index"),
                    ):
                        rejected += 1
            if len(candidates) >= request.limit:
                break
        candidates = candidates[: request.limit]
        server_usage = (data.get("usage") or {}).get("server_tool_use") or {}
        billable = server_usage.get("web_search_requests")
        warnings = [f"rejected {rejected} malformed search result(s)"] if rejected else []
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
                provider_request_id=str(data.get("id") or ""),
                billable_uses=int(billable) if isinstance(billable, int) else None,
            ),
            warnings=warnings,
        )


class SearxngSearchProvider:
    name = "searxng"
    capabilities = SearchProviderCapabilities(
        provider=name,
        returns_source_urls=True,
    )

    def __init__(self, settings: Settings, *, transport: GetJson = get_json) -> None:
        self._settings = settings
        self._transport = transport

    def search(self, request: SearchRequest) -> SearchBatch:
        if not self._settings.searxng_url:
            raise SearchUnavailableError("SearXNG URL is not configured")
        language = "zh-CN" if request.language == "zh" else request.language
        query = urlencode(
            {"q": request.text.strip(), "format": "json", "language": language}
        )
        try:
            data = self._transport(
                f"{self._settings.searxng_url}/search?{query}",
                proxy=self._settings.proxy,
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"SearXNG search request failed: {exc}") from exc
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise SearchProviderError("SearXNG results are missing or invalid")

        candidates: list[SearchCandidate] = []
        for source_rank, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _candidate(
                number=request.first_candidate_number + len(candidates),
                request=request,
                provider=self.name,
                rank=source_rank,
                title=item.get("title"),
                url=item.get("url"),
                snippet=item.get("content") or item.get("snippet"),
                published_at=item.get("publishedDate"),
            )
            if normalized:
                candidates.append(normalized)
            if len(candidates) >= request.limit:
                break
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
            ),
        )


class TavilySearchProvider:
    name = "tavily"
    capabilities = SearchProviderCapabilities(
        provider=name,
        returns_source_urls=True,
    )

    def __init__(self, settings: Settings, *, transport: PostJson = post_json) -> None:
        self._settings = settings
        self._transport = transport

    def search(self, request: SearchRequest) -> SearchBatch:
        if not self._settings.tavily_api_key:
            raise SearchUnavailableError("Tavily API key is not configured")
        try:
            data = self._transport(
                "https://api.tavily.com/search",
                {
                    "api_key": self._settings.tavily_api_key,
                    "query": request.text.strip(),
                    "search_depth": "basic",
                    "max_results": request.limit,
                },
                proxy=self._settings.proxy,
                timeout=self._settings.search_timeout_seconds,
            )
        except Exception as exc:
            raise SearchProviderError(f"Tavily search request failed: {exc}") from exc
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise SearchProviderError("Tavily results are missing or invalid")

        candidates: list[SearchCandidate] = []
        for source_rank, item in enumerate(raw_results, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _candidate(
                number=request.first_candidate_number + len(candidates),
                request=request,
                provider=self.name,
                rank=source_rank,
                title=item.get("title"),
                url=item.get("url"),
                snippet=item.get("content"),
                published_at=item.get("published_date"),
            )
            if normalized:
                candidates.append(normalized)
            if len(candidates) >= request.limit:
                break
        return SearchBatch(
            provider=self.name,
            candidates=candidates,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=len(candidates),
            ),
        )


class UnavailableSearchProvider:
    name = "none"
    capabilities = SearchProviderCapabilities(
        provider=name,
        returns_source_urls=False,
    )

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def search(self, request: SearchRequest) -> SearchBatch:
        raise SearchUnavailableError(self.reason)


def detect_native_search_provider(settings: Settings) -> str | None:
    """Identify only providers whose native protocol is known and documented."""

    host = (urlsplit(settings.effective_search_api_base).hostname or "").lower()
    if host in {"api.z.ai", "open.bigmodel.cn"}:
        return "zai"
    if host == "api.openai.com":
        return "openai"
    if host in {"generativelanguage.googleapis.com", "ai.google.dev"}:
        return "gemini"
    if host == "api.anthropic.com":
        return "anthropic"
    return None


def build_search_provider(
    settings: Settings,
    *,
    post_transport: PostJson = post_json,
    get_transport: GetJson = get_json,
) -> SearchProvider:
    """Resolve configuration without making a live or billable capability probe."""

    requested = settings.search_provider.strip().lower() or "auto"
    aliases = {"z.ai": "zai", "zhipu": "zai", "glm": "zai"}
    requested = aliases.get(requested, requested)
    native = detect_native_search_provider(settings)

    if requested in {"none", "offline"}:
        return UnavailableSearchProvider(
            "open-web search is disabled; only offline lookup is available"
        )
    if requested == "native":
        native_factories = {
            "zai": lambda: ZaiSearchProvider(settings, transport=post_transport),
            "openai": lambda: OpenAISearchProvider(settings, transport=post_transport),
            "gemini": lambda: GeminiSearchProvider(settings, transport=post_transport),
            "anthropic": lambda: AnthropicSearchProvider(settings, transport=post_transport),
        }
        if native in native_factories:
            return native_factories[native]()
        return UnavailableSearchProvider(
            "the configured OpenAI-compatible endpoint has no known native search protocol"
        )
    if requested == "zai":
        return ZaiSearchProvider(settings, transport=post_transport)
    if requested == "openai":
        return OpenAISearchProvider(settings, transport=post_transport)
    if requested == "gemini":
        return GeminiSearchProvider(settings, transport=post_transport)
    if requested in {"anthropic", "claude"}:
        return AnthropicSearchProvider(settings, transport=post_transport)
    if requested == "searxng":
        return SearxngSearchProvider(settings, transport=get_transport)
    if requested == "tavily":
        return TavilySearchProvider(settings, transport=post_transport)
    if requested not in {"auto", "external"}:
        return UnavailableSearchProvider(f"unsupported search provider: {requested}")

    if requested == "auto" and native == "zai":
        return ZaiSearchProvider(settings, transport=post_transport)
    if requested == "auto" and native == "openai":
        return OpenAISearchProvider(settings, transport=post_transport)
    if requested == "auto" and native == "gemini":
        return GeminiSearchProvider(settings, transport=post_transport)
    if requested == "auto" and native == "anthropic":
        return AnthropicSearchProvider(settings, transport=post_transport)
    if settings.searxng_url:
        return SearxngSearchProvider(settings, transport=get_transport)
    if settings.tavily_api_key:
        return TavilySearchProvider(settings, transport=post_transport)
    return UnavailableSearchProvider(
        "no supported native or explicitly configured external search provider"
    )
