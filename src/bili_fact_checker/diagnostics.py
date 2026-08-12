"""Redacted, non-billable installation and capability diagnostics."""

from __future__ import annotations

import os
import shutil
import sys
from importlib.util import find_spec
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from bili_fact_checker.config import Settings
from bili_fact_checker.providers.llm import build_llm_provider
from bili_fact_checker.providers.search import (
    UnavailableSearchProvider,
    build_search_provider,
)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: Literal["ok", "warning", "error"]
    message: str


@dataclass(frozen=True)
class DoctorReport:
    ready: bool
    checks: list[DoctorCheck]
    llm_provider: str
    search_provider: str
    search_native_to_llm: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "llm_provider": self.llm_provider,
            "search_provider": self.search_provider,
            "search_native_to_llm": self.search_native_to_llm,
            "checks": [asdict(item) for item in self.checks],
        }


def _writable_parent(path: Path) -> Path | None:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def run_doctor(settings: Settings) -> DoctorReport:
    """Inspect local configuration without contacting an API or Bilibili."""

    checks: list[DoctorCheck] = []
    if sys.version_info >= (3, 11):
        checks.append(
            DoctorCheck("python", "ok", f"Python {sys.version_info.major}.{sys.version_info.minor}")
        )
    else:  # pragma: no cover - package metadata prevents this in normal installs
        checks.append(DoctorCheck("python", "error", "需要 Python 3.11 或更高版本"))

    try:
        llm_provider = build_llm_provider(settings)
    except Exception as exc:
        llm_name = "invalid"
        checks.append(DoctorCheck("llm", "error", str(exc)))
    else:
        llm_name = llm_provider.name
        parsed = urlsplit(settings.openai_api_base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            checks.append(DoctorCheck("llm", "error", "LLM API base 不是有效的 HTTP(S) 地址"))
        elif not settings.openai_api_key:
            checks.append(
                DoctorCheck(
                    "llm",
                    "error",
                    f"{llm_name} 已识别，但缺少 OPENAI_API_KEY/GLM_API_KEY",
                )
            )
        elif not settings.openai_model:
            checks.append(DoctorCheck("llm", "error", "缺少 OPENAI_MODEL"))
        else:
            checks.append(
                DoctorCheck(
                    "llm",
                    "ok",
                    f"{llm_name} · {parsed.hostname} · {settings.openai_model}（未发起联网探测）",
                )
            )

    search_provider = build_search_provider(settings)
    search_name = search_provider.name
    native_search = search_provider.capabilities.native_to_llm
    if isinstance(search_provider, UnavailableSearchProvider):
        checks.append(
            DoctorCheck(
                "search",
                "warning",
                f"开放网页搜索不可用：{search_provider.reason}；核查会保守弃权",
            )
        )
    else:
        relationship = "复用 AI 账号" if native_search else "独立搜索服务"
        checks.append(
            DoctorCheck(
                "search",
                "ok",
                f"{search_name} · {relationship}（未发起可能计费的探测）",
            )
        )

    if settings.sessdata:
        checks.append(
            DoctorCheck("bilibili", "ok", "已找到 SESSDATA（未显示、未联网验证）")
        )
    else:
        checks.append(
            DoctorCheck(
                "bilibili",
                "warning",
                "未找到 SESSDATA；需要登录字幕的视频无法读取",
            )
        )

    cache_parent = _writable_parent(settings.cache_dir)
    if cache_parent is not None and os.access(cache_parent, os.W_OK):
        checks.append(
            DoctorCheck("cache", "ok", f"缓存目录可创建：{settings.cache_dir}")
        )
    else:
        checks.append(
            DoctorCheck(
                "cache",
                "warning",
                f"缓存目录不可写：{settings.cache_dir}；仍可运行但会重复请求",
            )
        )

    missing_tools = [name for name in ("yt-dlp", "ffmpeg") if not shutil.which(name)]
    if find_spec("faster_whisper") is None:
        missing_tools.append("faster-whisper")
    model_exists = Path(settings.whisper_model).expanduser().exists()
    if not missing_tools and model_exists:
        checks.append(DoctorCheck("asr", "ok", "本地 ASR 外部工具和模型路径已就绪"))
    else:
        details = []
        if missing_tools:
            details.append("缺少 " + ", ".join(missing_tools))
        if not model_exists:
            details.append("Whisper 模型路径不存在")
        checks.append(
            DoctorCheck(
                "asr",
                "warning",
                "；".join(details) + "；有 B 站字幕时不影响运行",
            )
        )

    return DoctorReport(
        ready=not any(item.status == "error" for item in checks),
        checks=checks,
        llm_provider=llm_name,
        search_provider=search_name,
        search_native_to_llm=native_search,
    )
