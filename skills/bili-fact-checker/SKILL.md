---
name: bili-fact-checker
description: >-
  Analyze Bilibili video oral content and run evidence-backed fact checks.
  Use when the user provides a Bilibili BV id/URL and wants a transcript,
  summary, checkable claims, or a sourced fact-check report.
---

# bili-fact-checker

B站口播内容分析与可举证事实核查。差异化：报告必须区分 **有出处 / 模型推断 / 未找到证据**。

## When to use

- User pastes a `BVxxxx` or `bilibili.com/video/...` link
- Asks to summarize, extract claims, or fact-check spoken claims in a B站 video

## Prerequisites

Environment (see repo `.env.example`):

- `BILI_SESSDATA` or `~/.config/bili/SESSDATA`
- `OPENAI_API_KEY` / `GLM_API_KEY` (OpenAI-compatible)
- `GOOGLE_FACTCHECK_API_KEY` (optional but recommended)
- Optional: `SEARXNG_URL` or `TAVILY_API_KEY` for web evidence
- Optional ASR: `faster-whisper` + local model at `WHISPER_MODEL`

## Commands

Prefer the installed CLI from the repo:

```bash
# full pipeline → output/<bvid>/report.{json,md,html}
bili-fact-checker run "BVxxxxxxxx" --print-md

# subtitle only
bili-fact-checker subtitle "BVxxxxxxxx" -o out.srt

# summary only
bili-fact-checker summarize "BVxxxxxxxx"

# verify claims
bili-fact-checker verify "BVxxxxxxxx"
```

Or HTTP (local server):

```bash
cd /path/to/bili-fact-checker
python -m uvicorn server.app:app --host 127.0.0.1 --port 8765

curl -X POST http://127.0.0.1:8765/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"bvid":"BVxxxxxxxx","tasks":["summary","verify"]}'
```

## Output contract

- `report.json`: machine-readable schema `0.1`
- Each claim has `judgment.label` in:
  - `sourced_factcheck` — Google Fact Check Tools hit
  - `sourced_web` — web search evidence
  - `model_inference` — no external evidence (must not be presented as verified)
- Always surface the disclaimer in the report

## Notes

- Do not import VideoCaptioner into this project (GPL). Credits thank it as prior captioning tooling.
- This tool produces **leads**, not final truth.
