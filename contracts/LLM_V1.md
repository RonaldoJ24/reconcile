# Reconcile bounded interpretation contract v1

Frozen 2026-09-14 for Phase 4. The interpreter may select an already enumerated
allocation candidate or abstain. It never creates money, applies/reverses a
proposal, runs SQL, fetches URLs, or treats document instructions as trusted.

## Modes and input

`off` is the default. `direct` receives the same bounded decision-time payment,
invoice, credit, evidence, and candidate records used by the rules and learned
paths. `hybrid` receives that identical record plus the verified ranker's ordered
candidate IDs and raw scores. Rank order is context, not authority or confidence.

Each request contains at most ten candidate invoices and five immutable evidence
spans. Candidate IDs, source IDs, monetary centavos, currencies, dates, source
hashes/versions, prompt/schema versions, and the decision timestamp are explicit.
All quoted source content is delimited as untrusted data. The complete UTF-8
request must be at most 6,000 bytes, a conservative upper bound on byte-tokenized
input; needed-content truncation abstains before a provider call. Output is capped
at 2,048 tokens.

## Provider request and validated result

OpenAI is the only provider. The base URL is `https://api.openai.com/v1`, the
default configurable model is `gpt-6-luna`, and live use requires an explicit
enable flag, API credential, and nonzero budget. Chat completions set:

- `reasoning_effort: "none"`
- `response_format: {"type": "json_object"}`
- `stream: false`
- `max_completion_tokens: 2048`

The provider changed from DeepSeek (`deepseek-flash`) to OpenAI on 2026-09-23. The
v2 evaluation measured the DeepSeek configuration.

The JSON result is strict and has no extra fields:

```json
{
  "decision": "select | needs_review",
  "candidate_id": "candidate ID or null",
  "reason_code": "evidence_supported | ambiguous | contradictory | insufficient_evidence",
  "citations": [
    {"source_id": "UUID", "start": 0, "end": 12, "quote": "exact source text"}
  ]
}
```

`select` requires one known candidate and at least one citation. `needs_review`
requires a null candidate. Every citation must name a supplied immutable source,
fit its character bounds, and exactly equal that source slice. Unknown IDs,
malformed/empty/truncated JSON, unsupported values, bad citations, and a selected
candidate whose deterministic allocation validation fails are structured failures
and cannot create a proposal revision. Citation existence does not establish
entailment; contradictory and negated cases are measured separately.

## Workflow, failure, and cancellation

The compiled stages are: load actual observations, enumerate/validate candidates,
optionally rank, compile bounded request, read validated cache, reserve budget,
call, parse/validate, persist telemetry/cache, and return interpretation. No
database transaction remains open during the provider call. Cancellation and the
overall deadline are checked between stages. One attempt has a 20-second deadline;
the overall workflow has a 45-second deadline and free-profile concurrency is one.

Only network errors, HTTP 429, and HTTP 5xx may retry once. HTTP 400/401/402/403/
404/422 and validation failures do not retry. Each attempt reserves separately.
A timeout or indeterminate transport outcome retains its reservation as possibly
billed. Definite pre-send/provider rejections release it. All failures return an
explicit unavailable/needs-review result; no cached or rules result is labeled live.

## Cache and budget

Only successful, strictly validated interpretations are cached. The SHA-256 key
covers workspace ID, immutable source IDs/hashes/versions and exact spans,
candidates, optional rank context, requested model, thinking/output/token settings,
prompt/schema versions, and budget-policy version. A source/evidence/candidate or
configuration change therefore misses the cache. Cache replay is labeled, provider
prompt-cache tokens are recorded separately, and financial application is never
cached.

PostgreSQL transactionally reserves the worst uncached peak-price amount before
every attempt under locked UTC-day, UTC-month, session, and execution counters.
The smallest configured limit wins. Attempts and reservations survive workspace
deletion. Default application ceilings are five attempts per session, 25 globally
per UTC day, USD 0.10 per UTC day, and USD 1.00 per UTC month; live mode remains
off until explicitly enabled. This execution additionally caps all attempts at
USD 0.05 and never tops up credit.

The official 2026-09-23 `gpt-6-luna` standard rate card is USD 0.10/1M input,
USD 0.125/1M cache writes, USD 0.01/1M cached input and USD 0.50/1M output tokens.
Reservations price every uncached input token at the cache-write rate, and cache
discounts are ignored. A 6,000-input/2,048-output attempt reserves USD 0.001774,
or 1,774 microdollars. Persist requested/response model, input,
output, provider-cache and reasoning tokens when reported, latency, retry number,
cache status, HTTP/failure code, reservation state, and reconciled estimated cost.
Missing usage remains unknown, never zero.

## Evaluation boundary

Live calls run only through explicit budgeted commands or an explicit authenticated
action, never default tests, CI, page load, health checks, or rules-resolved cases.
Phase 4 compares rules, learned ranker, direct, and hybrid on identical development
inputs, including missing/changed/contradictory evidence. It records every failure
in denominators. Sealed/final labels, deployment, autonomous application, and claims
of real-world validation remain outside Phase 4.
