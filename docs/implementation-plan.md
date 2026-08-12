# 1.0 implementation plan

This plan targets a public open-source release that a non-programmer can run
with one AI-provider configuration. Search must reuse that provider's native
web-search capability when possible. SearXNG, Tavily, and Brave are optional
fallbacks, not hidden prerequisites.

## Guiding decisions

- The default setup asks for an AI API base, key, and model. Native search
  inherits those credentials when the selected provider supports it.
- `SEARCH_PROVIDER=auto` is the default. It selects a known native adapter,
  then an explicitly configured external adapter, then historical/offline
  lookup. It never guesses that an arbitrary OpenAI-compatible endpoint
  supports a vendor-specific search tool.
- Z.AI is the first native adapter because it is the existing default model
  provider and exposes structured search results through the same account.
- OpenAI Responses web search, Gemini Google Search grounding, and Anthropic
  web search follow as separate adapters because their request and response
  protocols are incompatible with one another.
- A search result, native-search answer, citation annotation, title, or snippet
  discovers candidate URLs. It does not become evidence until the application
  fetches the page and retains an exact excerpt.
- Search-tool calls can have provider-specific charges. The application caps
  calls, records usage, caches results, and reports cost as unknown when the
  provider does not return enough billing data. Prices are not hard-coded.
- Google Fact Check, Data Commons ClaimReview, and Cofacts are historical
  lookup sources. They supplement open-web retrieval rather than replace it.

## Phase 1 — contract, schema, and provider foundation

- Finish the versioned domain models and public report schema.
- Define `LlmProvider`, `SearchProvider`, `PageExtractor`,
  `EvidenceReranker`, and `EvidenceVerifier` protocols.
- Define normalized provider results: query, title, direct URL, snippet,
  publication time, provider, retrieval time, and raw provider reference.
- Add an explicit capability matrix for native search, source URLs, cited text,
  domain/date filters, usage reporting, and page-open support.
- Implement configuration precedence and redacted diagnostics:
  `auto`, `native`, named providers, `external`, `offline`, and `none`.
- Make search credentials inherit the LLM credentials by default while still
  permitting a different search account.
- Add offline provider contract tests and fixtures for malformed, missing, and
  fabricated URLs.

Exit gate: an unknown OpenAI-compatible endpoint is never sent a proprietary
search-tool payload, and every supported provider normalizes to the same
validated candidate schema.

## Phase 2 — native and fallback search providers

- Implement Z.AI structured Web Search first, using the current API key/base.
- Implement OpenAI Responses `web_search` and parse its source/citation fields.
- Implement Gemini `google_search` grounding and its URL annotations.
- Implement Anthropic versioned web-search tools and citation blocks.
- Retain SearXNG and Tavily adapters; add Brave only if its terms and caching
  restrictions fit the release behavior.
- Keep Google Fact Check as a low-priority historical adapter instead of a
  privileged truth source.
- Add strict call budgets, timeouts, retries, query/result deduplication, and a
  persistent cache with provider-aware expiry.
- Record every generated query, provider choice, result, retry, billable-use
  counter when available, and failure in the audit trail.

Exit gate: a user of each supported native provider needs no separate search
configuration, and provider failure degrades visibly without becoming a
model-memory verdict.

## Phase 3 — trustworthy evidence engine

- Preserve transcript segment IDs through claim extraction.
- Validate exact speaker quotes and anchors; reject invented anchors.
- Generate atomic claims, check-worthiness decisions, and bounded Chinese and
  English fact-checking questions.
- Validate public URLs and prevent SSRF before any fetch or redirect.
- Replace the hand-written main-text parser with Trafilatura; retain a small
  deterministic fallback and make browser extraction an optional extra.
- Store canonical URL, metadata, content hash, retrieval time, extraction
  status, and immutable exact excerpts.
- Rank passages with a lightweight lexical baseline and offer BGE-M3 /
  bge-reranker-v2-m3 through an optional `local-ml` extra.
- Classify each excerpt independently as support, refute, context, irrelevant,
  or unclear. Validate every model-produced identifier against known IDs.
