# bili-fact-checker

把 B 站视频变成**可核对的口播分析报告**：总结内容、抽出可核查声明，并尽量附上外部证据。

```text
BV / 链接
  → 拿字幕（优先官方 CC/AI；没有再本地转写或导入外部字幕）
  → LLM 总结 + 抽声明
  → 复用 AI 提供商的原生搜索发现候选网页
  → 抓取真实网页并保留精确证据引文
  → report.json / report.md / report.html
```

报告使用四种保守结论：

| 结论 | 含义 |
|---|---|
| `supported` | 支持证据达到门槛，且无同等级反向证据 |
| `refuted` | 反驳证据达到门槛，且无同等级支持证据 |
| `disputed` | 可信的支持和反驳材料同时存在 |
| `insufficient_evidence` | 没有证据，或证据尚未达到自动裁决门槛 |

搜索摘要和模型记忆永远不算证据。输出是**辅助审阅报告**，不是权威裁决。

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

默认 `SEARCH_PROVIDER=auto`。使用 Z.AI/智谱端点时，核查流程会复用上面
同一套 API base/key 调用结构化 Web Search，不要求再申请一套搜索服务。
原生联网搜索可能被供应商单独计费，请在供应商控制台核对规则。

同一账号也支持 OpenAI、Gemini 和 Anthropic 的原生 LLM + 搜索协议；配置
示例、自动识别边界和外部搜索回退见 [`docs/providers.md`](docs/providers.md)。

只有无字幕走本地 ASR 时才需要 **`yt-dlp`**、**`ffmpeg`**；有 CC 时字幕
链路主要靠 B 站 API。`yt-dlp` 会随 `[asr]` extra 安装，`ffmpeg` 仍由系统安装。

只有高级或自托管场景才需要额外搜索配置：

```bash
export SEARCH_PROVIDER=searxng
export SEARXNG_URL='http://127.0.0.1:8080'
# 或 SEARCH_PROVIDER=tavily + TAVILY_API_KEY='...'
# 需要代理时再自行设置 HTTP_PROXY / HTTPS_PROXY；项目没有隐式代理。
```

完整变量列表见 [`.env.example`](.env.example)。

配置后先运行本地诊断；它只检查配置和文件，**不会调用模型或搜索 API**：

```bash
bili-fact-checker doctor
```

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

# 多 P 视频选择第二个分 P
bili-fact-checker run "BVxxxxxxxx" --page 2

# 强制不用本机 ASR（没有 CC 就会报错并提示 --transcript）
bili-fact-checker run "BVxxxxxxxx" --no-asr

# 外部字幕
bili-fact-checker run "BVxxxxxxxx" --transcript ./from-videocaptioner.srt
```

### 本地网页 / API（可挂到 blog）

```bash
bili-fact-checker serve
# 浏览器打开 http://127.0.0.1:8765
# POST /v1/analyze  {"bvid":"BVxxx","tasks":["summary","verify"]}
# GET  /v1/jobs/{id}
```

服务默认只监听 `127.0.0.1`。任务由有界队列执行并持久化到
`~/.local/share/bili-fact-checker/jobs`；重启时未完成任务会标记为
`interrupted`，可调用 `POST /v1/jobs/{id}/retry` 重试。另有任务历史、取消、
JSON 报告和 HTML 报告接口。若要通过反向代理暴露，先设置强随机
`BFC_API_TOKEN`（至少 32 个字符），请求使用 `Authorization: Bearer ...`。
`serve` 会拒绝在缺少强令牌时监听非回环地址；可用下面的命令生成令牌：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

自托管后把链接写进 blog 即可，见 [`docs/blog-integration.md`](docs/blog-integration.md)。

### Docker / Compose

默认 Compose 不需要单独的搜索服务，仍复用已配置 AI provider 的原生搜索：

```bash
cp .env.example .env
# 填入 key、model、BFC_API_TOKEN 和可选的 BILI_SESSDATA；生成 token：
python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up -d --build
# 仅宿主机可访问：http://127.0.0.1:8765
```

容器内服务监听非回环地址，因此即使 Compose 默认只发布到宿主机回环地址，
也必须配置至少 32 个字符的 `BFC_API_TOKEN`。容器以非 root 用户运行，状态
保存在 `bfc-data` volume。要主动选择自托管
SearXNG 时，再把 `.env` 设为 `SEARCH_PROVIDER=searxng`、
`SEARXNG_URL=http://searxng:8080`，并运行
`docker compose --profile searxng up -d --build`。

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
- [Trafilatura](https://github.com/adbar/trafilatura) — 网页正文和元数据提取

---

## 许可与免责

MIT。第三方组件遵循各自许可证。

模型、检索和来源页面都可能出错或过时。请核对视频原话、精确引文和来源页面；
`insufficient_evidence` 表示系统选择弃权，不表示声明为真或为假。
