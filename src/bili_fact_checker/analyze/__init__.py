"""Summary + claim extraction from transcripts."""

from __future__ import annotations

from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import Transcript
from bili_fact_checker.providers import chat, extract_json_array


CHUNK_CHARS = 4500
CHUNK_OVERLAP = 400


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = " ".join(text.split())
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def summarize(settings: Settings, transcript: Transcript) -> str:
    body = transcript.text
    if len(body) > 12000:
        body = body[:12000] + "\n…(截断)"

    prompt = f"""基于以下 B 站视频字幕，生成结构化总结（Markdown）。

要求：
1. 只用 3-5 个加粗小标题（**标题**），不要一级标题。
2. 第一个小标题做概述；覆盖前中后重要内容。
3. 忽略广告、口头禅、点赞引导。
4. 只输出最终总结，不要写作计划或自我说明。

标题：{transcript.title}
BV：{transcript.bvid}

字幕：
{body}
"""
    return chat(
        settings,
        prompt,
        system="你是中文视频内容分析助手。只输出 Markdown 总结。",
    ).strip()


def extract_claims(settings: Settings, transcript: Transcript) -> list[dict[str, Any]]:
    """Extract checkable claims with optional timestamps; chunk long videos."""
    chunks = _chunk_text(transcript.text)
    # Build a rough timestamp map from segments for nearest-second lookup
    timeline = [(s.start, s.text) for s in transcript.segments]

    all_claims: list[dict[str, Any]] = []
    seen: set[str] = set()

    for idx, chunk in enumerate(chunks):
        prompt = f"""从视频字幕片段中提取可独立核查的事实声明。

只保留：具体数据、研究/论文引用、历史事件、可证伪的断言。
忽略：主观评价、广告、情绪表达、空洞口号。

每项 JSON 字段：
- claim_zh: 中文声明
- claim_en: 英文翻译（便于检索国际事实核查库）
- type: 统计数据|研究引用|历史事件|其他
- quote: 字幕中支撑该声明的短原句
- timestamp_sec: 若能从上下文估计则给整数秒，否则 0

只输出 JSON 数组。

视频标题：{transcript.title}
片段 {idx + 1}/{len(chunks)}：
{chunk}
"""
        raw = chat(
            settings,
            prompt,
            system="你是事实核查助手。只输出 JSON 数组。",
        )
        for item in extract_json_array(raw):
            if not isinstance(item, dict):
                continue
            zh = str(item.get("claim_zh") or item.get("text") or "").strip()
            if not zh or zh in seen:
                continue
            seen.add(zh)
            ts = item.get("timestamp_sec", 0)
            try:
                ts_i = int(float(ts))
            except (TypeError, ValueError):
                ts_i = 0
            if ts_i <= 0 and timeline:
                quote = str(item.get("quote") or "")
                ts_i = _guess_timestamp(quote or zh, timeline)
            all_claims.append(
                {
                    "claim_zh": zh,
                    "claim_en": str(item.get("claim_en") or "").strip(),
                    "type": str(item.get("type") or "其他").strip(),
                    "quote": str(item.get("quote") or "").strip(),
                    "timestamp_sec": ts_i,
                }
            )

    return all_claims


def _guess_timestamp(needle: str, timeline: list[tuple[float, str]]) -> int:
    needle = needle.strip()
    if not needle:
        return 0
    for start, text in timeline:
        if needle[:20] in text or text[:20] in needle:
            return int(start)
    return 0
