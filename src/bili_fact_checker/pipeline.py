"""End-to-end pipeline."""

from __future__ import annotations

from typing import Any, Callable

from bili_fact_checker.analyze import extract_claims, summarize
from bili_fact_checker.config import Settings
from bili_fact_checker.evidence import verify_claims
from bili_fact_checker.ingest import fetch_transcript
from bili_fact_checker.report import build_report


LogFn = Callable[[str], None]


def run_pipeline(
    settings: Settings,
    url_or_bvid: str,
    *,
    tasks: list[str] | None = None,
    lang: str = "zh-CN",
    asr: bool = True,
    transcript_file: str | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    tasks = tasks or ["summary", "verify"]
    _log = log or (lambda _m: None)

    if transcript_file:
        _log(f"ingest: loading external transcript {transcript_file}…")
        from bili_fact_checker.ingest import load_transcript_file

        transcript = load_transcript_file(settings, url_or_bvid, transcript_file)
    else:
        _log("ingest: fetching transcript…")
        transcript = fetch_transcript(settings, url_or_bvid, lang=lang, asr=asr)
    _log(f"ingest: {transcript.bvid} · {transcript.title} · {transcript.source} · {len(transcript.text)} chars")

    summary_text: str | None = None
    claims: list[dict[str, Any]] = []

    if "summary" in tasks:
        _log("analyze: summarizing…")
        summary_text = summarize(settings, transcript)

    need_claims = "verify" in tasks or "claims" in tasks
    if need_claims:
        _log("analyze: extracting claims…")
        claims = extract_claims(settings, transcript)
        _log(f"analyze: {len(claims)} claims")

    if "verify" in tasks and claims:
        _log("evidence: verifying claims…")
        claims = verify_claims(settings, claims)

    report = build_report(
        transcript=transcript.to_dict(),
        summary=summary_text,
        claims=claims,
        tasks=tasks,
    )
    report["transcript"] = transcript.to_dict()
    return report
