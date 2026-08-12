"""Transcript-aware summarisation and atomic claim extraction."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import Segment, Transcript
from bili_fact_checker.providers import chat, extract_json_array


CHUNK_CHARS = 4500
CHUNK_OVERLAP = 400


def _chunk_text(
    text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Compatibility helper used by callers that only have plain text."""

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


def _timestamp(seconds: float) -> str:
    minutes, second = divmod(int(seconds), 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _chunk_segments(
    transcript: Transcript,
    size: int = CHUNK_CHARS,
    overlap_segments: int = 3,
) -> list[list[Segment]]:
    """Chunk a transcript without destroying source anchors."""

    if not transcript.segments:
        return []
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    current_size = 0
    for segment in transcript.segments:
        rendered_size = len(segment.text) + len(segment.id) + 16
        if current and current_size + rendered_size > size:
            chunks.append(current)
            current = current[-overlap_segments:] if overlap_segments else []
            current_size = sum(
                len(item.text) + len(item.id) + 16 for item in current
            )
        current.append(segment)
        current_size += rendered_size
    if current and (not chunks or current != chunks[-1]):
        chunks.append(current)
    return chunks


def _normalise(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def _claim_signature(value: str) -> tuple[set[str], set[str]]:
    numbers = set(re.findall(r"\d+(?:\.\d+)?%?", value))
    polarity_words = {
        word
        for word in (
            "上升",
            "下降",
            "增加",
            "减少",
            "支持",
            "反对",
            "是",
            "不是",
            "有",
            "没有",
            "高于",
            "低于",
        )
        if word in value
    }
    return numbers, polarity_words


def _claims_are_near_duplicate(left: str, right: str) -> bool:
    """Conservative overlap dedupe that keeps different numbers/polarity apart."""

    normalized_left = _normalise(left)
    normalized_right = _normalise(right)
    if normalized_left == normalized_right:
        return True
    if min(len(normalized_left), len(normalized_right)) < 8:
        return False
    if _claim_signature(left) != _claim_signature(right):
        return False
    return SequenceMatcher(
        None, normalized_left, normalized_right, autojunk=False
    ).ratio() >= 0.94


def _merge_claim_occurrence(
    existing: dict[str, Any],
    *,
    anchor_ids: list[str],
    timestamp: int,
    entities: list[str],
) -> None:
    existing["anchor_segment_ids"] = list(
        dict.fromkeys([*existing["anchor_segment_ids"], *anchor_ids])
    )
    timestamps = {
        int(existing.get("timestamp_sec") or 0),
        *(int(value) for value in existing.get("timestamps_sec") or []),
        timestamp,
    }
    existing["timestamps_sec"] = sorted(timestamps)
    existing["timestamp_sec"] = min(timestamps)
    existing["entities"] = list(
        dict.fromkeys([*existing.get("entities", []), *entities])
    )


def _select_claims_across_timeline(
    claims: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Apply a hard cap without selecting only the beginning of a long video."""

    if limit <= 0:
        return []
    ordered = sorted(claims, key=lambda item: int(item["timestamp_sec"]))
    if len(ordered) <= limit:
        selected = ordered
    elif limit == 1:
        selected = [ordered[len(ordered) // 2]]
    else:
        indexes = {
            round(index * (len(ordered) - 1) / (limit - 1))
            for index in range(limit)
        }
        selected = [ordered[index] for index in sorted(indexes)]
    for index, item in enumerate(selected, start=1):
        item["id"] = f"claim_{index:04d}"
    return selected


def _validated_anchor(
    item: dict[str, Any], segment_map: dict[str, Segment]
) -> tuple[list[str], str] | None:
    """Validate a verbatim quote and its segment references.

    Semantic similarity is intentionally not used here: a source anchor must be
    exact enough for the user to inspect in the original transcript.
    """

    quote = " ".join(str(item.get("quote") or "").split())
    raw_ids = item.get("anchor_segment_ids") or []
    if not isinstance(raw_ids, list):
        raw_ids = []
    supplied_ids = [str(value) for value in raw_ids]
    if supplied_ids and any(value not in segment_map for value in supplied_ids):
        return None
    ids = supplied_ids

    if not ids and quote:
        normalised_quote = _normalise(quote)
        for segment_id, segment in segment_map.items():
            if normalised_quote in _normalise(segment.text):
                ids = [segment_id]
                break

    if not ids or not quote:
        return None
    anchored_text = " ".join(segment_map[value].text for value in ids)
    if _normalise(quote) not in _normalise(anchored_text):
        return None
    return ids, quote


def summarize(settings: Settings, transcript: Transcript) -> str:
    """Map-reduce summarisation so long videos do not lose their ending."""

    chunks = _chunk_segments(transcript, size=7000, overlap_segments=1)
    if not chunks:
        return ""

    notes: list[str] = []
    for index, chunk in enumerate(chunks):
        body = "\n".join(
            f"[{segment.id} {_timestamp(segment.start)}] {segment.text}"
            for segment in chunk
        )
        prompt = f"""请提炼以下 B 站字幕片段的事实性内容和论证脉络。

要求：
1. 覆盖片段开头、中间和结尾的重要内容。
2. 保留关键限定条件、数字和不确定性。
3. 忽略广告、口头禅和互动引导。
4. 只输出简洁的中文要点，不补充字幕外知识。

视频：{transcript.title}
片段 {index + 1}/{len(chunks)}：
{body}
"""
        notes.append(
            chat(
                settings,
                prompt,
                system="你是谨慎的视频内容编辑，只总结提供的字幕。",
            ).strip()
        )

    combined = "\n\n".join(
        f"片段 {index + 1}：\n{note}" for index, note in enumerate(notes)
    )
    prompt = f"""把以下分段笔记合并成一份结构化中文总结。

要求：
1. 使用 3-5 个加粗小标题，不使用一级标题。
2. 去重但保留前中后内容、关键限定条件和分歧。
3. 不增加笔记中没有的事实。
4. 只输出最终 Markdown。

视频：{transcript.title}

{combined}
"""
    return chat(
        settings,
        prompt,
        system="你是谨慎的中文视频内容编辑，只输出 Markdown。",
    ).strip()


def extract_claims(settings: Settings, transcript: Transcript) -> list[dict[str, Any]]:
    """Extract atomic claims with exact, code-validated transcript anchors."""

    chunks = _chunk_segments(transcript)
    segment_map = {segment.id: segment for segment in transcript.segments}
    all_claims: list[dict[str, Any]] = []

    for index, chunk in enumerate(chunks):
        rendered = "\n".join(
            f"[{segment.id} {_timestamp(segment.start)}] {segment.text}"
            for segment in chunk
        )
        prompt = f"""从视频字幕片段中提取可独立核查的原子事实声明。

只保留具体数据、研究或论文引用、历史事件和可证伪断言。
忽略主观评价、预测、广告、情绪表达和空洞口号。复合声明必须拆开。

每项 JSON 字段：
- claim_zh: 独立、保留限定条件的中文声明
- claim_en: 忠实英文翻译，仅用于跨语言检索
- type: 统计数据|研究引用|历史事件|公共事件|其他
- quote: 必须逐字来自字幕的短原句，不得改写
- anchor_segment_ids: quote 所在的一个或多个 seg_XXXXX
- checkability_reason: 为什么可由外部证据核查
- entities: 人物、机构、地点或专有名词数组
- temporal_context: 声明涉及的日期或时期，没有则为空字符串

只输出 JSON 数组。

视频：{transcript.title}
片段 {index + 1}/{len(chunks)}：
{rendered}
"""
        raw = chat(
            settings,
            prompt,
            system="你是声明拆解器。所有 quote 必须来自输入，只输出 JSON 数组。",
        )
        for item in extract_json_array(raw):
            if not isinstance(item, dict):
                continue
            zh = str(item.get("claim_zh") or item.get("text") or "").strip()
            anchor = _validated_anchor(item, segment_map)
            if not zh or not _normalise(zh) or not anchor:
                continue
            anchor_ids, quote = anchor
            entities = item.get("entities") or []
            if not isinstance(entities, list):
                entities = []
            clean_entities = [
                str(value).strip() for value in entities if str(value).strip()
            ]
            timestamp = int(segment_map[anchor_ids[0]].start)
            duplicate = next(
                (
                    existing
                    for existing in all_claims
                    if _claims_are_near_duplicate(existing["claim_zh"], zh)
                ),
                None,
            )
            if duplicate is not None:
                _merge_claim_occurrence(
                    duplicate,
                    anchor_ids=anchor_ids,
                    timestamp=timestamp,
                    entities=clean_entities,
                )
                continue
            all_claims.append(
                {
                    "id": f"claim_{len(all_claims) + 1:04d}",
                    "claim_zh": zh,
                    "claim_en": str(item.get("claim_en") or "").strip(),
                    "claim_type": str(item.get("type") or "其他").strip(),
                    "quote": quote,
                    "anchor_segment_ids": anchor_ids,
                    "timestamp_sec": timestamp,
                    "timestamps_sec": [timestamp],
                    "checkability_reason": str(
                        item.get("checkability_reason") or ""
                    ).strip(),
                    "entities": clean_entities,
                    "temporal_context": str(
                        item.get("temporal_context") or ""
                    ).strip(),
                }
            )

    return _select_claims_across_timeline(all_claims, settings.max_claims)


def _guess_timestamp(needle: str, timeline: list[tuple[float, str]]) -> int:
    """Deprecated compatibility helper for downstream imports."""

    needle = needle.strip()
    if not needle:
        return 0
    for start, text in timeline:
        if needle[:20] in text or text[:20] in needle:
            return int(start)
    return 0