- Aggregate verdicts deterministically and default to
  `insufficient_evidence`. Permit a second search round only when a recorded
  evidence gap justifies it and the budget allows it.

Exit gate: a model cannot create a sourced verdict without fetched, retained
evidence, and no-evidence claims always remain insufficient.

## Phase 4 — complete Bilibili workflow

- Harden video/part selection, metadata, subtitle authentication, and typed
  error reporting.
- Prefer Bilibili CC/AI subtitles and make local ASR an optional fallback.
- Use segment-preserving map/reduce for long transcripts without truncating the
  end of a video.
- Deduplicate overlapping claims while retaining all relevant timestamps.
- Add resumable jobs and caching at transcript, claim, query, URL, document,
  and assessment levels.
- Provide fast and strict presets with visible claim/search/page limits.
- Generate versioned JSON, readable Markdown, and self-contained HTML reports
  from the same report model.

Exit gate: long, multipart, missing-subtitle, expired-cookie, partial-search,
and partial-page-fetch cases all produce intelligible outcomes and resumable
audit records.

## Phase 5 — distributable local application

- Add `doctor`, provider/capability diagnostics, typed errors, retries, and
  redacted support bundles.
- Add a local setup wizard for API base/key/model and optional Bilibili cookie.
  Secrets are never echoed back or placed in reports and are stored only when
  the user opts in.
- Make CLI and REST API call the same application service.
- Move API and compiled web assets into the installable Python package.
- Add a bounded persistent job queue, cancellation, progress events, and
  optional bearer authentication.
- Add wheel/pipx smoke tests, a non-root Docker image, and a simple Compose
  setup that does not require a separate search container.
- Offer SearXNG as an opt-in Compose profile for self-hosting users.

Exit gate: a clean user can reach the UI, configure one supported AI provider,
analyze a public video, and inspect a report without deploying a search engine.

## Phase 6 — evidence review UI

- Design the complete desktop and mobile experience before implementation.
- Implement first-run provider setup, capability status, and a cost/limit
  preview before starting a job.
- Implement URL submission, part selection, live stage progress, cancellation,
  retry, and resumable history.
- Implement claim navigation, Bilibili timestamp links, verdict basis,
  supporting/refuting/context evidence, exact excerpts, source metadata, and
  the full retrieval audit.
- Distinguish provider unavailable, search budget exhausted, page inaccessible,
  extraction failed, evidence conflicting, and evidence insufficient states.
- Never display an uncalibrated truth percentage. Show evidence strength and
  the rule that produced the verdict instead.

Exit gate: browser tests cover setup through evidence inspection, and desktop
and mobile visual checks pass the accepted design specification.

## Phase 7 — evaluation, security, and release

- Add CI for formatting, typing, Python/frontend tests, builds, wheel install,
  containers, dependency review, and secret scanning.
- Add adversarial tests for prompt injection in subtitles/pages, fabricated
  citations, redirect SSRF, duplicate sources, poisoned snippets, and provider
  response drift.
- Build a redistributable offline regression set and opt-in adapters for
  AVeriTeC, CFEVER, and CHEF without bundling license-restricted datasets.
- Measure claim-anchor accuracy, URL retrieval success, evidence recall,
  citation validity, verdict accuracy conditioned on sufficient evidence,
  abstention quality, latency, and search/tool usage.
- Rewrite README around a five-minute one-provider quick start and an honest
  provider/capability/cost matrix.
- Add contributing, security, privacy, schema, troubleshooting, benchmark,
  and release documents. Tag 1.0 only after every gate passes.

Exit gate: a new user can install, configure, run, understand, and audit the
project without private knowledge of the author's machine or an undisclosed
second paid service.

## Delivery order

The implementation order is deliberately vertical:

1. finish Phase 1 and a Z.AI native-search slice;
2. complete one claim -> search -> fetch -> excerpt -> verdict path;
3. lock it with offline tests before adding more providers;
4. add OpenAI, Gemini, and Anthropic adapters against the same contract;
5. finish the Bilibili workflow, packaging, and UI;
6. run security/evaluation gates and prepare the public 1.0 release.

This avoids building four integrations before the evidence contract is proven.
