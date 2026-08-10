# 与同类工具对比

| 维度 | bili-fact-checker | Bilitato 验真 | bili2text / VideoCaptioner |
|---|---|---|---|
| 主问题 | 口播声明是否可举证 | 边看边 AI 自评 | 视频 → 文字/字幕 |
| B站接入 | CC API + 可选 ASR | 页面注入字幕 | 下载 + ASR |
| 证据 | Google ClaimReview + 可选网页搜索 | 基本无联网证据（Beta） | 无核查 |
| 输出 | report.json/md/html，标签分清来源 | 插件侧栏 | txt/srt |
| 形态 | CLI / Skill / 本地 API | Chrome 扩展 | CLI / 桌面管道 |
| 许可 | MIT（不捆绑 GPL 字幕框架） | 开源插件 | 各项目自有许可 |

一句话：他们解决「看懂/转写」，我们解决「声明 + 证据标签」。
