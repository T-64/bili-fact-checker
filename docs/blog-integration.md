# Blog 接入

本项目假设 **API 自托管**，blog 只做入口链接。

## 最小步骤

1. 在一台能访问 B站 / LLM /（可选）搜索的机器上：

```bash
cd bili-fact-checker
pip install .
export BILI_SESSDATA=... OPENAI_API_KEY=...
export BFC_API_TOKEN='至少32个字符的强随机令牌'
bili-fact-checker serve
```

2. 用 Tailscale serve / Caddy / 反代把 `8765` 暴露给你自己（建议
   tailnet-only）。非回环监听必须设置至少 32 个字符的 `BFC_API_TOKEN`，
   否则服务会拒绝启动；不要公网裸奔凭证。

3. Blog 文章或导航里放：

```html
<a href="https://your-host/">B站口播事实核查</a>
```

用户打开后即见 `web/index.html`：贴 BV → 出报告。

## 仅嵌入报告

若已有 `job_id` 或静态 `report.html`，可 iframe：

```html
<iframe src="https://your-host/v1/jobs/{job_id}/report.html" style="width:100%;min-height:70vh;border:0"></iframe>
```

或把 CLI 生成的 `output/<bvid>/report.html` 当静态页托管。
