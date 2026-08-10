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
    google_factcheck_api_key: str
    searxng_url: str
    tavily_api_key: str
    whisper_model: str
    whisper_device: str
    whisper_compute_type: str
    cookie_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        proxy = _env("HTTPS_PROXY") or _env("HTTP_PROXY") or "http://127.0.0.1:7890"
        api_key = _env("OPENAI_API_KEY") or _env("GLM_API_KEY") or _load_glm_from_hermes()
        whisper = _env("WHISPER_MODEL") or str(Path.home() / "whisper-model")
        return cls(
            sessdata=_load_sessdata(),
            proxy=proxy,
            openai_api_key=api_key,
            openai_api_base=_env("OPENAI_API_BASE", "https://api.z.ai/api/paas/v4").rstrip("/"),
            openai_model=_env("OPENAI_MODEL", "glm-4-flash"),
            google_factcheck_api_key=_env("GOOGLE_FACTCHECK_API_KEY"),
            searxng_url=_env("SEARXNG_URL").rstrip("/"),
            tavily_api_key=_env("TAVILY_API_KEY"),
            whisper_model=os.path.expanduser(whisper),
            whisper_device=_env("WHISPER_DEVICE", "cuda"),
            whisper_compute_type=_env("WHISPER_COMPUTE_TYPE", "float16"),
            cookie_file=Path.home() / ".config" / "bili" / "cookies.txt",
        )


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
