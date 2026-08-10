# bili-fact-checker

B站专用**口播内容分析 + 可举证事实核查**开源工具。

输入 BV 号 → 字幕（CC / 本地 Whisper）→ 总结 & 声明抽取 → Google Fact Check + 网页证据 → 报告（JSON / Markdown / HTML）。

> **定位**：辅助线索，不是真理机。报告必须区分 `sourced_factcheck` / `sourced_web` / `model_inference`。

## 和 Bilitato「验真」的差别

| | Bilitato 验真 Beta | bili-fact-checker |
|---|---|---|
| 形态 | Chrome 插件 | CLI / Agent Skill / 本地 API + 轻量页 |
| 证据 | 多为 LLM 自评（官方也写明缺联网） | ClaimReview + 可选网页搜索，无证据必须标 `model_inference` |
| 目标用户 | 边看边用 | Agent / 开发者；也可自托管后挂 blog 入口 |

## 快速开始

```bash
git clone https://github.com/T-64/bili-fact-checker.git
cd bili-fact-checker
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[asr,dev]"   # asr 可选：本地 Whisper

cp .env.example .env   # 或直接导出环境变量
# 至少需要：
#   BILI_SESSDATA 或 ~/.config/bili/SESSDATA
#   OPENAI_API_KEY / GLM_API_KEY（OpenAI 兼容，默认 z.ai）
# 建议：
#   GOOGLE_FACTCHECK_API_KEY
#   SEARXNG_URL 或 TAVILY_API_KEY（中文声明网页证据）

bili-fact-checker run "BVxxxxxxxx" --print-md
# → output/BVxxxxxxxx/report.{json,md,html}
```

### CLI

```bash
bili-fact-checker list "BVxxxxxxxx"
bili-fact-checker subtitle "BVxxxxxxxx" -o out.srt
bili-fact-checker summarize "BVxxxxxxxx"
bili-fact-checker verify "BVxxxxxxxx"
bili-fact-checker run "BVxxxxxxxx" --tasks summary,verify
```

### 本地 API + 报告页（可挂 blog）

```bash
pip install -e .
uvicorn server.app:app --host 127.0.0.1 --port 8765
# 打开 http://127.0.0.1:8765
# POST /v1/analyze  {"bvid":"BVxxx","tasks":["summary","verify"]}
# GET  /v1/jobs/{id}
```

Blog 入口：链到你自托管的 `http://your-host:8765/` 即可（tailnet / 反代按你的环境配置）。

### Agent Skill

见 [`skills/bili-fact-checker/SKILL.md`](skills/bili-fact-checker/SKILL.md)，复制到 Cursor / Claude Code skills 目录即可。

## 环境变量

见 [`.env.example`](.env.example)。密钥不要提交进仓库。

| 变量 | 用途 |
|---|---|
| `BILI_SESSDATA` | B站字幕 API |
| `OPENAI_API_KEY` / `GLM_API_KEY` | LLM（也读 `~/.hermes/.env`） |
| `OPENAI_API_BASE` | 默认 `https://api.z.ai/api/paas/v4` |
| `GOOGLE_FACTCHECK_API_KEY` | Tier 0 ClaimReview |
| `SEARXNG_URL` / `TAVILY_API_KEY` | Tier 1 网页证据 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 默认 `http://127.0.0.1:7890` |
| `WHISPER_MODEL` | 本地 CTranslate2 模型路径 |

## 架构

```text
BV → ingest(CC|ASR) → analyze(summary,claims) → evidence(FC+web) → judge → report
```

Python 包：`bili_fact_checker`。核心逻辑只在库里；CLI / FastAPI / Skill 都是薄壳。

## Credits

本项目的字幕/ASR 能力站在这些开源项目之上，**运行时依赖**请一并致谢：

- [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) — 音频下载（`--asr`）
- [openai/whisper](https://github.com/openai/whisper) — Whisper ASR 原版
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CUDA 加速转写（本项目默认）
- [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) — faster-whisper 推理后端

**参考与致谢（不作为本仓库 GPL 依赖捆绑）**：

- [WEIFENG2333/VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner)（卡卡字幕助手，GPL-3.0）— 作者本地字幕全管道曾参考并 patch 此项目；本仓库 **不 import** VideoCaptioner，以免 GPL 传染。字幕生成能力向其致谢。

**Prior art / 同类**：

- [lanbinleo/bili2text](https://github.com/lanbinleo/bili2text) — B站转文字 CLI
- [erikzhuang55/Bilitato](https://github.com/erikzhuang55/Bilitato) — B站观看助手与验真 Beta（证据层不同）

## 许可

MIT。第三方依赖遵循各自许可证（VideoCaptioner 为 GPL-3.0，故未捆绑）。

## 免责声明

输出可能错误或过时。请人工核对来源。不要把 `model_inference` 当成已核实事实。
