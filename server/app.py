"""Local FastAPI server for blog / simple frontend."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bili_fact_checker.config import Settings
from bili_fact_checker.pipeline import run_pipeline
from bili_fact_checker.report import to_html

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
JOBS_DIR = ROOT / "jobs"
JOBS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="bili-fact-checker", version="0.1.0")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class AnalyzeRequest(BaseModel):
    bvid: str = Field(..., description="BV id or bilibili URL")
    tasks: list[str] = Field(default_factory=lambda: ["summary", "verify"])
    lang: str = "zh-CN"
    asr: bool = True
    page: int = Field(default=1, ge=1)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "status": "queued", "created_at": time.time()}

    def worker() -> None:
        with _lock:
            _jobs[job_id]["status"] = "running"
        try:
            settings = Settings.from_env()
            logs: list[str] = []

            def log(m: str) -> None:
                logs.append(m)

            report = run_pipeline(
                settings,
                req.bvid,
                tasks=req.tasks,
                lang=req.lang,
                asr=req.asr,
                page=req.page,
                log=log,
            )
            job_dir = JOBS_DIR / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "report.json").write_text(
                __import__("json").dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (job_dir / "report.html").write_text(to_html(report), encoding="utf-8")
            with _lock:
                _jobs[job_id].update(
                    {
                        "status": "done",
                        "report": report,
                        "logs": logs,
                        "html_path": str(job_dir / "report.html"),
                    }
                )
        except Exception as e:
            with _lock:
                _jobs[job_id].update({"status": "error", "error": str(e)})

    threading.Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        # try disk
        path = JOBS_DIR / job_id / "report.json"
        if path.exists():
            import json

            return JSONResponse(
                {"id": job_id, "status": "done", "report": json.loads(path.read_text(encoding="utf-8"))}
            )
        raise HTTPException(404, "job not found")
    payload = {k: v for k, v in job.items() if k != "html_path"}
    return JSONResponse(payload)


@app.get("/v1/jobs/{job_id}/report.html")
def get_job_html(job_id: str) -> HTMLResponse:
    path = JOBS_DIR / job_id / "report.html"
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"))
    with _lock:
        job = _jobs.get(job_id)
    if job and job.get("report"):
        return HTMLResponse(to_html(job["report"]))
    raise HTTPException(404, "report not ready")


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "web UI missing")
    return FileResponse(index_path)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
