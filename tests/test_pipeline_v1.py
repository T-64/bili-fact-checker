from __future__ import annotations

from dataclasses import replace

from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import Segment, Transcript
from bili_fact_checker.models import SearchProviderCapabilities, SearchUsage
from bili_fact_checker.pipeline import run_pipeline
from bili_fact_checker.providers.search import SearchBatch


class EmptySearchProvider:
    name = "fixture"
    capabilities = SearchProviderCapabilities(provider=name)

    def search(self, request):
        return SearchBatch(
            provider=self.name,
            usage=SearchUsage(
                provider=self.name,
                request_count=1,
                result_count=0,
            ),
        )


def test_runtime_pipeline_emits_v1_and_abstains_without_fetched_evidence(
    monkeypatch, tmp_path,
):
    transcript = Transcript(
        bvid="BV1TEST00001",
        title="测试视频",
        aid="1",
        cid="2",
        source="cc",
        language="zh-CN",
        page=1,
        part_title="第一部分",
        segments=[Segment(start=5, end=9, text="该指标在2024年下降了百分之十")],
    )
    monkeypatch.setattr(
        "bili_fact_checker.pipeline.fetch_transcript",
        lambda *_args, **_kwargs: transcript,
    )
    monkeypatch.setattr(
        "bili_fact_checker.pipeline.extract_claims",
        lambda *_args, **_kwargs: [
            {
                "id": "claim_0001",
                "claim_zh": "该指标在2024年下降10%",
                "claim_en": "The indicator fell 10 percent in 2024",
                "claim_type": "统计数据",
                "quote": "该指标在2024年下降了百分之十",
                "anchor_segment_ids": ["seg_00001"],
                "timestamp_sec": 5,
                "checkability_reason": "可由公开统计资料核查",
                "entities": [],
                "temporal_context": "2024年",
            }
        ],
    )
    monkeypatch.setattr(
        "bili_fact_checker.pipeline.build_search_provider",
        lambda _settings: EmptySearchProvider(),
    )
    settings = replace(
        Settings.from_env(),
        max_searches_per_claim=1,
        max_searches_per_run=1,
        cache_dir=tmp_path,
    )

    report = run_pipeline(settings, transcript.bvid, tasks=["verify"], asr=False)

    assert report["schema_version"] == "1.0"
    assert report["claims"][0]["verdict"]["verdict"] == "insufficient_evidence"
    assert report["claims"][0]["documents"] == []
    assert report["claims"][0]["excerpts"] == []
    assert report["run"]["search_providers"] == ["fixture"]
    assert report["run"]["search_usage"][0]["request_count"] == 1
    assert report["video"]["page"] == 1
    assert report["video"]["part_title"] == "第一部分"
    assert "judgment" not in report["claims"][0]
