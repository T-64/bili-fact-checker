# Report schema 1.0

Public reports are `AnalysisReport` in `src/bili_fact_checker/models.py`.

Required top-level fields:

- `schema_version`: always `"1.0"`
- `disclaimer`
- `run`: software version, model, search providers, usage
- `video`: BV, title, URL, optional part
- `transcript`: source (`cc` / `asr` / `file`), language, segments, SHA-256
- `claims`: each with `claim`, evidence documents/excerpts/assessments, `verdict`
- `events`: audit trail
- `stats`

Verdicts are computed in code, not chosen by the model:

- `supported` / `refuted`: one direction reaches the evidence threshold
- `disputed`: both directions reach the threshold
- `insufficient_evidence`: default when evidence is missing or too weak

Claim `quote` must be an exact transcript substring. Excerpt IDs in assessments
must refer to fetched excerpts. URLs that were not fetched cannot appear as
evidence.
