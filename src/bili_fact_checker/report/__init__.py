"""Versioned report construction and JSON / Markdown / HTML rendering."""

from __future__ import annotations

import hashlib
import html
import json
from typing import Any

from bili_fact_checker import __version__
from bili_fact_checker.models import (
    AnalysisEvent,
    AnalysisReport,
    ClaimAnalysis,
    ReportStats,
    RunInfo,
    SearchProviderCapabilities,
    SearchUsage,
    TranscriptInfo,
    TranscriptSegment,
    Verdict,
    VideoInfo,
)


def _stats(claims: list[ClaimAnalysis]) -> ReportStats:
    values = [item.verdict.verdict for item in claims]
    return ReportStats(
        claim_count=len(values),
        supported=values.count(Verdict.SUPPORTED),
        refuted=values.count(Verdict.REFUTED),
        disputed=values.count(Verdict.DISPUTED),
        insufficient_evidence=values.count(Verdict.INSUFFICIENT_EVIDENCE),
    )


def build_report(
    *,
    transcript: dict[str, Any],
    summary: str | None,
    claims: list[ClaimAnalysis],
    model: str,
    search_providers: list[str],
    search_capabilities: list[SearchProviderCapabilities] | None = None,
    search_usage: list[SearchUsage] | None = None,
    events: list[AnalysisEvent] | None = None,
) -> AnalysisReport:
    """Build the single public 1.0 report representation."""

    transcript_text = str(transcript.get("text") or "")
    segments = [
        TranscriptSegment(
            id=str(item.get("id") or f"seg_{index:05d}"),
            start=float(item.get("start") or 0),
            end=float(item.get("end") or 0),
            text=str(item.get("text") or ""),
        )
        for index, item in enumerate(transcript.get("segments") or [], start=1)
        if str(item.get("text") or "").strip()
    ]
    bvid = str(transcript.get("bvid") or "")
    source = str(transcript.get("source") or "file")
    return AnalysisReport(
        run=RunInfo(
            software_version=__version__,
            model=model,
            search_providers=search_providers,
            search_capabilities=search_capabilities or [],
            search_usage=search_usage or [],
        ),
        video=VideoInfo(
            bvid=bvid,
            title=str(transcript.get("title") or bvid),
            url=f"https://www.bilibili.com/video/{bvid}",
            aid=str(transcript.get("aid") or ""),
            cid=str(transcript.get("cid") or ""),
        ),
        transcript=TranscriptInfo(
            source=source,
            language=str(transcript.get("language") or "unknown"),
            segments=segments,
            text_sha256=hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(),
        ),
        summary=summary or "",
        claims=claims,
        events=events or [],
        stats=_stats(claims),
    )


def report_dict(report: AnalysisReport | dict[str, Any]) -> dict[str, Any]:
    if isinstance(report, AnalysisReport):
        return report.model_dump(mode="json")
    return report


def _timestamp_link(video_url: str, seconds: int) -> str:
    separator = "&" if "?" in video_url else "?"
    return f"{video_url}{separator}t={seconds}"


