# Security

Report vulnerabilities privately to the repository owner. Do not open a public
issue that includes live API keys, Bilibili cookies, or a working exploit.

This project treats the following as security-sensitive:

- `BFC_API_TOKEN` and non-loopback API binds
- LLM/search API keys and Bilibili `SESSDATA`
- SSRF controls on evidence page fetches
- Prompt-injection boundaries around subtitles, titles, and page excerpts

Local config is stored at `~/.config/bili-fact-checker/config.json` with mode
`0600` only when the user opts in. Environment variables always override the
file.
