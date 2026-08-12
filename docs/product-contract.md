# Product contract

`bili-fact-checker` is an evidence review assistant for spoken claims in
Bilibili videos. It is not an oracle and it must never turn model confidence
or a search result into a factual verdict.

## Audience and primary workflow

The primary user is a non-programmer who can paste a Bilibili URL, supply the
minimum required credentials, and review a report in a local web interface.
The same workflow must remain scriptable through the CLI and REST API.

For supported AI providers, the normal setup uses one provider account for both
language-model calls and native web search. A separate SearXNG, Tavily, Brave,
or Google Fact Check account must not be an undocumented prerequisite. A
Bilibili cookie may still be required for videos whose subtitles require login.

The product promise is:

1. preserve the speaker's exact words and timestamp;
2. turn those words into independently checkable claims;
3. record every search query and retrieval failure;
4. quote the exact passage used from every fetched source;
5. distinguish relevant context from supporting or refuting evidence;
6. return `insufficient_evidence` whenever the evidence bar is not met.

## Non-negotiable truthfulness rules

- Search titles and snippets are discovery metadata, not evidence.
- AI-provider native-search answers and citation annotations are discovery
  metadata until their source pages are independently fetched.
- A URL is not evidence until the page is fetched and an exact excerpt is
  stored with it.
- A model may classify an excerpt, but it cannot mint a source URL, evidence
  identifier, source-quality tier, or final verdict.
- Every cited URL and excerpt identifier must be selected from the retrieved
  evidence supplied to the model and validated by code.
- No retrieved evidence means `insufficient_evidence`. The application must
  not emit `likely_true`, `likely_false`, or a truth probability from model
  memory.
- Conflicting credible evidence means `disputed`, not whichever position has
  the more persuasive prose.
- Retrieval, parsing, authentication, subtitle, and model failures must remain
  visible in the audit trail. They must not silently become "no evidence".
- The UI must display the speaker quote, timestamp, evidence excerpt, source,
  retrieval date, and verdict basis together.

## Verdict vocabulary

The public schema has exactly four verdicts:

- `supported`: the evidence bar for support is met and no comparable refuting
  evidence is present;
- `refuted`: the evidence bar for refutation is met and no comparable
  supporting evidence is present;
- `disputed`: credible supporting and refuting evidence are both present;
- `insufficient_evidence`: all other cases.

Verdicts describe the current evidence bundle, not eternal truth. Reports must
show their generation and retrieval timestamps.

## Evidence bar

An excerpt first passes through relevance and stance classification. Only
`supports` and `refutes` count toward a verdict; `context` and `irrelevant` do
not.

Evidence strength is assigned by deterministic code from source metadata and
independence:

- `high`: a directly relevant primary/official source, a well-matched
  ClaimReview, or two independent credible sources with the same stance;
- `medium`: one directly relevant credible secondary source;
- `low`: weakly attributable, low-quality, duplicated, or incomplete material;
- `none`: no validated excerpt with a directional stance.

Only high-strength evidence can produce `supported` or `refuted` by default.
Medium/low evidence remains visible but yields `insufficient_evidence`.

## Security and cost boundaries

- The server binds to loopback by default.
- Public exposure requires the operator to configure authentication and a
  reverse proxy deliberately.
- Analysis jobs run through a bounded queue with concurrency, timeout, input,
  claim-count, search-call, search-result, retry, and page-size limits.
- Evidence fetching rejects loopback, link-local, private-network, and unsafe
  URL schemes to prevent SSRF.
- Secrets never appear in reports, logs, API responses, fixtures, or examples.
- Native search may be billed separately by an AI provider. The application
  must show configured call limits before a run, record provider-reported usage,
  cache searches, and never claim that a search is free or quote a stale price.
- Provider capability detection must not make a paid live call without explicit
  user action.

## Definition of done for 1.0

The project is releasable only when all of the following are true:

- a clean machine can run the CLI from an installed wheel;
- `docker compose up` starts the API and UI without requiring a separate search
  service when a supported native-search provider is configured;
- one-provider setup works for Z.AI and the documented native-search behavior
  of every additional provider is covered by contract fixtures;
- a user can complete the primary workflow without reading source code;
- the same offline fixture produces a deterministic schema-valid report;
- fabricated citation identifiers and URLs are rejected by tests;
- unsupported claims produce `insufficient_evidence` in tests and UI;
- desktop and mobile browser tests cover submit, progress, results, evidence
  inspection, failure, and retry states;
- CI runs lint, unit tests, integration tests, frontend build, wheel smoke test,
  and container build.
