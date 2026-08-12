from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace

import httpx
import pytest

from bili_fact_checker.api.app import create_app
from bili_fact_checker.api.jobs import JobManager, QueueFullError
from bili_fact_checker.config import Settings


def settings(tmp_path, **changes) -> Settings:
    values = {
        "data_dir": tmp_path,
        "job_workers": 1,
        "job_queue_size": 1,
        "api_token": "",
        **changes,
    }
    return replace(Settings.from_env(), **values)


def minimal_report(bvid: str) -> dict:
    return {
        "schema_version": "1.0",
        "disclaimer": "fixture",
        "run": {"generated_at": "2026-08-13T00:00:00Z"},
        "video": {
            "bvid": bvid,
            "title": "测试视频",
            "url": f"https://www.bilibili.com/video/{bvid}",
            "page": 1,
        },
        "summary": "测试总结",
        "claims": [],
        "stats": {},
    }


def wait_for_status(manager: JobManager, job_id: str, expected: set[str]):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job["status"] in expected:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {expected}: {manager.get(job_id)}")


def test_persistent_job_completes_and_writes_all_report_formats(tmp_path):
    def runner(_settings, bvid, *, log, **_kwargs):
        log("ingest: fixture")
        log("evidence: fixture")
        return minimal_report(bvid)

    manager = JobManager(settings(tmp_path), runner=runner)
    try:
        submitted = manager.submit(
            {
                "bvid": "BV1TEST00001",
                "tasks": ["summary", "verify"],
                "lang": "zh-CN",
                "asr": False,
                "page": 1,
            }
        )
        done = wait_for_status(manager, submitted["id"], {"done"})

        assert done["stage"] == "done"
        assert manager.report_path(done["id"], "json").exists()
        assert manager.report_path(done["id"], "md").exists()
        assert manager.report_path(done["id"], "html").exists()
        persisted = json.loads(
            (tmp_path / "jobs" / done["id"] / "state.json").read_text()
        )
        assert persisted["status"] == "done"

        retried = manager.retry(done["id"])
        retried_done = wait_for_status(manager, retried["id"], {"done"})
        assert retried_done["id"] != done["id"]
        assert retried_done["request"] == done["request"]
    finally:
        manager.shutdown()


def test_bounded_queue_and_cooperative_cancellation(tmp_path):
    entered = threading.Event()

    def runner(_settings, bvid, *, log, **_kwargs):
        entered.set()
        while True:
            log("evidence: still running")
            time.sleep(0.01)

    manager = JobManager(
        settings(tmp_path, job_queue_size=0),
        runner=runner,
    )
    try:
        first = manager.submit({"bvid": "BV1TEST00001"})
        assert entered.wait(timeout=1)
        with pytest.raises(QueueFullError):
            manager.submit({"bvid": "BV1TEST00002"})

        manager.cancel(first["id"])
        cancelled = wait_for_status(manager, first["id"], {"cancelled"})
        assert cancelled["error"] == "任务已由用户取消。"
    finally:
        manager.shutdown()


def test_recovery_marks_incomplete_job_as_interrupted(tmp_path):
    job_id = "abcdef123456"
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text(
        json.dumps(
            {
                "id": job_id,
                "status": "running",
                "created_at": "2026-08-13T00:00:00Z",
                "request": {"bvid": "BV1TEST00001"},
                "logs": [],
            }
        ),
        encoding="utf-8",
    )

    manager = JobManager(settings(tmp_path))
    try:
        recovered = manager.get(job_id)
        assert recovered["status"] == "interrupted"
        assert "可以重试" in recovered["error"]
    finally:
        manager.shutdown()


def test_api_auth_status_and_report_flow(tmp_path):
    configured = settings(tmp_path, api_token="test-token")

    def runner(_settings, bvid, *, log, **_kwargs):
        log("ingest: fixture")
        return minimal_report(bvid)

    manager = JobManager(configured, runner=runner)
    app = create_app(configured, manager)
    headers = {"Authorization": "Bearer test-token"}
    try:
        async def scenario() -> None:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                assert (await client.get("/health")).status_code == 200
                assert (await client.get("/v1/status")).status_code == 401
                status = await client.get("/v1/status", headers=headers)
                assert status.status_code == 200
                assert status.json()["authentication_required"] is True

                response = await client.post(
                    "/v1/analyze",
                    headers=headers,
                    json={"bvid": "BV1TEST00001", "asr": False},
                )
                assert response.status_code == 202
                job_id = response.json()["id"]
                deadline = time.monotonic() + 3
                while manager.get(job_id)["status"] != "done":
                    if time.monotonic() >= deadline:
                        raise AssertionError("API job did not complete")
                    await asyncio.sleep(0.01)
                report = await client.get(
                    f"/v1/jobs/{job_id}/report", headers=headers
                )
                assert report.status_code == 200
                assert report.json()["video"]["bvid"] == "BV1TEST00001"

                invalid = await client.post(
                    "/v1/analyze",
                    headers=headers,
                    json={"bvid": "BV1TEST00001", "preset": "unlimited"},
                )
                assert invalid.status_code == 422

        asyncio.run(scenario())
    finally:
        manager.shutdown()


def test_fast_preset_applies_real_server_side_limits(tmp_path):
    observed = {}

    def runner(run_settings, bvid, *, log, **_kwargs):
        observed.update(
            max_claims=run_settings.max_claims,
            per_claim=run_settings.max_searches_per_claim,
            total=run_settings.max_searches_per_run,
        )
        return minimal_report(bvid)

    configured = settings(
        tmp_path,
        max_claims=15,
        max_searches_per_claim=3,
        max_searches_per_run=30,
    )
    manager = JobManager(configured, runner=runner)
    try:
        job = manager.submit({"bvid": "BV1TEST00001", "preset": "fast"})
        wait_for_status(manager, job["id"], {"done"})
        assert observed == {"max_claims": 6, "per_claim": 1, "total": 6}
    finally:
        manager.shutdown()


def test_packaged_web_ui_contains_primary_accessible_controls():
    import bili_fact_checker.api.app as app_module

    path = (
        app_module.Path(app_module.__file__).resolve().parent.parent
        / "web_dist"
        / "index.html"
    )
    content = path.read_text(encoding="utf-8")

    assert "证据台 · B站口播核查" in content
    assert 'id="analyzeForm"' in content
    assert 'label for="bvid"' in content
    assert 'aria-label="核查模式"' in content
    assert 'id="auditContent"' in content
