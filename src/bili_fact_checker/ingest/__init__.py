"""Bilibili subtitle ingest: CC first, optional faster-whisper ASR."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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
    id: str = ""

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

    def __post_init__(self) -> None:
        for index, segment in enumerate(self.segments, 1):
            if not segment.id:
                segment.id = f"seg_{index:05d}"

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


def check_bili_login(settings: Settings) -> tuple[bool, str]:
    """Return (is_login, uname_or_reason)."""
    try:
        data = _api_get(settings, "https://api.bilibili.com/x/web-interface/nav", cookie=True)
    except Exception as e:
        return False, f"nav 请求失败: {e}"
    if data.get("code") == -101 or not (data.get("data") or {}).get("isLogin"):
        return False, "SESSDATA 无效或已过期（账号未登录）"
    uname = str((data.get("data") or {}).get("uname") or "")
    return True, uname


def list_subtitles(
    settings: Settings, aid: str, cid: str, *, bvid: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (subtitles, player_data_meta)."""
    url = f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}"
    if bvid:
        url += f"&bvid={bvid}"
    data = _api_get(settings, url, cookie=True)
    if data.get("code") != 0:
        raise RuntimeError(f"获取字幕列表失败: {data.get('message', 'unknown')}")
    payload = data.get("data") or {}
    subs = (payload.get("subtitle") or {}).get("subtitles") or []
    meta = {
        "need_login_subtitle": bool(payload.get("need_login_subtitle")),
        "login_mid": payload.get("login_mid") or 0,
    }
    return list(subs), meta


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


def asr_ready(settings: Settings) -> tuple[bool, str]:
    """Return (ok, hint). Whisper runs locally via faster-whisper + model weights."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False, "未安装 faster-whisper：pip install 'bili-fact-checker[asr]'"
    model_path = Path(settings.whisper_model)
    if not model_path.exists():
        return (
            False,
            f"本地 Whisper 模型不存在: {model_path}（设置 WHISPER_MODEL，需 CTranslate2 格式）",
        )
    return True, ""


def _parse_srt(content: str) -> list[Segment]:
    segs: list[Segment] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    ts_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
    )

    def to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        # optional index line
        idx = 0
        if lines[0].strip().isdigit():
            idx = 1
        if idx >= len(lines):
            continue
        m = ts_re.search(lines[idx])
        if not m:
            continue
        text = " ".join(lines[idx + 1 :]).strip()
        if not text:
            continue
        segs.append(
            Segment(
                start=to_sec(*m.group(1, 2, 3, 4)),
                end=to_sec(*m.group(5, 6, 7, 8)),
                text=text,
            )
        )
    return segs


def load_transcript_file(
    settings: Settings,
    url_or_bvid: str,
    transcript_path: str | Path,
) -> Transcript:
    """Use an external transcript (e.g. from VideoCaptioner) instead of ASR."""
    path = Path(transcript_path)
    if not path.exists():
        raise FileNotFoundError(f"字幕文件不存在: {path}")

    bvid = extract_bvid(url_or_bvid)
    aid, cid, title = ("", "", bvid)
    if settings.sessdata:
        try:
            aid, cid, title = fetch_video_meta(settings, bvid)
        except Exception:
            pass

    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(raw)
        if isinstance(data, dict) and "segments" in data:
            segs = [
                Segment(
                    start=float(s.get("start", 0)),
                    end=float(s.get("end", 0)),
                    text=str(s.get("text", "")).strip(),
                )
                for s in data["segments"]
                if str(s.get("text", "")).strip()
            ]
            title = str(data.get("title") or title)
        else:
            raise ValueError("JSON 字幕需含 segments[{start,end,text}]")
    elif suffix == ".srt":
        segs = _parse_srt(raw)
    else:
        # plain text → one segment
        text = " ".join(raw.split())
        segs = [Segment(start=0.0, end=0.0, text=text)] if text else []

    if not segs:
        raise RuntimeError(f"未能从 {path} 解析出字幕内容")

    return Transcript(
        bvid=bvid,
        title=title,
        aid=aid,
        cid=cid,
        source="file",
        language="external",
        segments=segs,
    )


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
    subs, meta = list_subtitles(settings, aid, cid, bvid=bvid)
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

    # Empty track list is often "not logged in", not "video has no captions"
    if meta.get("need_login_subtitle") or not meta.get("login_mid"):
        ok, reason = check_bili_login(settings)
        if not ok:
            msg = (
                f"警告: 视频 {bvid} 的 CC/AI 字幕需要登录才能拉取，但当前 Cookie 未登录：{reason}。"
                "请更新 ~/.config/bili/SESSDATA（以及 cookies.txt）。"
            )
            if not asr:
                raise RuntimeError(
                    msg
                    + "\n已禁用 ASR。刷新登录后再跑，或去掉 --no-asr / 使用 --transcript。"
                )
            print(msg + " 将尝试本机 ASR 兜底（若可用）。", file=sys.stderr)

    if not asr:
        raise RuntimeError(
            f"视频 {bvid} 无 CC/AI 字幕，且已禁用 ASR。"
            "可：去掉 --no-asr 并安装本地 Whisper；"
            "或用 VideoCaptioner 等工具生成 .srt 后加 --transcript path.srt"
        )

    ok, hint = asr_ready(settings)
    if not ok:
        raise RuntimeError(
            f"视频 {bvid} 无现成字幕，需要本地语音转写，但环境未就绪：{hint}\n"
            "可选方案：\n"
            "  1) pip install 'bili-fact-checker[asr]' 并准备 CTranslate2 模型（WHISPER_MODEL）\n"
            "  2) 用外部字幕工具（如 VideoCaptioner）先转出 .srt，再运行：\n"
            "     bili-fact-checker run BV... --transcript out.srt"
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
