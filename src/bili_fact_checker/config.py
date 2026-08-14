"""Environment-driven settings. Never hardcode secrets."""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit


MIN_PUBLIC_API_TOKEN_LENGTH = 32


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def is_loopback_host(host: str) -> bool:
    """Return whether a server bind target is unambiguously loopback-only."""

    normalized = host.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # Hostnames other than localhost can resolve differently over time. Treat
        # them as non-loopback so an ambiguous bind never disables authentication.
        return False


def validate_api_bind(host: str, api_token: str) -> None:
    """Reject unauthenticated or weakly authenticated non-loopback binds."""

    if is_loopback_host(host):
        return
    if len(api_token.strip()) < MIN_PUBLIC_API_TOKEN_LENGTH:
        raise ValueError(
            f"refusing non-loopback API bind to {host!r}: set BFC_API_TOKEN to "
            "a token of at least "
            f"{MIN_PUBLIC_API_TOKEN_LENGTH} characters "
            "(generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(32))\")"
        )


def _load_glm_from_hermes() -> str:
    hermes = Path.home() / ".hermes" / ".env"
    if not hermes.exists():
        return ""
    for line in hermes.read_text(encoding="utf-8").splitlines():
        if line.startswith("GLM_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def user_config_path() -> Path:
    override = _env("BFC_CONFIG_PATH")
    if override:
        return Path(os.path.expanduser(override))
    return Path.home() / ".config" / "bili-fact-checker" / "config.json"


_USER_CONFIG_KEYS = (
    "openai_api_base",
    "openai_api_key",
    "openai_model",
    "sessdata",
)


_last_config_error = ""


def user_config_error() -> str:
    return _last_config_error


def load_user_config(path: Path | None = None) -> dict[str, str]:
    global _last_config_error
    _last_config_error = ""
    target = path or user_config_path()
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        _last_config_error = f"配置文件损坏或无法读取：{target}"
        return {}
    if not isinstance(raw, dict):
        _last_config_error = f"配置文件格式无效：{target}"
        return {}
    values: dict[str, str] = {}
    for key in _USER_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            values[key] = value.strip()
    return values


def save_user_config(values: dict[str, str], path: Path | None = None) -> Path:
    target = path or user_config_path()
    payload = {
        key: values[key].strip()
        for key in _USER_CONFIG_KEYS
        if values.get(key, "").strip()
    }
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(
        prefix=".config.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def clear_user_config(path: Path | None = None) -> bool:
    target = path or user_config_path()
    if not target.is_file():
        return False
    target.unlink()
    return True


def validate_setup_values(*, api_base: str, api_key: str, model: str) -> None:
    if not api_key.strip():
        raise ValueError("API key is required")
    if not model.strip():
        raise ValueError("model is required")
    parsed = urlsplit(api_base.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API base must be an HTTP(S) URL")


def apply_setup(
    current: Settings,
    updates: dict[str, str],
    *,
    persist: bool,
) -> Settings:
    """Apply wizard values. Empty secret fields keep the current value."""

    base = (updates.get("openai_api_base") or current.openai_api_base).strip().rstrip("/")
    model = (updates.get("openai_model") or current.openai_model).strip()
    key = (updates.get("openai_api_key") or current.openai_api_key).strip()
    sessdata = (updates.get("sessdata") or current.sessdata).strip()
    validate_setup_values(api_base=base, api_key=key, model=model)
    merged = replace(
        current,
        openai_api_base=base,
        openai_api_key=key,
        openai_model=model,
        sessdata=sessdata,
    )
    if persist:
        save_user_config(
            {
                "openai_api_base": merged.openai_api_base,
                "openai_api_key": merged.openai_api_key,
                "openai_model": merged.openai_model,
                "sessdata": merged.sessdata,
            }
        )
    return merged


def setup_status(settings: Settings) -> dict[str, object]:
    path = user_config_path()
    return {
        "config_path": str(path),
        "persisted": path.is_file(),
        "openai_api_base": settings.openai_api_base,
        "openai_model": settings.openai_model,
        "has_api_key": bool(settings.openai_api_key),
        "has_sessdata": bool(settings.sessdata),
    }


def _load_sessdata_file() -> str:
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
    data_dir: Path
    job_workers: int
    job_queue_size: int
    api_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        # A clean installation must not assume the author's local proxy.
        saved = load_user_config()
        proxy = _env("HTTPS_PROXY") or _env("HTTP_PROXY")
        api_key = (
            _env("OPENAI_API_KEY")
            or _env("GLM_API_KEY")
            or saved.get("openai_api_key", "")
            or _load_glm_from_hermes()
        )
        whisper = _env("WHISPER_MODEL") or str(Path.home() / "whisper-model")
        return cls(
            sessdata=_env("BILI_SESSDATA") or saved.get("sessdata", "") or _load_sessdata_file(),
            proxy=proxy,
            openai_api_key=api_key,
            openai_api_base=(
                _env("OPENAI_API_BASE")
                or saved.get("openai_api_base", "")
                or "https://api.z.ai/api/paas/v4"
            ).rstrip("/"),
            openai_model=_env("OPENAI_MODEL") or saved.get("openai_model", "") or "glm-4-flash",
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
            data_dir=Path(
                os.path.expanduser(
                    _env(
                        "BFC_DATA_DIR",
                        str(Path.home() / ".local" / "share" / "bili-fact-checker"),
                    )
                )
            ),
            job_workers=max(1, int(_env("BFC_JOB_WORKERS", "2"))),
            job_queue_size=max(0, int(_env("BFC_JOB_QUEUE_SIZE", "8"))),
            api_token=_env("BFC_API_TOKEN"),
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
