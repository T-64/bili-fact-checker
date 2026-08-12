"""Small persistent caches for paid search calls and fetched public pages."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from bili_fact_checker.evidence.fetch import FetchedPage, fetch_candidate
from bili_fact_checker.models import EvidenceDocument, SearchCandidate, SearchUsage
from bili_fact_checker.providers.search import (
    SearchBatch,
    SearchProvider,
    SearchRequest,
)


class JsonDiskCache:
    """One JSON file per key, with atomic replacement and a fixed TTL."""

    def __init__(self, root: Path, namespace: str, ttl_seconds: int) -> None:
        self.root = root / namespace
        self.ttl_seconds = max(0, ttl_seconds)

    def _path(self, key: dict[str, Any]) -> Path:
        encoded = json.dumps(
            key, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self.root / f"{hashlib.sha256(encoded).hexdigest()}.json"

    def get(self, key: dict[str, Any]) -> dict[str, Any] | None:
        if self.ttl_seconds == 0:
            return None
        path = self._path(key)
        try:
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: dict[str, Any], value: dict[str, Any]) -> None:
        if self.ttl_seconds == 0:
            return
        temporary: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self._path(key)
            temporary = path.with_suffix(
                f".tmp-{os.getpid()}-{uuid.uuid4().hex}"
            )
            payload = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            )
            temporary.write_text(payload, encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


class CachedSearchProvider:
    """Cache normalized candidates without caching secrets or generated answers."""

    def __init__(
        self,
        provider: SearchProvider,
        cache: JsonDiskCache,
        *,
        endpoint_namespace: str = "",
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._endpoint_namespace = endpoint_namespace
        self.name = provider.name
        self.capabilities = provider.capabilities

    def _key(self, request: SearchRequest) -> dict[str, Any]:
        return {
            "schema": 1,
            "provider": self.name,
            "endpoint": self._endpoint_namespace,
            "query": " ".join(request.text.casefold().split()),
            "language": request.language,
            "limit": request.limit,
            "allowed_domain": request.allowed_domain.casefold(),
            "recency": request.recency,
        }

    def search(self, request: SearchRequest) -> SearchBatch:
        key = self._key(request)
        cached = self._cache.get(key)
        if cached is not None:
            try:
                stored = [
                    SearchCandidate.model_validate(item)
                    for item in cached.get("candidates") or []
                ]
            except Exception:
                pass
            else:
                candidates = [
                    item.model_copy(
                        update={
                            "id": (
                                f"candidate_"
                                f"{request.first_candidate_number + index:05d}"
                            ),
                            "query_id": request.query_id,
                            "rank": index + 1,
                        }
                    )
                    for index, item in enumerate(stored[: request.limit])
                ]
                return SearchBatch(
                    provider=self.name,
                    candidates=candidates,
                    usage=SearchUsage(
                        provider=self.name,
                        request_count=0,
                        result_count=len(candidates),
                        provider_request_id="cache",
                        billable_uses=0,
                    ),
                    cache_hit=True,
                )

        batch = self._provider.search(request)
        self._cache.put(
            key,
            {
                "candidates": [
                    item.model_dump(mode="json") for item in batch.candidates
                ]
            },
        )
        return batch


PageFetcher = Callable[..., FetchedPage]


class CachedPageFetcher:
    """Cache extracted article text while rebinding run-local evidence IDs."""

    def __init__(
        self,
        cache: JsonDiskCache,
        *,
        fetcher: PageFetcher = fetch_candidate,
    ) -> None:
        self._cache = cache
        self._fetcher = fetcher

    def __call__(
        self,
        candidate: SearchCandidate,
        *,
        document_id: str,
        **kwargs: Any,
    ) -> FetchedPage:
        key = {"schema": 1, "url": str(candidate.url)}
        cached = self._cache.get(key)
        if cached is not None:
            try:
                document = EvidenceDocument.model_validate(cached.get("document"))
                text = str(cached.get("text") or "")
                if not text:
                    raise ValueError("cached page has no text")
            except Exception:
                pass
            else:
                return FetchedPage(
                    document=document.model_copy(
                        update={"id": document_id, "candidate_id": candidate.id}
                    ),
                    text=text,
                    cache_hit=True,
                )

        page = self._fetcher(candidate, document_id=document_id, **kwargs)
        self._cache.put(
            key,
            {
                "document": page.document.model_dump(mode="json"),
                "text": page.text,
            },
        )
        return page
