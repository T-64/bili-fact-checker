"""Report rendering: JSON / Markdown / HTML."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any


def build_report(
    *,
    transcript: dict[str, Any],
    summary: str | None,
    claims: list[dict[str, Any]],
    tasks: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "本工具输出仅为辅助线索，不是权威最终裁决。"
            "请核对来源链接；标为 model_inference 的条目无外部举证。"
        ),
        "tasks": tasks,
        "video": {
            "bvid": transcript.get("bvid"),
            "title": transcript.get("title"),
            "aid": transcript.get("aid"),
            "cid": transcript.get("cid"),
            "subtitle_source": transcript.get("source"),
            "language": transcript.get("language"),
            "char_count": len(transcript.get("text") or ""),
            "url": f"https://www.bilibili.com/video/{transcript.get('bvid')}",
        },
        "summary": summary or "",
        "claims": claims,
        "stats": _stats(claims),
    }


def _stats(claims: list[dict[str, Any]]) -> dict[str, int]:
    sourced = 0
    inference = 0
    unverified = 0
    for c in claims:
        j = c.get("judgment") or {}
        label = j.get("label") or ""
        verdict = j.get("verdict") or ""
        if label == "model_inference":
            inference += 1
        elif label.startswith("sourced"):
            sourced += 1
        if verdict in ("unverified", "") and not c.get("has_sourced_evidence"):
            unverified += 1
    return {
        "claim_count": len(claims),
        "sourced": sourced,
        "model_inference": inference,
        "unverified_or_weak": unverified,
    }


def to_markdown(report: dict[str, Any]) -> str:
    v = report.get("video") or {}
    lines = [
        "# B站口播事实核查报告",
        "",
        f"**视频**: [{v.get('title')}]({v.get('url')})",
        f"**BV**: `{v.get('bvid')}` · 字幕来源: `{v.get('subtitle_source')}`",
        f"**生成时间**: {report.get('generated_at')}",
        "",
        f"> {report.get('disclaimer')}",
        "",
    ]
    if report.get("summary"):
        lines.extend(["## 内容总结", "", report["summary"].strip(), ""])

    claims = report.get("claims") or []
    lines.append(f"## 声明核查（{len(claims)}）")
    lines.append("")
    stats = report.get("stats") or {}
    lines.append(
        f"汇总：有出处 {stats.get('sourced', 0)} · "
        f"模型推断 {stats.get('model_inference', 0)} · "
        f"弱/未核实 {stats.get('unverified_or_weak', 0)}"
    )
    lines.append("")

    for i, c in enumerate(claims, 1):
        j = c.get("judgment") or {}
        label = j.get("label") or "unknown"
        verdict = j.get("verdict") or "unverified"
        lines.append(f"### {i}. [{c.get('type', '')}] `{verdict}` · `{label}`")
        lines.append(f"**声明**: {c.get('claim_zh')}")
        if c.get("claim_en"):
            lines.append(f"**EN**: {c['claim_en']}")
        if c.get("timestamp_sec"):
            lines.append(f"**时间**: {int(c['timestamp_sec'])}s")
        if j.get("rationale"):
            lines.append(f"**理由**: {j['rationale']}")
        sources = j.get("sources") or []
        fc = c.get("google_factcheck") or {}
        if fc.get("url"):
            sources = list(sources) + [fc["url"]]
        # unique preserve order
        seen = set()
        uniq = []
        for u in sources:
            if u and u not in seen:
                seen.add(u)
                uniq.append(u)
        if uniq:
            lines.append("**来源**:")
            for u in uniq:
                lines.append(f"- {u}")
        elif label == "model_inference":
            lines.append("**来源**: （无外部证据 · model_inference）")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_html(report: dict[str, Any]) -> str:
    v = report.get("video") or {}
    claims = report.get("claims") or []
    summary = report.get("summary") or ""

    def badge(label: str, verdict: str) -> str:
        tone = "gray"
        if label.startswith("sourced"):
            tone = "green" if verdict in ("supported", "likely_true") else "amber"
        if verdict in ("refuted", "likely_false"):
            tone = "red"
        if label == "model_inference":
            tone = "gray"
        return f'<span class="badge {tone}">{html.escape(verdict)} · {html.escape(label)}</span>'

    cards = []
    for i, c in enumerate(claims, 1):
        j = c.get("judgment") or {}
        sources = j.get("sources") or []
        fc = c.get("google_factcheck") or {}
        if fc.get("url"):
            sources = list(sources) + [fc["url"]]
        links = "".join(
            f'<li><a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(u)}</a></li>'
            for u in dict.fromkeys([u for u in sources if u])
        )
        cards.append(
            f"""
