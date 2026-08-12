"""Environment-driven settings. Never hardcode secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _load_glm_from_hermes() -> str:
    hermes = Path.home() / ".hermes" / ".env"
    if not hermes.exists():
        return ""
    for line in hermes.read_text(encoding="utf-8").splitlines():
        if line.startswith("GLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _load_sessdata() -> str:
    raw = _env("BILI_SESSDATA")
    if raw:
        return raw
    path = Path.home() / ".config" / "bili" / "SESSDATA"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


@dataclass(frozen=True)
class Settings:
    sessdata: str
    proxy: str
    openai_api_key: str
    openai_api_base: str
    openai_model: str
    llm_provider: str
    search_provider: str
    search_api_key: str
    search_api_base: str
    google_factcheck_api_key: str
    searxng_url: str
    tavily_api_key: str
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    cookie_file: Path
    max_claims: int
    max_searches_per_claim: int
    max_searches_per_run: int
    search_results_per_query: int
    search_timeout_seconds: float
    fetch_max_bytes: int
    fetch_timeout_seconds: float
    evidence_reranker: str
    evidence_reranker_model: str
    cache_dir: Path
    search_cache_ttl_seconds: int
    page_cache_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        # A clean installation must not assume the author's local proxy.
        proxy = _env("HTTPS_PROXY") or _env("HTTP_PROXY")
        api_key = _env("OPENAI_API_KEY") or _env("GLM_API_KEY") or _load_glm_from_hermes()
        whisper = _env("WHISPER_MODEL") or str(Path.home() / "whisper-model")
        return cls(
            sessdata=_load_sessdata(),
            proxy=proxy,
            openai_api_key=api_key,
            openai_api_base=_env("OPENAI_API_BASE", "https://api.z.ai/api/paas/v4").rstrip("/"),
            openai_model=_env("OPENAI_MODEL", "glm-4-flash"),
            llm_provider=(
                _env("BFC_LLM_PROVIDER") or _env("LLM_PROVIDER", "auto")
            ).lower(),
            search_provider=(
                _env("BFC_SEARCH_PROVIDER") or _env("SEARCH_PROVIDER", "auto")
            ).lower(),
            search_api_key=_env("BFC_SEARCH_API_KEY"),
            search_api_base=_env("BFC_SEARCH_API_BASE").rstrip("/"),
            google_factcheck_api_key=_env("GOOGLE_FACTCHECK_API_KEY"),
            searxng_url=_env("SEARXNG_URL").rstrip("/"),
            tavily_api_key=_env("TAVILY_API_KEY"),
            whisper_model=os.path.expanduser(whisper),
            whisper_device=_env("WHISPER_DEVICE", "cuda"),
            whisper_compute_type=_env("WHISPER_COMPUTE_TYPE", "float16"),
            cookie_file=Path.home() / ".config" / "bili" / "cookies.txt",
            max_claims=int(_env("BFC_MAX_CLAIMS", "15")),
            max_searches_per_claim=int(
                _env("BFC_MAX_SEARCHES_PER_CLAIM", "3")
            ),
            max_searches_per_run=int(_env("BFC_MAX_TOTAL_SEARCHES", "30")),
            search_results_per_query=int(_env("BFC_SEARCH_RESULTS", "5")),
            search_timeout_seconds=float(_env("BFC_SEARCH_TIMEOUT", "30")),
            fetch_max_bytes=int(_env("BFC_FETCH_MAX_BYTES", "2000000")),
            fetch_timeout_seconds=float(_env("BFC_FETCH_TIMEOUT", "20")),
            evidence_reranker=_env("BFC_EVIDENCE_RERANKER", "lexical").lower(),
            evidence_reranker_model=_env(
                "BFC_EVIDENCE_RERANKER_MODEL",
                "BAAI/bge-reranker-v2-m3",
            ),
            cache_dir=Path(
                os.path.expanduser(
                    _env(
                        "BFC_CACHE_DIR",
                        str(Path.home() / ".cache" / "bili-fact-checker"),
                    )
                )
            ),
            search_cache_ttl_seconds=int(
                _env("BFC_SEARCH_CACHE_TTL", "86400")
            ),
            page_cache_ttl_seconds=int(
                _env("BFC_PAGE_CACHE_TTL", "604800")
            ),
        )

    @property
    def effective_search_api_key(self) -> str:
        """Reuse the LLM account unless a distinct search account is supplied."""

        return self.search_api_key or self.openai_api_key

    @property
    def effective_search_api_base(self) -> str:
        """Reuse the LLM endpoint unless search is explicitly separated."""

        return self.search_api_base or self.openai_api_base


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
