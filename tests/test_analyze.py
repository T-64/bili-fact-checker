from __future__ import annotations

import json
from dataclasses import replace

from bili_fact_checker.analyze import (
    _claims_are_near_duplicate,
    _validated_anchor,
    _select_claims_across_timeline,
    extract_claims,
)
from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import Segment, Transcript, _plain_text_segments


def test_plain_text_input_is_split_into_bounded_segments():
    segments = _plain_text_segments("没有标点" * 300, max_chars=120)

    assert len(segments) > 1
    assert all(0 < len(item.text) <= 120 for item in segments)


def test_near_duplicate_dedupe_is_conservative_about_numbers_and_polarity():
    assert _claims_are_near_duplicate(
        "该机构报告称2024年指标下降10%。",
        "该机构的报告称，2024年该指标下降10%",
    )
    assert not _claims_are_near_duplicate(
        "该机构报告称2024年指标下降10%。",
        "该机构报告称2024年指标下降20%。",
    )
    assert not _claims_are_near_duplicate(
        "研究发现这种疗法是有效的",
        "研究发现这种疗法不是有效的",
    )


def test_anchor_with_fabricated_segment_id_is_rejected():
    segment = Segment(start=0, end=1, text="这是字幕中的原话。", id="seg_00001")
    result = _validated_anchor(
        {
            "quote": "这是字幕中的原话。",
            "anchor_segment_ids": ["seg_00001", "seg_99999"],
        },
        {segment.id: segment},
    )

    assert result is None


def test_timeline_cap_keeps_beginning_middle_and_end():
    claims = [
        {
            "id": f"claim_{index + 1:04d}",
            "timestamp_sec": index * 10,
        }
        for index in range(10)
    ]

    selected = _select_claims_across_timeline(claims, 3)

    assert [item["timestamp_sec"] for item in selected] == [0, 40, 90]
    assert [item["id"] for item in selected] == [
        "claim_0001",
        "claim_0002",
        "claim_0003",
    ]


def test_claim_extraction_scans_all_chunks_before_applying_limit(monkeypatch):
    transcript = Transcript(
        bvid="BV1TEST00001",
        title="长视频",
        aid="1",
        cid="2",
        source="cc",
        language="zh-CN",
        segments=[
            Segment(start=0, end=1, text="2020年样本数量为一百。"),
            Segment(start=100, end=101, text="2021年样本数量为二百。"),
            Segment(start=200, end=201, text="2022年样本数量为三百。"),
        ],
    )
    monkeypatch.setattr(
        "bili_fact_checker.analyze._chunk_segments",
        lambda value: [[item] for item in value.segments],
    )
    calls = []

    def fake_chat(_settings, prompt, **_kwargs):
        calls.append(prompt)
        for index, segment in enumerate(transcript.segments, start=1):
            if segment.id in prompt:
                return json.dumps(
                    [
                        {
                            "claim_zh": segment.text,
                            "claim_en": "",
                            "type": "统计数据",
                            "quote": segment.text,
                            "anchor_segment_ids": [segment.id],
                            "checkability_reason": "可查",
                            "entities": [],
                            "temporal_context": str(2019 + index),
                        }
                    ],
                    ensure_ascii=False,
                )
        return "[]"

    monkeypatch.setattr("bili_fact_checker.analyze.chat", fake_chat)
    settings = replace(Settings.from_env(), max_claims=2)

    claims = extract_claims(settings, transcript)

    assert len(calls) == 3
    assert [item["timestamp_sec"] for item in claims] == [0, 200]
    assert [item["timestamps_sec"] for item in claims] == [[0], [200]]


def test_repeated_claim_keeps_all_occurrence_timestamps(monkeypatch):
    transcript = Transcript(
        bvid="BV1TEST00001",
        title="重复声明",
        aid="1",
        cid="2",
        source="cc",
        language="zh-CN",
        segments=[
            Segment(start=5, end=6, text="报告称2024年指标下降10%。"),
            Segment(start=65, end=66, text="报告称，2024年该指标下降10%。"),
        ],
    )
    monkeypatch.setattr(
        "bili_fact_checker.analyze._chunk_segments",
        lambda value: [[item] for item in value.segments],
    )

    def fake_chat(_settings, prompt, **_kwargs):
        segment = next(item for item in transcript.segments if item.id in prompt)
        return json.dumps(
            [
                {
                    "claim_zh": segment.text,
                    "type": "统计数据",
                    "quote": segment.text,
                    "anchor_segment_ids": [segment.id],
                }
            ],
            ensure_ascii=False,
        )

    monkeypatch.setattr("bili_fact_checker.analyze.chat", fake_chat)

    claims = extract_claims(Settings.from_env(), transcript)

    assert len(claims) == 1
    assert claims[0]["anchor_segment_ids"] == ["seg_00001", "seg_00002"]
    assert claims[0]["timestamps_sec"] == [5, 65]
