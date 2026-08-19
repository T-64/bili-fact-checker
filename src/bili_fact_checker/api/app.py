"""Loopback-first FastAPI application."""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bili_fact_checker import __version__
from bili_fact_checker.api.jobs import JobManager, JobNotFoundError, QueueFullError
from bili_fact_checker.config import Settings, apply_setup, clear_user_config, setup_status
from bili_fact_checker.diagnostics import run_doctor
from bili_fact_checker.ingest import check_bili_login, extract_bvid, fetch_video_info
from bili_fact_checker.ingest.login import (
    QrLoginMonitor,
    generate_login_qr,
    persist_bili_login,
    poll_login_qr,
    public_login_state,
    qr_svg,
    should_update_user_config,
    sync_sessdata_from_cookie_file,
)


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openai_api_base: str = Field(default="", max_length=300)
    openai_api_key: str = Field(default="", max_length=2000)
    openai_model: str = Field(default="", max_length=200)
    sessdata: str = Field(default="", max_length=4000)
    persist: bool = False


class BiliQrPollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qrcode_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9]+$")
    persist: bool = True


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bvid: str = Field(min_length=5, max_length=300)
    tasks: list[str] = Field(default_factory=lambda: ["summary", "verify"])
    lang: str = Field(default="zh-CN", max_length=20)
    asr: bool = True
    page: int = Field(default=1, ge=1, le=10_000)
    preset: str = Field(default="balanced", pattern=r"^(fast|balanced|strict)$")

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: list[str]) -> list[str]:
        allowed = {"summary", "claims", "verify"}
        normalized = list(dict.fromkeys(value))
        if not normalized or any(item not in allowed for item in normalized):
            raise ValueError("tasks must contain summary, claims, and/or verify")
        return normalized


