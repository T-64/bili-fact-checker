from __future__ import annotations

import json
import stat
from dataclasses import replace

from bili_fact_checker.api.app import create_app
from bili_fact_checker.cli import build_parser, cmd_setup
from bili_fact_checker.config import Settings, load_user_config, save_user_config
from bili_fact_checker.diagnostics import run_doctor


def isolate_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BFC_CONFIG_PATH", str(tmp_path / "config.json"))
    for name in (
        "OPENAI_API_KEY",
        "GLM_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_MODEL",
        "BILI_SESSDATA",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("bili_fact_checker.config._load_glm_from_hermes", lambda: "")
    monkeypatch.setattr("bili_fact_checker.config._load_sessdata_file", lambda: "")


def test_user_config_is_used_when_env_is_empty(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    save_user_config(
        {
            "openai_api_base": "https://api.example.test/v1",
            "openai_api_key": "file-secret",
            "openai_model": "file-model",
            "sessdata": "cookie-secret",
        }
    )

    settings = Settings.from_env()

    assert settings.openai_api_key == "file-secret"
    assert settings.openai_api_base == "https://api.example.test/v1"
    assert settings.openai_model == "file-model"
    assert settings.sessdata == "cookie-secret"


def test_env_overrides_user_config(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    save_user_config({"openai_api_key": "file-secret", "openai_model": "file-model"})
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")

    settings = Settings.from_env()

    assert settings.openai_api_key == "env-secret"
    assert settings.openai_model == "env-model"


def test_save_user_config_is_private_and_ignores_unknown_fields(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    path = save_user_config(
        {"openai_api_key": "file-secret", "unknown": "nope", "openai_model": "m"}
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"openai_api_key": "file-secret", "openai_model": "m"}
    assert load_user_config()["openai_api_key"] == "file-secret"


def test_setup_cli_saves_without_echoing_secrets(tmp_path, monkeypatch, capsys):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.sys.stdin.isatty", lambda: False)
    args = build_parser().parse_args(
        [
            "setup",
            "--api-base",
            "https://api.example.test/v1",
            "--api-key",
            "cli-secret",
            "--model",
            "cli-model",
            "--save",
        ]
    )

    assert cmd_setup(args) == 0
    output = capsys.readouterr().out
    assert "cli-secret" not in output
    assert Settings.from_env().openai_api_key == "cli-secret"
    assert "cli-model" in output


def test_setup_cli_without_save_does_not_claim_ready(tmp_path, monkeypatch, capsys):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.sys.stdin.isatty", lambda: False)
    args = build_parser().parse_args(
        ["setup", "--api-key", "temp-secret", "--model", "m"]
    )
    assert cmd_setup(args) == 0
    output = capsys.readouterr().out
    assert "没有改变后续命令配置" in output
    assert Settings.from_env().openai_api_key == ""


def test_setup_rejects_invalid_base_and_empty_model(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.sys.stdin.isatty", lambda: False)
    bad_base = build_parser().parse_args(
        ["setup", "--api-base", "not-a-url", "--api-key", "k", "--model", "m", "--save"]
    )
    assert cmd_setup(bad_base) == 1
    configured = replace(
        Settings.from_env(), openai_api_key="k", openai_model="", openai_api_base=""
    )
    from bili_fact_checker.config import apply_setup
    import pytest

    with pytest.raises(ValueError, match="HTTP"):
        apply_setup(
            configured,
            {"openai_api_base": "ftp://x", "openai_api_key": "k", "openai_model": "m"},
            persist=False,
        )
    with pytest.raises(ValueError, match="model"):
        apply_setup(
            replace(Settings.from_env(), openai_model=""),
            {"openai_api_key": "k", "openai_model": ""},
            persist=False,
        )


def test_corrupt_config_is_diagnosed(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    settings = Settings.from_env()
    report = run_doctor(settings)
    assert report.ready is False
    assert any(item.name == "config" and item.status == "error" for item in report.checks)


def test_setup_clear_removes_file(tmp_path, monkeypatch, capsys):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.sys.stdin.isatty", lambda: False)
    assert (
        cmd_setup(
            build_parser().parse_args(
                ["setup", "--api-key", "cli-secret", "--model", "m", "--save"]
            )
        )
        == 0
    )
    assert (tmp_path / "config.json").exists()
    assert cmd_setup(build_parser().parse_args(["setup", "--clear"])) == 0
    assert not (tmp_path / "config.json").exists()
    assert "cli-secret" not in capsys.readouterr().out


def test_setup_cli_requires_api_key(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr("bili_fact_checker.cli.sys.stdin.isatty", lambda: False)
    args = build_parser().parse_args(["setup", "--model", "x"])

    assert cmd_setup(args) == 1
    assert not (tmp_path / "config.json").exists()


def test_setup_api_redacts_secrets_and_can_persist(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    configured = replace(Settings.from_env(), openai_api_key="", data_dir=tmp_path)
    app = create_app(configured)

    import asyncio

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            missing = await client.put("/v1/setup", json={"openai_model": "x"})
            assert missing.status_code == 400

            preview = await client.get("/v1/setup")
            assert preview.status_code == 200
            assert "openai_api_key" not in preview.json()
            assert preview.json()["has_api_key"] is False

            saved = await client.put(
                "/v1/setup",
                json={
                    "openai_api_base": "https://api.example.test/v1",
                    "openai_api_key": "api-secret",
                    "openai_model": "api-model",
                    "persist": True,
                },
            )
            body = saved.json()
            assert saved.status_code == 200
            assert body["ready"] is True
            assert body["has_api_key"] is True
            assert body["persisted"] is True
            assert "api-secret" not in json.dumps(body)

            status = await client.get("/v1/status")
            assert status.json()["ready"] is True
            assert "api-secret" not in json.dumps(status.json())

            cleared = await client.delete("/v1/setup")
            assert cleared.status_code == 200
            assert cleared.json()["cleared"] is True
            assert "api-secret" not in json.dumps(cleared.json())

    asyncio.run(scenario())
    assert not (tmp_path / "config.json").exists()


def test_setup_applies_to_the_next_job(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    observed = {}

    def runner(run_settings, _bvid, **_kwargs):
        observed["key"] = run_settings.openai_api_key
        return {
            "schema_version": "1.0",
            "disclaimer": "fixture",
            "run": {"generated_at": "2026-08-15T00:00:00Z"},
            "video": {
                "bvid": "BV1TEST00001",
                "title": "测试视频",
                "url": "https://www.bilibili.com/video/BV1TEST00001",
                "page": 1,
            },
            "summary": "",
            "claims": [],
            "stats": {},
        }

    from bili_fact_checker.api.jobs import JobManager

    configured = replace(
        Settings.from_env(), openai_api_key="", data_dir=tmp_path, job_workers=1
    )
    manager = JobManager(configured, runner=runner)
    app = create_app(configured, manager)

    import asyncio
    import time

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            await client.put(
                "/v1/setup",
                json={"openai_api_key": "job-secret", "persist": False},
            )
            job = await client.post(
                "/v1/analyze", json={"bvid": "BV1TEST00001", "asr": False}
            )
            assert job.status_code == 202
            job_id = job.json()["id"]
            deadline = time.monotonic() + 3
            while manager.get(job_id)["status"] != "done":
                if time.monotonic() >= deadline:
                    raise AssertionError(manager.get(job_id))
                await asyncio.sleep(0.01)

    try:
        asyncio.run(scenario())
        assert observed["key"] == "job-secret"
    finally:
        manager.shutdown()


def test_setup_api_without_persist_applies_in_memory_only(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    configured = replace(Settings.from_env(), openai_api_key="", data_dir=tmp_path)
    app = create_app(configured)

    import asyncio

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            saved = await client.put(
                "/v1/setup",
                json={
                    "openai_api_key": "memory-secret",
                    "openai_model": "memory-model",
                    "persist": False,
                },
            )
            assert saved.status_code == 200
            assert saved.json()["persisted"] is False
            assert (await client.get("/v1/status")).json()["ready"] is True

    asyncio.run(scenario())
    assert not (tmp_path / "config.json").exists()
    assert Settings.from_env().openai_api_key == ""


def test_persist_false_does_not_claim_a_file_write(tmp_path, monkeypatch):
    isolate_config(monkeypatch, tmp_path)
    save_user_config({"openai_api_key": "file-secret", "openai_model": "file-model"})
    configured = replace(Settings.from_env(), data_dir=tmp_path)
    app = create_app(configured)

    import asyncio

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            saved = await client.put(
                "/v1/setup",
                json={"openai_api_key": "session-secret", "persist": False},
            )
            assert saved.status_code == 200
            assert saved.json()["persisted"] is False

    asyncio.run(scenario())
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))[
        "openai_api_key"
    ] == "file-secret"


def test_persisted_setup_keeps_wizard_values_when_env_also_has_a_key(
    tmp_path, monkeypatch
):
    isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    observed = {}

    def runner(run_settings, _bvid, **_kwargs):
        observed["key"] = run_settings.openai_api_key
        return {
            "schema_version": "1.0",
            "disclaimer": "fixture",
            "run": {"generated_at": "2026-08-15T00:00:00Z"},
            "video": {
                "bvid": "BV1TEST00001",
                "title": "测试视频",
                "url": "https://www.bilibili.com/video/BV1TEST00001",
                "page": 1,
            },
            "summary": "",
            "claims": [],
            "stats": {},
        }

    from bili_fact_checker.api.jobs import JobManager

    configured = replace(
        Settings.from_env(), openai_api_key="", data_dir=tmp_path, job_workers=1
    )
    manager = JobManager(configured, runner=runner)
    app = create_app(configured, manager)

    import asyncio
    import time

    import httpx

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            saved = await client.put(
                "/v1/setup",
                json={
                    "openai_api_key": "wizard-secret",
                    "openai_model": "wizard-model",
                    "persist": True,
                },
            )
            assert saved.status_code == 200
            assert saved.json()["persisted"] is True
            job = await client.post(
                "/v1/analyze", json={"bvid": "BV1TEST00001", "asr": False}
            )
            job_id = job.json()["id"]
            deadline = time.monotonic() + 3
            while manager.get(job_id)["status"] != "done":
                if time.monotonic() >= deadline:
                    raise AssertionError(manager.get(job_id))
                await asyncio.sleep(0.01)

    try:
        asyncio.run(scenario())
        assert observed["key"] == "wizard-secret"
        saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert saved["openai_api_key"] == "wizard-secret"
        assert Settings.from_env().openai_api_key == "env-secret"
    finally:
        manager.shutdown()
