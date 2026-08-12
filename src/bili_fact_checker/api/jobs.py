"""Bounded, persistent analysis jobs shared by the API and future UI."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bili_fact_checker.config import Settings
from bili_fact_checker.pipeline import PipelineCancelled, run_pipeline
from bili_fact_checker.report import dumps_json, to_html, to_markdown


class QueueFullError(RuntimeError):
    pass


class JobNotFoundError(KeyError):
    pass


Runner = Callable[..., dict[str, Any]]
_JOB_ID = re.compile(r"^[0-9a-f]{12}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner = run_pipeline,
    ) -> None:
        self.settings = settings
        self.root = settings.data_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self._runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._capacity = threading.BoundedSemaphore(
            settings.job_workers + settings.job_queue_size
        )
        self._executor = ThreadPoolExecutor(
            max_workers=settings.job_workers,
            thread_name_prefix="bfc-job",
        )
        self._recover()

    def _job_dir(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise JobNotFoundError(job_id)
        return self.root / job_id

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.json"

    def _write_json_atomic(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _persist(self, job: dict[str, Any]) -> None:
        self._write_json_atomic(self._state_path(str(job["id"])), job)

    def _recover(self) -> None:
        for path in sorted(self.root.glob("*/state.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                job_id = str(job["id"])
                self._job_dir(job_id)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if job.get("status") in {"queued", "running", "cancelling"}:
                job["status"] = "interrupted"
                job["error"] = "服务曾在任务完成前停止；可以重试该任务。"
                job["updated_at"] = _now()
                self._persist(job)
            self._jobs[job_id] = job

    def _safe_error(self, error: Exception) -> str:
        message = str(error) or error.__class__.__name__
        for secret in (
            self.settings.openai_api_key,
            self.settings.search_api_key,
            self.settings.tavily_api_key,
            self.settings.sessdata,
            self.settings.api_token,
        ):
            if secret:
                message = message.replace(secret, "[redacted]")
        return message[:2000]

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._capacity.acquire(blocking=False):
            raise QueueFullError(
                f"job queue is full ({self.settings.job_workers} running + "
                f"{self.settings.job_queue_size} queued)"
            )
        job_id = uuid.uuid4().hex[:12]
        now = _now()
        job = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "created_at": now,
            "updated_at": now,
            "request": copy.deepcopy(request),
            "logs": [],
            "report_url": f"/v1/jobs/{job_id}/report",
            "report_html_url": f"/v1/jobs/{job_id}/report.html",
        }
        event = threading.Event()
        try:
            with self._lock:
                self._jobs[job_id] = job
                self._cancel_events[job_id] = event
                self._persist(job)
            self._executor.submit(self._run, job_id)
        except Exception:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._cancel_events.pop(job_id, None)
            self._capacity.release()
            raise
        return self.get(job_id)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            job["updated_at"] = _now()
            self._persist(job)

    def _run(self, job_id: str) -> None:
        event = self._cancel_events[job_id]
        try:
            if event.is_set():
                raise PipelineCancelled("analysis cancelled")
            self._update(job_id, status="running", stage="ingest")

            def log(message: str) -> None:
                if event.is_set():
                    raise PipelineCancelled("analysis cancelled")
                stage = message.split(":", 1)[0] if ":" in message else "running"
                with self._lock:
                    job = self._jobs[job_id]
                    job["logs"] = [*job.get("logs", []), message][-100:]
                    job["stage"] = stage
                    job["updated_at"] = _now()
                    self._persist(job)

            request = copy.deepcopy(self._jobs[job_id]["request"])
            report = self._runner(
                self.settings,
                request["bvid"],
                tasks=request.get("tasks"),
                lang=request.get("lang", "zh-CN"),
                asr=bool(request.get("asr", True)),
                page=int(request.get("page", 1)),
                log=log,
                cancel_check=event.is_set,
            )
            if event.is_set():
                raise PipelineCancelled("analysis cancelled")
            job_dir = self._job_dir(job_id)
            self._write_json_atomic(
                job_dir / "report.json",
                json.loads(dumps_json(report)),
            )
            (job_dir / "report.md").write_text(
                to_markdown(report), encoding="utf-8"
            )
            (job_dir / "report.html").write_text(
                to_html(report), encoding="utf-8"
            )
            self._update(job_id, status="done", stage="done", error="")
        except PipelineCancelled:
            self._update(
                job_id,
                status="cancelled",
                stage="cancelled",
                error="任务已由用户取消。",
            )
        except Exception as exc:
            self._update(
                job_id,
                status="error",
                stage="error",
                error=self._safe_error(exc),
            )
        finally:
            with self._lock:
                self._cancel_events.pop(job_id, None)
            self._capacity.release()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return copy.deepcopy(job)

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            return copy.deepcopy(jobs[: max(1, min(limit, 200))])

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if job["status"] not in {"queued", "running", "cancelling"}:
                return copy.deepcopy(job)
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
            job["status"] = "cancelling"
            job["stage"] = "cancelling"
            job["updated_at"] = _now()
            self._persist(job)
            return copy.deepcopy(job)

    def retry(self, job_id: str) -> dict[str, Any]:
        previous = self.get(job_id)
        if previous["status"] in {"queued", "running", "cancelling"}:
            raise ValueError("cannot retry an active job")
        return self.submit(copy.deepcopy(previous["request"]))

    def report_path(self, job_id: str, suffix: str) -> Path:
        job = self.get(job_id)
        if job["status"] != "done":
            raise FileNotFoundError("report is not ready")
        path = self._job_dir(job_id) / f"report.{suffix}"
        if not path.exists():
            raise FileNotFoundError("report is missing")
        return path

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=wait, cancel_futures=False)