def create_app(
    settings: Settings | None = None,
    manager: JobManager | None = None,
) -> FastAPI:
    configured = settings or Settings.from_env()
    jobs = manager or JobManager(configured)
    qr_monitor = QrLoginMonitor()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        qr_monitor.stop()
        if manager is None:
            jobs.shutdown(wait=False)

    application = FastAPI(
        title="bili-fact-checker",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.state.jobs = jobs
    application.state.qr_monitor = qr_monitor

    def current_settings() -> Settings:
        return application.state.settings

    def replace_settings(next_settings: Settings) -> None:
        application.state.settings = next_settings
        jobs.settings = next_settings

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        token = current_settings().api_token
        if not token:
            return
        expected = f"Bearer {token}"
        provided = authorization or ""
        try:
            matches = hmac.compare_digest(provided, expected)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise HTTPException(401, "missing or invalid bearer token")

    def find_job(job_id: str) -> dict[str, Any]:
        try:
            return jobs.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(404, "job not found") from exc

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/v1/setup", dependencies=[Depends(authorize)])
    async def get_setup() -> dict[str, Any]:
        return setup_status(current_settings())

    @application.put("/v1/setup", dependencies=[Depends(authorize)])
    async def put_setup(request: SetupRequest) -> dict[str, Any]:
        updates = {
            "openai_api_base": request.openai_api_base.strip(),
            "openai_api_key": request.openai_api_key.strip(),
            "openai_model": request.openai_model.strip(),
            "sessdata": request.sessdata.strip(),
        }
        try:
            merged = apply_setup(current_settings(), updates, persist=False)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if request.persist:
            apply_setup(merged, {}, persist=True)
        replace_settings(merged)
        status = setup_status(merged)
        status["ready"] = run_doctor(merged).ready
        status["persisted"] = request.persist
        return status

    @application.delete("/v1/setup", dependencies=[Depends(authorize)])
    async def delete_setup() -> dict[str, Any]:
        clear_user_config()
        replace_settings(Settings.from_env())
        status = setup_status(current_settings())
        status["ready"] = run_doctor(current_settings()).ready
        status["cleared"] = True
        return status

    @application.post(
        "/v1/setup/bilibili/qrcode", dependencies=[Depends(authorize)]
    )
    async def bili_qrcode() -> dict[str, Any]:
        try:
            qr = await asyncio.to_thread(generate_login_qr, current_settings())
            svg = await asyncio.to_thread(qr_svg, qr.url)
        except Exception as exc:
            raise HTTPException(502, f"获取登录二维码失败：{exc}") from exc

        def persist_from_qr(cookies: dict[str, str]) -> tuple[bool, str]:
            merged = persist_bili_login(
                current_settings(),
                cookies,
                persist_config=should_update_user_config(True),
            )
            replace_settings(merged)
            return check_bili_login(merged)

        qr_monitor.start(current_settings(), qr, svg, persist_from_qr)
        return {"qrcode_key": qr.qrcode_key, "url": qr.url, "svg": svg}

    @application.get(
        "/v1/setup/bilibili/session", dependencies=[Depends(authorize)]
    )
    async def bili_session() -> dict[str, str]:
        return qr_monitor.public_state()

    @application.post(
        "/v1/setup/bilibili/poll", dependencies=[Depends(authorize)]
    )
    async def bili_poll(request: BiliQrPollRequest) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                poll_login_qr, current_settings(), request.qrcode_key
            )
        except Exception as exc:
            raise HTTPException(502, f"查询登录状态失败：{exc}") from exc
        if result.status != "success":
            return {"status": result.status, "message": result.message}
        try:
            merged = persist_bili_login(
                current_settings(),
                result.cookies or {},
                persist_config=should_update_user_config(request.persist),
            )
        except Exception as exc:
            raise HTTPException(502, f"保存登录态失败：{exc}") from exc
        replace_settings(merged)
        ok, info = await asyncio.to_thread(check_bili_login, merged)
        if not ok:
            return {
                "status": "error",
                "message": f"Cookie 已写入，但登录校验失败：{info}",
            }
        return {"status": "success", "uname": info, "message": f"已登录 {info}"}

    @application.get("/v1/video", dependencies=[Depends(authorize)])
    async def video_info(bvid: str = Query(min_length=5, max_length=300)) -> dict[str, Any]:
        try:
            extracted = extract_bvid(bvid)
            metadata = fetch_video_info(current_settings(), extracted)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception:
            raise HTTPException(502, "failed to fetch video metadata") from None
        return {
            "bvid": metadata.bvid,
            "title": metadata.title,
            "parts": [
                {"page": part.page, "title": part.title} for part in metadata.parts
            ],
        }

    @application.get("/v1/status", dependencies=[Depends(authorize)])
    async def status() -> dict[str, Any]:
        active = sync_sessdata_from_cookie_file(current_settings())
        if active is not current_settings():
            replace_settings(active)
        report = run_doctor(active).to_dict()
        report["limits"] = {
            "workers": active.job_workers,
            "queue_size": active.job_queue_size,
            "max_claims": active.max_claims,
            "max_searches_per_claim": active.max_searches_per_claim,
            "max_searches_per_run": active.max_searches_per_run,
        }
        report["authentication_required"] = bool(active.api_token)
        report["setup"] = setup_status(active)
        report["bilibili"] = await asyncio.to_thread(
            public_login_state, active
        )
        report["presets"] = {
            "fast": {
                "label": "快速审阅",
                "max_claims": min(active.max_claims, 6),
                "max_searches_per_claim": min(
                    active.max_searches_per_claim, 1
                ),
                "max_searches_per_run": min(
                    active.max_searches_per_run, 6
                ),
            },
            "balanced": {
                "label": "均衡核查",
                "max_claims": min(active.max_claims, 10),
                "max_searches_per_claim": min(
                    active.max_searches_per_claim, 2
                ),
                "max_searches_per_run": min(
                    active.max_searches_per_run, 20
                ),
            },
            "strict": {
                "label": "严格核查",
                "max_claims": active.max_claims,
                "max_searches_per_claim": active.max_searches_per_claim,
                "max_searches_per_run": active.max_searches_per_run,
            },
        }
        return report

    @application.post(
        "/v1/analyze", status_code=202, dependencies=[Depends(authorize)]
    )
    async def analyze(request: AnalyzeRequest) -> dict[str, Any]:
        try:
            return jobs.submit(request.model_dump(mode="json"))
        except QueueFullError as exc:
            raise HTTPException(429, str(exc)) from exc

    @application.get("/v1/jobs", dependencies=[Depends(authorize)])
    async def list_jobs(
        limit: int = Query(default=50, ge=1, le=200)
    ) -> dict[str, Any]:
        return {"jobs": jobs.list(limit=limit)}

    @application.get("/v1/jobs/{job_id}", dependencies=[Depends(authorize)])
    async def get_job(job_id: str) -> dict[str, Any]:
        return find_job(job_id)

    @application.post(
        "/v1/jobs/{job_id}/cancel", dependencies=[Depends(authorize)]
    )
    async def cancel_job(job_id: str) -> dict[str, Any]:
        find_job(job_id)
        return jobs.cancel(job_id)

    @application.post(
        "/v1/jobs/{job_id}/retry",
        status_code=202,
        dependencies=[Depends(authorize)],
    )
    async def retry_job(job_id: str) -> dict[str, Any]:
        find_job(job_id)
        try:
            return jobs.retry(job_id)
        except QueueFullError as exc:
            raise HTTPException(429, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get(
        "/v1/jobs/{job_id}/report", dependencies=[Depends(authorize)]
    )
    async def get_report(job_id: str) -> JSONResponse:
        find_job(job_id)
        try:
            path = jobs.report_path(job_id, "json")
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    @application.get(
        "/v1/jobs/{job_id}/report.html", dependencies=[Depends(authorize)]
    )
    async def get_report_html(job_id: str) -> HTMLResponse:
        find_job(job_id)
        try:
            path = jobs.report_path(job_id, "html")
        except FileNotFoundError as exc:
            raise HTTPException(409, str(exc)) from exc
        return HTMLResponse(path.read_text(encoding="utf-8"))

    web_dir = Path(__file__).resolve().parent.parent / "web_dist"

    @application.get("/")
    async def index() -> FileResponse:
        path = web_dir / "index.html"
        if not path.exists():
            raise HTTPException(404, "web UI is not installed")
        return FileResponse(
            path,
            headers={"Cache-Control": "no-store"},
        )

    return application


def main() -> None:
    import uvicorn

    uvicorn.run(
        "bili_fact_checker.api.app:create_app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        factory=True,
    )
