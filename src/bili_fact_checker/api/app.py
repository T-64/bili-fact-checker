"""Loopback-first FastAPI application."""

from __future__ import annotations

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
from bili_fact_checker.config import Settings
from bili_fact_checker.diagnostics import run_doctor


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bvid: str = Field(min_length=5, max_length=300)
    tasks: list[str] = Field(default_factory=lambda: ["summary", "verify"])
    lang: str = Field(default="zh-CN", max_length=20)
    asr: bool = True
    page: int = Field(default=1, ge=1, le=10_000)

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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if manager is None:
            jobs.shutdown(wait=False)

    application = FastAPI(
        title="bili-fact-checker",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = configured
    application.state.jobs = jobs

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        if not configured.api_token:
            return
        expected = f"Bearer {configured.api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "missing or invalid bearer token")

    def find_job(job_id: str) -> dict[str, Any]:
        try:
            return jobs.get(job_id)
        except JobNotFoundError as exc:
            raise HTTPException(404, "job not found") from exc

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @application.get("/v1/status", dependencies=[Depends(authorize)])
    async def status() -> dict[str, Any]:
        report = run_doctor(configured).to_dict()
        report["limits"] = {
            "workers": configured.job_workers,
            "queue_size": configured.job_queue_size,
            "max_claims": configured.max_claims,
            "max_searches_per_claim": configured.max_searches_per_claim,
            "max_searches_per_run": configured.max_searches_per_run,
        }
        report["authentication_required"] = bool(configured.api_token)
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
        return FileResponse(path)

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
