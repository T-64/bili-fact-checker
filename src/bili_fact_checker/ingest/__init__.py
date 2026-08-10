"""Bilibili subtitle ingest: CC first, optional faster-whisper ASR."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.httputil import get_json, open_url


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transcript:
    bvid: str
    title: str
    aid: str
    cid: str
    source: str  # cc | asr
    language: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments if s.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "bvid": self.bvid,
            "title": self.title,
            "aid": self.aid,
            "cid": self.cid,
            "source": self.source,
            "language": self.language,
            "segments": [s.to_dict() for s in self.segments],
            "text": self.text,
        }

    def to_srt(self) -> str:
        lines: list[str] = []
        for i, seg in enumerate(self.segments, 1):
            text = seg.text.strip()
            if not text:
                continue
            lines.append(str(i))
            lines.append(f"{_fmt_ts(seg.start)} --> {_fmt_ts(seg.end)}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)


def extract_bvid(url_or_bvid: str) -> str:
    m = re.search(r"(BV[\w]+)", url_or_bvid)
    if not m:
        raise ValueError(f"无法从输入中提取 BV 号: {url_or_bvid}")
    return m.group(1)


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _api_get(settings: Settings, url: str, cookie: bool = False) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if cookie and settings.sessdata:
        headers["Cookie"] = f"SESSDATA={settings.sessdata}"
        headers["Referer"] = "https://www.bilibili.com/"
    return get_json(url, proxy=settings.proxy, headers=headers or None)


def fetch_video_meta(settings: Settings, bvid: str) -> tuple[str, str, str]:
    data = _api_get(
        settings,
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        cookie=True,
    )
    if data.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败: {data.get('message', 'unknown')}")
    info = data["data"]
    return str(info["aid"]), str(info["cid"]), str(info["title"])


def list_subtitles(settings: Settings, aid: str, cid: str) -> list[dict[str, Any]]:
    data = _api_get(
        settings,
        f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}",
        cookie=True,
    )
    if data.get("code") != 0:
        raise RuntimeError(f"获取字幕列表失败: {data.get('message', 'unknown')}")
    return data.get("data", {}).get("subtitle", {}).get("subtitles", []) or []


def _pick_subtitle(subs: list[dict[str, Any]], lang: str) -> dict[str, Any] | None:
    for s in subs:
        if s.get("lan") == lang:
            return s
    for s in subs:
        if lang in str(s.get("lan", "")):
            return s
    for s in subs:
        if "zh" in str(s.get("lan", "")):
            return s
    return subs[0] if subs else None


def _download_cc_segments(settings: Settings, subtitle_url: str) -> list[Segment]:
    if not subtitle_url.startswith("http"):
        subtitle_url = "https:" + subtitle_url
    raw = open_url(subtitle_url, proxy=settings.proxy)
    body = json.loads(raw.decode("utf-8")).get("body", [])
    segs: list[Segment] = []
    for item in body:
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        segs.append(
            Segment(
                start=float(item.get("from", 0)),
                end=float(item.get("to", 0)),
                text=text,
            )
        )
    return segs


def whisper_transcribe(settings: Settings, bvid: str, language: str = "auto") -> list[Segment]:
    """Download audio via yt-dlp, transcribe with faster-whisper."""
    audio_path = os.path.join(tempfile.gettempdir(), f"{bvid}.m4a")
    cmd = [
        "yt-dlp",
        "-f",
        "bestaudio",
        "-x",
        "--audio-format",
        "m4a",
        "-o",
        audio_path,
        f"https://www.bilibili.com/video/{bvid}",
        "--proxy",
        settings.proxy,
    ]
    if settings.cookie_file.exists():
        cmd.extend(["--cookies", str(settings.cookie_file)])

    subprocess.run(cmd, check=True, capture_output=True)

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "ASR 需要 faster-whisper：pip install 'bili-fact-checker[asr]'"
        ) from e

    model = WhisperModel(
        settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    lang = None if language in ("", "auto") else language
    try:
        segments_iter, _info = model.transcribe(audio_path, language=lang, beam_size=5)
        segs: list[Segment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue
            segs.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
        return segs
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)


def fetch_transcript(
    settings: Settings,
    url_or_bvid: str,
    *,
    lang: str = "zh-CN",
    asr: bool = True,
) -> Transcript:
    if not settings.sessdata:
        raise RuntimeError(
            "缺少 BILI_SESSDATA（环境变量或 ~/.config/bili/SESSDATA）"
        )

    bvid = extract_bvid(url_or_bvid)
    aid, cid, title = fetch_video_meta(settings, bvid)
    subs = list_subtitles(settings, aid, cid)
    target = _pick_subtitle(subs, lang)

    if target:
        segs = _download_cc_segments(settings, target.get("subtitle_url", ""))
        return Transcript(
            bvid=bvid,
            title=title,
            aid=aid,
            cid=cid,
            source="cc",
            language=str(target.get("lan", lang)),
            segments=segs,
        )

    if not asr:
        raise RuntimeError(
            f"视频 {bvid} 无可用字幕；加 --asr 使用本地 Whisper 转写"
        )

    segs = whisper_transcribe(settings, bvid, language="zh" if "zh" in lang else "auto")
    return Transcript(
        bvid=bvid,
        title=title,
        aid=aid,
        cid=cid,
        source="asr",
        language=lang,
        segments=segs,
    )
