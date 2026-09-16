# Same-input method observations

`POST /api/v1/proposals/{id}/compare` accepts the current `expected_revision`.
The server reconstructs the saved decision snapshot, verifies its fingerprint
and active rules identity, and returns observations for rules, bounded correction,
the existing shadow ranker, Direct and Hybrid. Rules and bounded correction share
one actual execution. Every observation is non-actionable; application still uses
the ordinary persisted proposal, reviewer confirmation and transaction service.

Comparisons are stored in audit records with revision and input fingerprint.
Proposal detail returns only a comparison matching the current revision and
fingerprint. Missing legacy snapshots and unsupported rule identities are explicit
Unavailable results. A corrected allocation does not retroactively change the
original model input or turn a reviewer decision into a model prediction.

## Measurements

Rules timing measures the rules call. Ranker timing measures feature construction,
prediction and ordering using the existing runtime timer. It excludes artifact
loading, retrieval, persistence, network and subsequent financial validation.
These local observations are not end-to-end latency distributions or benchmarks.
Raw ranker scores are not calibrated confidence. Unknown usage and cost stay null.

## Recording boundary

The production recording registry and trusted digest sets are empty. Direct and
Hybrid therefore return Unavailable; the comparison endpoint makes no provider
call. Historical smoke reports and validator fixtures are not eligible recordings.

A future registration requires independently authenticated provider evidence and
reviewed trust pins. A hash establishes content integrity, not proof that a provider
ran. The verifier checks the complete canonical recording against a separately
pinned digest, its pinned artifact reference, model/prompt/schema/parser/rules/mode
identities, case/version, fingerprint and portable source hashes and versions.
Hybrid also requires the same rank context produced from the saved candidates.
Source IDs are rebound only through matching source identity. Existing result
schema, citation-location, candidate and financial checks run again. Those checks
do not establish semantic entailment.

Tests install synthetic trust fixtures only within their test scope to exercise
acceptance and rejection paths. They are not authentic runtime recordings or
measurements of provider quality. The observation-only v2 evaluator remains a
separate module and does not ingest these runtime recordings.

## Historical aggregates

`GET /api/v1/evaluation` serves the packaged, sanitized aggregate with report
digest/date and current engine identity. The four historical rows retain their
original label definitions and operating points. V2 is not evaluated and external
domain review is pending. No sealed inputs or labels are runtime dependencies.
