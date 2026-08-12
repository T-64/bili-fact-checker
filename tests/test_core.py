"""Unit tests that do not require network."""

from __future__ import annotations

import json

import pytest

from bili_fact_checker.evidence.core import aggregate_verdict
from bili_fact_checker.ingest import extract_bvid
from bili_fact_checker.models import AtomicClaim, ClaimAnalysis
from bili_fact_checker.report import build_report, to_markdown, dumps_json


def test_extract_bvid_from_url():
    assert extract_bvid("https://www.bilibili.com/video/BV1EEGc6AErF") == "BV1EEGc6AErF"
    assert extract_bvid("BV1EEGc6AErF/?spm=1") == "BV1EEGc6AErF"


def test_report_schema_and_abstention_language():
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
    claim = AtomicClaim(
        id="claim_0001",
        claim_zh="无法核实的公开断言",
        claim_en="A public claim that could not be verified",
        claim_type="其他",
        quote="无法核实的公开断言",
        anchor_segment_ids=["seg_00001"],
        timestamp_sec=12,
    )
    claims = [
        ClaimAnalysis(claim=claim, verdict=aggregate_verdict([], [], []))
    ]
    report = build_report(
        transcript=transcript,
        summary="**概述**\n测试",
        claims=claims,
        model="fixture-model",
        search_providers=["none"],
    )
    data = report.model_dump(mode="json")
    assert data["schema_version"] == "1.0"
    assert data["stats"]["claim_count"] == 1
    assert data["stats"]["insufficient_evidence"] == 1
    md = to_markdown(report)
    assert "insufficient_evidence" in md
    assert "搜索结果摘要不计入证据" in md
    assert "model_inference" not in md
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


def test_multipart_video_selection(monkeypatch):
    from bili_fact_checker.config import Settings
    from bili_fact_checker.ingest import (
        fetch_video_info,
        select_video_part,
    )

    monkeypatch.setattr(
        "bili_fact_checker.ingest._api_get",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {
                "aid": 123,
                "cid": 111,
                "title": "多 P 测试视频",
                "pages": [
                    {"page": 1, "cid": 111, "part": "开篇"},
                    {"page": 2, "cid": 222, "part": "数据部分"},
                ],
            },
        },
    )
    metadata = fetch_video_info(Settings.from_env(), "BV1TEST00001")
    part = select_video_part(metadata, 2)
    assert metadata.aid == "123"
    assert part.cid == "222"
    assert part.title == "数据部分"

    with pytest.raises(ValueError, match="可选分 P：P1, P2"):
        select_video_part(metadata, 3)


def test_asr_download_targets_selected_part(tmp_path):
    from bili_fact_checker.config import Settings
    from bili_fact_checker.ingest import _audio_download_command

    settings = Settings.from_env()
    command = _audio_download_command(
        settings,
        "BV1TEST00001",
        str(tmp_path / "audio.m4a"),
        page=2,
    )
    assert "https://www.bilibili.com/video/BV1TEST00001?p=2" in command
