"""Unit tests that do not require network."""

from __future__ import annotations

import json

from bili_fact_checker.ingest import extract_bvid
from bili_fact_checker.report import build_report, to_markdown, dumps_json


def test_extract_bvid_from_url():
    assert extract_bvid("https://www.bilibili.com/video/BV1EEGc6AErF") == "BV1EEGc6AErF"
    assert extract_bvid("BV1EEGc6AErF/?spm=1") == "BV1EEGc6AErF"


def test_report_schema_and_labels():
    transcript = {
        "bvid": "BV1TEST00001",
        "title": "测试视频",
        "aid": "1",
        "cid": "2",
        "source": "cc",
        "language": "zh-CN",
        "text": "hello" * 10,
        "segments": [],
    }
    claims = [
        {
            "claim_zh": "地球是圆的",
            "claim_en": "Earth is round",
            "type": "其他",
            "timestamp_sec": 12,
            "has_sourced_evidence": True,
            "judgment": {
                "verdict": "supported",
                "label": "sourced_web",
                "rationale": "多来源一致",
                "sources": ["https://example.com"],
            },
        },
        {
            "claim_zh": "无法核实的断言",
            "claim_en": "unverifiable",
            "type": "其他",
            "timestamp_sec": 0,
            "has_sourced_evidence": False,
            "judgment": {
                "verdict": "unverified",
                "label": "model_inference",
                "rationale": "无外部证据",
                "sources": [],
            },
        },
    ]
    report = build_report(
        transcript=transcript,
        summary="**概述**\n测试",
        claims=claims,
        tasks=["summary", "verify"],
    )
    assert report["schema_version"] == "0.1"
    assert report["stats"]["claim_count"] == 2
    assert report["stats"]["sourced"] == 1
    assert report["stats"]["model_inference"] == 1
    md = to_markdown(report)
    assert "model_inference" in md
    assert "辅助线索" in md
    raw = dumps_json(report)
    assert json.loads(raw)["video"]["bvid"] == "BV1TEST00001"


def test_parse_srt_and_load_file(tmp_path):
    from bili_fact_checker.config import Settings
    from bili_fact_checker.ingest import _parse_srt, load_transcript_file

    srt = """1
00:00:01,000 --> 00:00:03,000
你好世界

2
00:00:04,000 --> 00:00:06,500
第二句
"""
    segs = _parse_srt(srt)
    assert len(segs) == 2
    assert segs[0].text == "你好世界"
    path = tmp_path / "a.srt"
    path.write_text(srt, encoding="utf-8")
    settings = Settings.from_env()
    tr = load_transcript_file(settings, "BV1TEST00001", path)
    assert tr.source == "file"
    assert "你好世界" in tr.text


def test_chunking_import():
    from bili_fact_checker.analyze import _chunk_text

    chunks = _chunk_text("a" * 10000, size=3000, overlap=200)
    assert len(chunks) >= 3
    assert all(len(c) <= 3000 for c in chunks)
