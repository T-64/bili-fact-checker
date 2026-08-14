---
name: bili-fact-checker
description: >-
  Analyze Bilibili video oral content and run evidence-backed fact checks.
  Use when the user provides a Bilibili BV id/URL and wants a transcript,
  summary, checkable claims, or a sourced fact-check report.
---

# bili-fact-checker

B站口播内容分析与可举证事实核查。报告结论只有
`supported` / `refuted` / `disputed` / `insufficient_evidence`。
搜索摘要和模型记忆不是证据。

## When to use

- User pastes a `BVxxxx` or `bilibili.com/video/...` link
- Asks to summarize, extract claims, or fact-check spoken claims

## Prerequisites

One AI provider is enough. Native search reuses that account when the
provider is Z.AI, OpenAI, Gemini, or Anthropic.

```bash
bili-fact-checker setup --save
bili-fact-checker doctor
```

Optional: `BILI_SESSDATA` for login-gated subtitles. Local Whisper is only
needed when a video has no CC/AI captions; otherwise pass `--transcript file.srt`.
SearXNG / Tavily are optional fallbacks, not required.

## Commands

```bash
bili-fact-checker run "BVxxxxxxxx" --print-md
bili-fact-checker subtitle "BVxxxxxxxx" -o out.srt
bili-fact-checker run "BVxxxxxxxx" --transcript ./out.srt
bili-fact-checker summarize "BVxxxxxxxx"
bili-fact-checker verify "BVxxxxxxxx"
bili-fact-checker serve
```

Local UI/API: `http://127.0.0.1:8765`. Do not start `uvicorn server.app:app`.

## Output

- `verdict.verdict`: `supported` | `refuted` | `disputed` | `insufficient_evidence`
- Treat results as an audit aid. Never present `insufficient_evidence` as true
  or false, and never treat search snippets as citations.
