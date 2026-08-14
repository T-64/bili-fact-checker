from __future__ import annotations

import json
from pathlib import Path

from bili_fact_checker.models import AnalysisReport


def test_sample_reports_match_schema_1_0():
    root = Path(__file__).resolve().parents[1] / "examples"
    data = json.loads((root / "sample_report.json").read_text(encoding="utf-8"))
    report = AnalysisReport.model_validate(data)
    assert report.schema_version == "1.0"
    assert report.run.software_version == "1.0.0"
    assert report.stats.supported == 1
    assert report.stats.insufficient_evidence == 1
    md = (root / "sample_report.md").read_text(encoding="utf-8")
    html = (root / "sample_report.html").read_text(encoding="utf-8")
    assert "model_inference" not in md
    assert "insufficient_evidence" in md
    assert "证据核查报告" in html or "口播" in html
