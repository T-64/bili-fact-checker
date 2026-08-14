# Troubleshooting

## 配置尚未就绪

Run `bili-fact-checker doctor`. Missing API key/model is an error. A corrupt
`config.json` is also an error; delete it with `bili-fact-checker setup --clear`
or fix the file.

## Cookie 过期 / 没有字幕

`need_login_subtitle` usually means `SESSDATA` expired, not that the video has
no captions. Refresh the cookie from a logged-in browser. If the video truly
has no CC/AI subtitles, install `[asr]` or pass `--transcript file.srt`.

## 搜索预算耗尽

The run hit `BFC_MAX_TOTAL_SEARCHES`. Remaining claims stay
`insufficient_evidence`. Use `fast` preset or raise the limit.

## 页面访问失败 / 被安全策略拒绝

The URL was not a public HTTP(S) page, redirected to a private address, or the
host refused the fetch. That candidate is dropped; it does not become evidence.

## Docker 重启后配置丢失

The image writes wizard config to `BFC_CONFIG_PATH=/data/config.json`, which is
on the `bfc-data` volume. Do not bind a fresh anonymous volume over `/data` if
you need persistence. Compose still requires `BFC_API_TOKEN`.

## 证据不足

This is abstention, not a hidden “false”. There was not enough fetched, retained
excerpt evidence to pass the deterministic threshold.
