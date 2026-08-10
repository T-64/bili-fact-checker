# bili-fact-checker

把 B 站视频变成**可核对的口播分析报告**：总结内容、抽出可核查声明，并尽量附上外部证据。

```text
BV / 链接
  → 拿字幕（优先官方 CC/AI；没有再本地转写或导入外部字幕）
  → LLM 总结 + 抽声明
  → Google Fact Check / 网页搜索举证
  → report.json / report.md / report.html
```

报告里每条声明会标明证据层级，避免把「模型猜测」写成「已核实」：

| 标签 | 含义 |
|---|---|
| `sourced_factcheck` | 命中 Google Fact Check（ClaimReview） |
| `sourced_web` | 有网页检索证据 |
| `model_inference` | 没有外部证据，仅模型推断（只能当线索） |

输出是**辅助线索**，不是权威裁决。

---

## 依赖怎么配

分三层。**有字幕的视频**其实可以只装基础依赖。

### 1. 必装（跑通「有 CC 的视频」）

```bash
git clone https://github.com/T-64/bili-fact-checker.git
cd bili-fact-checker
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 凭证（勿提交进 git）
export BILI_SESSDATA='你的SESSDATA'          # 或写入 ~/.config/bili/SESSDATA
# 注意：很多视频的 CC/AI 字幕必须登录才能通过 API 拉取。
# 若 bili-fact-checker list 显示 NOT LOGGED IN / need_login_subtitle，
# 说明 Cookie 过期——请从浏览器更新 SESSDATA（不要当成「视频没字幕」）。
export OPENAI_API_KEY='...'                  # 也可用 GLM_API_KEY / ~/.hermes/.env
export OPENAI_API_BASE='https://api.z.ai/api/paas/v4'   # OpenAI 兼容；可改
export OPENAI_MODEL='glm-4-flash'
```

还需要本机有 **`yt-dlp`**、**`ffmpeg`**（无字幕走 ASR 或你手动下音频时用到；有 CC 时字幕链路主要靠 B 站 API）。

建议再配：

```bash
export GOOGLE_FACTCHECK_API_KEY='...'   # 声明核查 Tier 0
# 二选一，提升中文声明命中率：
export SEARXNG_URL='http://127.0.0.1:8080'
# 或 export TAVILY_API_KEY='...'
export HTTPS_PROXY='http://127.0.0.1:7890'  # 国内访问 Google / 部分 API 时常需要
```

完整变量列表见 [`.env.example`](.env.example)。

### 2. 本地 Whisper（可选）

多数 B 站视频已有 CC / AI 字幕，**不必装 Whisper**。只有「没字幕」时才会用到本机转写。

若要用本机转写，语音识别在**你自己的电脑上**跑：

| 组件 | 作用 | 怎么装 |
|---|---|---|
| `faster-whisper` | ASR 引擎（基于 OpenAI Whisper） | `pip install 'bili-fact-checker[asr]'` |
| CTranslate2 模型目录 | 权重（如 large-v3） | 设 `WHISPER_MODEL=/path/to/model` |
| GPU（可选） | 明显加速 | `WHISPER_DEVICE=cuda`（默认）；CPU 可改 `cpu` |

```bash
pip install -e ".[asr]"
export WHISPER_MODEL="$HOME/whisper-model"   # CTranslate2 格式目录
export WHISPER_DEVICE=cuda                   # 无显卡改为 cpu
export WHISPER_COMPUTE_TYPE=float16          # CPU 常用 int8
```

没装 `faster-whisper`、或模型路径不存在时：有 CC 照常跑；无 CC 会报错并提示改用下面的外部字幕方案。

### 3. 无字幕且没有本机 Whisper 时

字幕获取顺序：

1. B 站自带 CC / AI 字幕（默认路径，不需要 Whisper）
2. （可选）本机 faster-whisper
3. **外部字幕文件**：用别的工具先转好，再喂进来

例如用 [VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner) 生成 `.srt`：

```bash
bili-fact-checker run "BVxxxxxxxx" --transcript ./video.srt --print-md
```

也支持 `.txt` 和本工具的 transcript `.json`。

> 本仓库不内置、不 import VideoCaptioner（GPL-3.0）；它只是可选的外部转写方式。

---

## 用法

```bash
# 全流程：总结 + 核查
bili-fact-checker run "BVxxxxxxxx" --print-md
# → output/BVxxxxxxxx/report.{json,md,html}

bili-fact-checker list "BVxxxxxxxx"                 # 看有哪些字幕轨
bili-fact-checker subtitle "BVxxxxxxxx" -o out.srt  # 只导出字幕
bili-fact-checker summarize "BVxxxxxxxx"
bili-fact-checker verify "BVxxxxxxxx"

# 强制不用本机 ASR（没有 CC 就会报错并提示 --transcript）
bili-fact-checker run "BVxxxxxxxx" --no-asr

# 外部字幕
bili-fact-checker run "BVxxxxxxxx" --transcript ./from-videocaptioner.srt
```

### 本地网页 / API（可挂到 blog）

```bash
uvicorn server.app:app --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
# POST /v1/analyze  {"bvid":"BVxxx","tasks":["summary","verify"]}
# GET  /v1/jobs/{id}
```

自托管后把链接写进 blog 即可，见 [`docs/blog-integration.md`](docs/blog-integration.md)。

### Agent Skill

把 [`skills/bili-fact-checker/`](skills/bili-fact-checker/) 拷进 Cursor / Claude Code 的 skills 目录。

---

## 项目结构

```text
src/bili_fact_checker/   # 核心库（ingest / analyze / evidence / report）
server/                  # FastAPI
web/                     # 极简前端
skills/bili-fact-checker/
examples/                # 示例报告
```

---

## 致谢

运行时依赖与参考：

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 无字幕时下载音频
- [OpenAI Whisper](https://github.com/openai/whisper) / [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) — 本机 ASR
- [VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner) — 可选的外部字幕生成工具（不捆绑进本仓库）

---

## 许可与免责

MIT。第三方组件遵循各自许可证。

模型与检索都可能出错或过时；请点开源链接人工核对。不要把 `model_inference` 当成已核实事实。