def to_markdown(report: AnalysisReport | dict[str, Any]) -> str:
    data = report_dict(report)
    video = data.get("video") or {}
    run = data.get("run") or {}
    stats = data.get("stats") or {}
    lines = [
        "# B站口播证据核查报告",
        "",
        f"**视频**: [{video.get('title')}]({video.get('url')})",
        f"**BV**: `{video.get('bvid')}` · Schema: `{data.get('schema_version')}`",
        f"**生成时间**: {run.get('generated_at')}",
        "",
        f"> {data.get('disclaimer')}",
        "",
    ]
    if data.get("summary"):
        lines.extend(["## 内容总结", "", str(data["summary"]).strip(), ""])

    claims = data.get("claims") or []
    lines.extend(
        [
            f"## 声明核查（{len(claims)}）",
            "",
            (
                f"支持 {stats.get('supported', 0)} · "
                f"反驳 {stats.get('refuted', 0)} · "
                f"存在争议 {stats.get('disputed', 0)} · "
                f"证据不足 {stats.get('insufficient_evidence', 0)}"
            ),
            "",
        ]
    )

    for index, item in enumerate(claims, start=1):
        claim = item.get("claim") or {}
        verdict = item.get("verdict") or {}
        seconds = int(claim.get("timestamp_sec") or 0)
        link = _timestamp_link(str(video.get("url") or ""), seconds)
        lines.extend(
            [
                (
                    f"### {index}. `{verdict.get('verdict')}` · "
                    f"证据强度 `{verdict.get('strength')}`"
                ),
                f"**声明**: {claim.get('claim_zh')}",
                f"**视频原话**: “{claim.get('quote')}”",
                f"**时间**: [{seconds}s]({link})",
                f"**判定依据**: {verdict.get('reason')}",
                "",
            ]
        )
        document_map = {
            value.get("id"): value for value in item.get("documents") or []
        }
        excerpt_map = {
            value.get("id"): value for value in item.get("excerpts") or []
        }
        assessment_map = {
            value.get("excerpt_id"): value
            for value in item.get("assessments") or []
        }
        evidence_ids = list(verdict.get("supporting_excerpt_ids") or [])
        evidence_ids.extend(verdict.get("refuting_excerpt_ids") or [])
        evidence_ids.extend(verdict.get("context_excerpt_ids") or [])
        if evidence_ids:
            lines.append("**已验证引文**:")
            lines.append("")
            for excerpt_id in dict.fromkeys(evidence_ids):
                excerpt = excerpt_map.get(excerpt_id) or {}
                document = document_map.get(excerpt.get("document_id")) or {}
                assessment = assessment_map.get(excerpt_id) or {}
                lines.extend(
                    [
                        (
                            f"- **{assessment.get('stance', 'context')}** — "
                            f"[{document.get('title') or document.get('url')}]"
                            f"({document.get('url')})"
                        ),
                        f"  > {excerpt.get('text')}",
                    ]
                )
        else:
            lines.append("**已验证引文**: 无。搜索结果摘要不计入证据。")
        lines.append("")

    events = data.get("events") or []
    if events:
        lines.extend(["## 审计事件", ""])
        for event in events:
            lines.append(
                f"- `{event.get('level')}` `{event.get('code')}` {event.get('message')}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_html(report: AnalysisReport | dict[str, Any]) -> str:
    data = report_dict(report)
    video = data.get("video") or {}
    claims = data.get("claims") or []
    cards: list[str] = []
    for index, item in enumerate(claims, start=1):
        claim = item.get("claim") or {}
        verdict = item.get("verdict") or {}
        seconds = int(claim.get("timestamp_sec") or 0)
        document_map = {
            value.get("id"): value for value in item.get("documents") or []
        }
        excerpt_map = {
            value.get("id"): value for value in item.get("excerpts") or []
        }
        assessment_map = {
            value.get("excerpt_id"): value
            for value in item.get("assessments") or []
        }
        evidence_ids = list(verdict.get("supporting_excerpt_ids") or [])
        evidence_ids.extend(verdict.get("refuting_excerpt_ids") or [])
        evidence_ids.extend(verdict.get("context_excerpt_ids") or [])
        evidence_html: list[str] = []
        for excerpt_id in dict.fromkeys(evidence_ids):
            excerpt = excerpt_map.get(excerpt_id) or {}
            document = document_map.get(excerpt.get("document_id")) or {}
            assessment = assessment_map.get(excerpt_id) or {}
            evidence_html.append(
                "<li>"
                f"<strong>{html.escape(str(assessment.get('stance') or 'context'))}</strong> "
                f"<a href=\"{html.escape(str(document.get('url') or ''))}\" "
                "target=\"_blank\" rel=\"noopener\">"
                f"{html.escape(str(document.get('title') or document.get('url') or '来源'))}</a>"
                f"<blockquote>{html.escape(str(excerpt.get('text') or ''))}</blockquote>"
                "</li>"
            )
        evidence_body = (
            f"<ul class=\"evidence\">{''.join(evidence_html)}</ul>"
            if evidence_html
            else '<p class="muted">无已验证引文；搜索摘要不计入证据。</p>'
        )
        cards.append(
            f"""
<article class="claim">
  <header><span>#{index}</span><b>{html.escape(str(verdict.get('verdict') or ''))}</b>
  <small>证据强度 {html.escape(str(verdict.get('strength') or 'none'))}</small></header>
  <h3>{html.escape(str(claim.get('claim_zh') or ''))}</h3>
  <p>原话：“{html.escape(str(claim.get('quote') or ''))}” ·
  <a href="{html.escape(_timestamp_link(str(video.get('url') or ''), seconds))}">{seconds}s</a></p>
  <p>{html.escape(str(verdict.get('reason') or ''))}</p>
  {evidence_body}
</article>"""
        )

    summary = html.escape(str(data.get("summary") or "")).replace("\n", "<br>")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>证据核查报告 · {html.escape(str(video.get('title') or ''))}</title>
<style>
:root{{--bg:#f5f2eb;--paper:#fffdf8;--ink:#24231f;--muted:#706d65;--line:#d8d2c5;--accent:#a53a2a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:900px;margin:auto;padding:36px 20px 72px}}a{{color:var(--accent)}}
.intro,.claim{{background:var(--paper);border:1px solid var(--line);padding:18px 20px;margin:16px 0}}
.claim header{{display:flex;gap:12px;align-items:center;color:var(--muted)}}.claim h3{{margin:.7rem 0}}
.evidence{{padding-left:22px}}blockquote{{border-left:3px solid var(--line);margin:.5rem 0 1rem;padding-left:12px}}
.muted,small{{color:var(--muted)}}
</style></head><body><main>
<h1>B站口播证据核查报告</h1>
<p><a href="{html.escape(str(video.get('url') or ''))}">{html.escape(str(video.get('title') or ''))}</a>
· {html.escape(str(video.get('bvid') or ''))}</p>
<div class="intro">{html.escape(str(data.get('disclaimer') or ''))}</div>
<h2>内容总结</h2><div class="intro">{summary or '未生成总结'}</div>
<h2>声明核查（{len(claims)}）</h2>{''.join(cards) or '<p>没有提取到可核查声明。</p>'}
</main></body></html>"""


def dumps_json(report: AnalysisReport | dict[str, Any]) -> str:
    return json.dumps(report_dict(report), ensure_ascii=False, indent=2) + "\n"
