from __future__ import annotations

import json
from dataclasses import replace

from bili_fact_checker.cli import build_parser, cmd_doctor
from bili_fact_checker.config import Settings
from bili_fact_checker.diagnostics import run_doctor


def test_doctor_is_redacted_and_does_not_require_separate_native_search_key(tmp_path):
    secret = "never-print-this-secret"
    settings = replace(
        Settings.from_env(),
        openai_api_base="https://api.z.ai/api/paas/v4",
        openai_api_key=secret,
        openai_model="fixture-model",
        search_provider="auto",
        search_api_key="",
        cache_dir=tmp_path / "cache",
        sessdata="private-cookie",
    )

    report = run_doctor(settings)
    rendered = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.ready is True
    assert report.search_provider == "zai"
    assert report.search_native_to_llm is True
    assert secret not in rendered
    assert "private-cookie" not in rendered


def test_doctor_reports_missing_llm_key_as_error(tmp_path):
    settings = replace(
        Settings.from_env(),
        openai_api_key="",
        cache_dir=tmp_path,
    )
    report = run_doctor(settings)

    assert report.ready is False
    assert any(item.name == "llm" and item.status == "error" for item in report.checks)


def test_doctor_cli_is_available_without_video_argument(monkeypatch, capsys):
    parser = build_parser()
    args = parser.parse_args(["doctor", "--json"])
    settings = replace(Settings.from_env(), openai_api_key="fixture")
    monkeypatch.setattr(
        "bili_fact_checker.cli.Settings.from_env",
        lambda: settings,
    )

    code = cmd_doctor(args)

    assert code == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True