<article class="claim">
  <header><span class="idx">#{i}</span> {badge(str(j.get('label') or ''), str(j.get('verdict') or ''))}</header>
  <p class="claim-text">{html.escape(str(c.get('claim_zh') or ''))}</p>
  <p class="meta">{html.escape(str(c.get('type') or ''))}
  {" · " + str(int(c['timestamp_sec'])) + "s" if c.get("timestamp_sec") else ""}</p>
  <p class="rationale">{html.escape(str(j.get("rationale") or ""))}</p>
  {"<ul class='sources'>" + links + "</ul>" if links else "<p class='no-src'>无外部证据</p>"}
</article>"""
        )

    summary_html = html.escape(summary).replace("\n", "<br>\n") if summary else "<p class='empty'>（未生成总结）</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>核查报告 · {html.escape(str(v.get('title') or v.get('bvid') or ''))}</title>
<style>
:root {{ --bg:#0f1115; --panel:#171a21; --text:#e8eaed; --muted:#9aa0a6; --line:#2a2f3a;
  --green:#3d8b6e; --red:#a85a5a; --amber:#a88a4a; --gray:#6b7280; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family: ui-sans-serif, system-ui, sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }}
main {{ max-width:820px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:1.5rem; margin:0 0 8px; }}
.sub {{ color:var(--muted); font-size:.95rem; margin-bottom:24px; }}
.callout {{ border:1px solid var(--line); background:var(--panel); padding:12px 14px; border-radius:8px; color:var(--muted); font-size:.9rem; }}
section {{ margin-top:28px; }}
h2 {{ font-size:1.15rem; border-bottom:1px solid var(--line); padding-bottom:8px; }}
.claim {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:12px 0; }}
.claim header {{ display:flex; gap:10px; align-items:center; margin-bottom:8px; }}
.idx {{ color:var(--muted); font-size:.85rem; }}
.badge {{ font-size:.75rem; padding:2px 8px; border-radius:999px; border:1px solid var(--line); }}
.badge.green {{ color:#b6e2d0; border-color:var(--green); }}
.badge.red {{ color:#f0c4c4; border-color:var(--red); }}
.badge.amber {{ color:#ead7a8; border-color:var(--amber); }}
.badge.gray {{ color:#c5c9d0; border-color:var(--gray); }}
.claim-text {{ margin:0 0 6px; font-weight:600; }}
.meta, .rationale, .no-src {{ color:var(--muted); font-size:.9rem; }}
.sources {{ margin:8px 0 0; padding-left:18px; }}
.sources a {{ color:#8ab4f8; }}
a {{ color:#8ab4f8; }}
</style>
</head>
<body>
<main>
  <h1>B站口播事实核查</h1>
  <p class="sub"><a href="{html.escape(str(v.get('url') or '#'))}">{html.escape(str(v.get('title') or ''))}</a>
  · {html.escape(str(v.get('bvid') or ''))} · 字幕 {html.escape(str(v.get('subtitle_source') or ''))}</p>
  <div class="callout">{html.escape(str(report.get('disclaimer') or ''))}</div>
  <section>
    <h2>内容总结</h2>
    <div>{summary_html}</div>
  </section>
  <section>
    <h2>声明核查（{len(claims)}）</h2>
    {''.join(cards) if cards else "<p class='empty'>无声明</p>"}
  </section>
</main>
</body>
</html>
"""


def dumps_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
