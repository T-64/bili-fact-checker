# Changelog

## 1.0.0

- Evidence-backed report schema 1.0: `supported` / `refuted` / `disputed` /
  `insufficient_evidence`. Search snippets and model memory are not evidence.
- Native search adapters for Z.AI, OpenAI, Gemini, and Anthropic, with optional
  SearXNG/Tavily fallbacks.
- Local setup wizard (`bili-fact-checker setup` and first-run web form).
- Loopback-first API with required token for public binds.
- Offline tests for prompt-injection boundaries, SSRF redirects, and abstention.
