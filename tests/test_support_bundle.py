from __future__ import annotations

import json
from dataclasses import replace

from bili_fact_checker.cli import build_parser, cmd_doctor
from bili_fact_checker.config import Settings
from bili_fact_checker.diagnostics import build_support_bundle, run_doctor


def test_support_bundle_is_redacted(tmp_path, monkeypatch):
    secret = "never-print-this-secret"
    monkeypatch.setenv("BFC_CONFIG_PATH", str(tmp_path / "missing.json"))
    settings = replace(
        Settings.from_env(),
        openai_api_key=secret,
        sessdata="private-cookie",
        api_token="token-secret-value-32-chars-ok",
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path,
    )
    bundle = build_support_bundle(settings)
    rendered = json.dumps(bundle, ensure_ascii=False)
    assert secret not in rendered
    assert "private-cookie" not in rendered
    assert "token-secret-value-32-chars-ok" not in rendered
    assert bundle["has_api_key"] is True
    assert "version" in bundle


def test_doctor_output_writes_bundle(tmp_path, monkeypatch):
    path = tmp_path / "support.json"
    settings = replace(Settings.from_env(), openai_api_key="fixture", cache_dir=tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.Settings.from_env", lambda: settings)
    args = build_parser().parse_args(["doctor", "--output", str(path)])
    assert cmd_doctor(args) == 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["ready"] is True
    assert "fixture" not in path.read_text(encoding="utf-8")
    assert run_doctor(settings).ready is True
