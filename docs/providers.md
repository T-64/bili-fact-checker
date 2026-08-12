# Provider configuration

The default is `LLM_PROVIDER=auto` and `SEARCH_PROVIDER=auto`. Detection uses
the configured API hostname and does not make a live, potentially billable
probe.

| Provider | API base example | LLM protocol | Native search protocol |
|---|---|---|---|
| Z.AI / 智谱 | `https://api.z.ai/api/paas/v4` | OpenAI-compatible Chat Completions | structured `/web_search` |
| OpenAI | `https://api.openai.com/v1` | Chat Completions | Responses `web_search` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta` | native `generateContent` | Interactions `google_search` |
| Anthropic | `https://api.anthropic.com/v1` | native Messages | versioned server-side web search |
| Other OpenAI-compatible endpoint | provider-specific `/v1` | Chat Completions | none assumed |

The legacy environment names `OPENAI_API_BASE`, `OPENAI_API_KEY`, and
`OPENAI_MODEL` are retained for compatibility; they hold the selected
provider's base, key, and model even when that provider is Gemini or Anthropic.
No secret is written to a report.

## One-account examples

### Z.AI / 智谱

```bash
export OPENAI_API_BASE=https://api.z.ai/api/paas/v4
export OPENAI_API_KEY=...
export OPENAI_MODEL=glm-4-flash
```

### OpenAI

```bash
export OPENAI_API_BASE=https://api.openai.com/v1
export OPENAI_API_KEY=...
export OPENAI_MODEL=your-supported-model
```

### Gemini

```bash
export OPENAI_API_BASE=https://generativelanguage.googleapis.com/v1beta
export OPENAI_API_KEY=...
export OPENAI_MODEL=your-supported-gemini-model
```

### Anthropic

```bash
export OPENAI_API_BASE=https://api.anthropic.com/v1
export OPENAI_API_KEY=...
export OPENAI_MODEL=your-supported-claude-model
```

Model names and tool availability change over time. The project deliberately
does not hard-code a supposedly current model name for third-party providers.

## Separate or external search

Set a distinct native-search account without changing the LLM account:

```bash
export BFC_SEARCH_API_BASE=https://api.example/v1
export BFC_SEARCH_API_KEY=...
export SEARCH_PROVIDER=openai  # zai | openai | gemini | anthropic
```

Or select an external provider:

```bash
export SEARCH_PROVIDER=searxng
export SEARXNG_URL=http://127.0.0.1:8080
```

```bash
export SEARCH_PROVIDER=tavily
export TAVILY_API_KEY=...
```

`SEARCH_PROVIDER=offline` or `none` disables open-web discovery. Claims without
already available evidence then remain `insufficient_evidence`.

## Compatibility and billing boundary

"OpenAI-compatible" usually means Chat Completions compatibility. It does not
mean that OpenAI Responses web search, Gemini grounding, Anthropic server
tools, or Z.AI Web Search are supported. The application sends proprietary
tool payloads only when the provider is known or selected explicitly.

Native web search may be billed separately from model tokens. Configure
`BFC_MAX_SEARCHES_PER_CLAIM` and `BFC_MAX_TOTAL_SEARCHES` before large runs.
Reports record provider-returned usage counts where available but never embed
mutable vendor prices.

Retrieval is incremental: the next query is sent only when the current fetched
evidence remains below the deterministic verdict threshold. Successful search
results are cached for one day and extracted page text for seven days under
`~/.cache/bili-fact-checker`. Cache hits are identified in the audit events and
record zero requests/billable uses for that run. Configure
`BFC_SEARCH_CACHE_TTL`, `BFC_PAGE_CACHE_TTL`, and `BFC_CACHE_DIR`; set a TTL to
`0` to disable that cache.
