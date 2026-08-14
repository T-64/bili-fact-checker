"""Browser-level smoke test. Skipped unless Playwright is installed."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import replace

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from bili_fact_checker.api.app import create_app
from bili_fact_checker.api.jobs import JobManager
from bili_fact_checker.config import Settings
from bili_fact_checker.ingest import VideoMetadata, VideoPart


def _report(bvid: str) -> dict:
    from bili_fact_checker.evidence.core import aggregate_verdict
    from bili_fact_checker.models import AtomicClaim, ClaimAnalysis
    from bili_fact_checker.report import build_report

    claim = AtomicClaim(
        id="claim_0001",
        claim_zh="示例声明需要外部证据核对",
        claim_en="Example claim",
        claim_type="其他",
        quote="示例声明需要外部证据核对",
        anchor_segment_ids=["seg_00001"],
        timestamp_sec=5,
    )
    return build_report(
        transcript={
            "bvid": bvid,
            "title": "示例视频",
            "aid": "1",
            "cid": "2",
            "source": "cc",
            "language": "zh-CN",
            "text": "示例声明需要外部证据核对",
            "segments": [
                {"id": "seg_00001", "start": 5, "end": 8, "text": "示例声明需要外部证据核对"}
            ],
        },
        summary="**概述**\n示例",
        claims=[ClaimAnalysis(claim=claim, verdict=aggregate_verdict([], [], []))],
        model="fixture-model",
        search_providers=["none"],
    ).model_dump(mode="json")


def test_browser_setup_submit_and_inspect_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bili_fact_checker.api.app.fetch_video_info",
        lambda _settings, bvid: VideoMetadata(
            bvid=bvid,
            aid="1",
            title="示例视频",
            parts=[VideoPart(page=1, cid="1", title="正片")],
        ),
    )
    configured = replace(
        Settings.from_env(),
        openai_api_key="",
        openai_model="",
        data_dir=tmp_path,
        job_workers=1,
        api_token="",
    )
    manager = JobManager(configured, runner=lambda _s, bvid, **_k: _report(bvid))
    app = create_app(configured, manager)

    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() >= deadline:
            manager.shutdown()
            raise AssertionError("API server did not start")
        time.sleep(0.05)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:
                pytest.skip(f"Chromium is not available: {exc}")
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.wait_for_selector("#setupForm:not(.hidden)")
            page.fill("#setupKey", "browser-secret")
            page.fill("#setupModel", "fixture-model")
            page.click("#saveSetup")
            page.wait_for_selector("#analyzeForm:not(.hidden)")
            page.fill("#bvid", "BV1TEST00001")
            page.click("#start")
            page.wait_for_selector("#claimDetail", timeout=15_000)
            assert "示例声明" in page.inner_text("#claimDetail")
            assert "证据不足" in page.inner_text("#claimDetail")
            mobile = browser.new_page(viewport={"width": 390, "height": 844})
            mobile.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert mobile.locator("#analyzeForm").is_visible()
            browser.close()
    finally:
        server.should_exit = True
        manager.shutdown()
