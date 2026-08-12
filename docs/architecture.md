# Architecture

## Pipeline

```text
Bilibili URL / BV
  -> video metadata + selected part
  -> timestamped transcript segments
  -> segment-preserving chunks
  -> atomic claims anchored to exact segment IDs
  -> bounded multilingual fact-checking questions
  -> search provider routing
       -> native AI-provider search (default when supported)
       -> explicitly configured external search (optional)
       -> historical/offline lookup (supplemental)
  -> normalized search candidates (discovery only)
  -> safe page fetch + article extraction
  -> immutable evidence excerpts
  -> per-excerpt relevance and stance classification
  -> deterministic evidence aggregation
  -> versioned report + audit trail
```

The important boundary is between discovery and evidence. Search APIs return
candidates. Only successfully fetched pages with retained excerpts can enter
the evidence classifier.

## Package layout target

```text
src/bili_fact_checker/
  api/          FastAPI routes, bounded jobs, auth
  claims/       transcript chunking, extraction, anchoring, deduplication
  evidence/     search, safe fetch, extraction, classification, aggregation
  ingest/       Bilibili metadata/subtitles, files, optional ASR
  models.py     versioned Pydantic domain and report models
  providers/    OpenAI-compatible LLM and search adapters
  report/       JSON, Markdown and static HTML renderers
  web_dist/     built React application included in wheels
  cli.py        doctor, list, transcript, analyze, serve
```

Provider interfaces isolate network services from the pipeline. Tests use
offline implementations rather than monkey-patching global HTTP calls.

## Provider routing

The default user-facing setting is `SEARCH_PROVIDER=auto`. Search credentials
inherit the configured LLM credentials unless the user deliberately supplies a
separate search account.

`auto` is deterministic rather than optimistic:

1. a known Z.AI, OpenAI, Gemini, or Anthropic provider selects its native search
   adapter if the configured API surface and model support it;
2. an explicitly configured SearXNG, Tavily, or other external adapter is used
   next;
3. historical indexes are queried as a supplement;
4. otherwise the run records `search_unavailable` and cannot produce a sourced
   verdict for a novel claim.

An arbitrary OpenAI-compatible endpoint is not assumed to implement OpenAI's
Responses API or any provider-specific built-in tool. `doctor` reports detected
and configured capabilities without making a paid probe unless the user asks
for a live check.

Native search protocols remain separate adapters:

- Z.AI structured Web Search / Web Search in Chat;
- OpenAI Responses web search and source annotations;
- Gemini Google Search grounding metadata;
- Anthropic versioned web-search result and citation blocks.

Each adapter emits the same `SearchCandidate` objects. Provider-generated
answers and cited text may help rank candidates, but only application-fetched
documents and retained excerpts can enter evidence aggregation.

## Domain objects

- `TranscriptSegment`: stable ID, start/end seconds, exact text.
- `AtomicClaim`: normalized claim, exact quote, anchor segment IDs, checkability
  rationale, named entities and temporal context.
- `SearchPlan`: claim ID and recorded Chinese/English queries.
- `SearchCandidate`: provider/query, direct URL, title, snippet, publication
  time, retrieval time and raw reference; used only for discovery.
- `EvidenceDocument`: fetched URL, canonical URL, metadata, extracted text hash,
  retrieval time and source-quality signals.
- `EvidenceExcerpt`: immutable excerpt ID, exact text, document ID and character
  range.
- `EvidenceAssessment`: excerpt ID, relevance, stance and constrained rationale.
- `ClaimVerdict`: deterministic verdict, strength, supporting/refuting excerpt
  IDs and human-review flags.
- `AnalysisReport`: schema version, software/model configuration (redacted),
  video/transcript provenance, claims, evidence, provider capabilities, usage
  counters, events and failures.

## Trust boundaries

LLM output is untrusted structured input. Pydantic validates its shape and
additional code validates all references against known segment, claim,
document and excerpt IDs. Unknown references are discarded and recorded.

Remote HTML is untrusted. Fetching permits only public HTTP(S) destinations,
limits redirects and bytes, applies timeouts, strips active content, and never
renders remote HTML in the report.

The final verdict is not requested from the model. It is computed from validated
assessments, source signals and source independence. A model may produce a
plain-language explanation only after the verdict and citation set are fixed.

Provider-native browsing is also an untrusted boundary. Citation URLs must be
read from documented response fields, normalized, checked against redirect and
SSRF policy, and fetched independently. A native-search answer without usable
source URLs is not evidence.

## Runtime and delivery

- CLI and API call the same application service.
- Jobs persist as JSON under a configurable data directory using atomic writes.
- A bounded executor exposes progress events while a job is running.
- The production container runs as a non-root user and uses a health check.
- The default Docker Compose path reuses a supported AI provider and has no
  mandatory search container. SearXNG is an opt-in profile for users who want
  self-hosted metasearch.
- The frontend is built separately with Vite and its compiled assets are bundled
  inside the Python wheel and container.

## Cost and usage boundary

Search limits apply independently from token limits. A run records configured
budgets and provider-reported counts, but does not hard-code mutable vendor
prices. Cache keys include provider, normalized query, language, filters, and
the provider API version where applicable. The UI previews maximum calls and
shows actual/unknown usage without presenting an unreliable currency estimate.
