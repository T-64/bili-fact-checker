---
name: bili-fact-checker
description: >-
  Analyze Bilibili video oral content and run evidence-backed fact checks.
  Use when the user provides a Bilibili BV id/URL and wants a transcript,
  summary, checkable claims, or a sourced fact-check report.
---

# bili-fact-checker

B站口播内容分析与可举证事实核查。报告必须区分 **sourced_factcheck / sourced_web / model_inference**。

## When to use

- User pastes a `BVxxxx` or `bilibili.com/video/...` link
- Asks to summarize, extract claims, or fact-check spoken claims

## Prerequisites

See repo README / `.env.example`:

- Required: `BILI_SESSDATA`, OpenAI-compatible LLM key
- Recommended: `GOOGLE_FACTCHECK_API_KEY`, `SEARXNG_URL` or `TAVILY_API_KEY`
- Subtitles: B站 CC first; else local `faster-whisper` + `WHISPER_MODEL`; else `--transcript file.srt` (e.g. from VideoCaptioner)

## Commands

```bash
bili-fact-checker run "BVxxxxxxxx" --print-md
bili-fact-checker subtitle "BVxxxxxxxx" -o out.srt
bili-fact-checker run "BVxxxxxxxx" --transcript ./out.srt   # no local Whisper
bili-fact-checker summarize "BVxxxxxxxx"
bili-fact-checker verify "BVxxxxxxxx"
```

Local API:

```bash
uvicorn server.app:app --host 127.0.0.1 --port 8765
```

## Output

- `judgment.label`: `sourced_factcheck` | `sourced_web` | `model_inference`
- Treat results as leads, not final truth. Never present `model_inference` as verified.
