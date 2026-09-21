# Case experience API — additive v1 extension

Agreed interface for the continuation. Implement after financial prerequisites.
No provider execution is implied by opening, comparing or mutating a case.

## Cases

`GET /api/v1/cases` (authenticated) returns `version` and `cases`, each with
`id`, `title`, `description`, `amount` (centavos). Registry IDs: `straightforward`,
`bundle`, `correction`, `insufficient`, `adversarial`. Public input bytes are newly
authored development examples, not evaluation labels. React gets descriptions,
never an expected answer. Default CTA opens `bundle`.

`POST /api/v1/cases/{case_id}/open` requires the existing mutation/session guard.
It loads the registered packet through parser, validation, commit and matching jobs.
Return `case_id`, `scenario_version`, `payment_id`, nullable `proposal_id`, `jobs`
(job IDs) and `resumed`. Repeated requests resume; do not reset balances or create
duplicates. Serialize creation per workspace, retaining existing quotas/admission.
The browser can use existing bounded job polling and query by `payment_id`.

Cases have separate natural-key namespaces inside the session workspace. Session
isolation remains authoritative. Loading a case must not change manual-import data.

Controlled scenario changes require an explicit CSRF-protected mutation with an
expected revision. Preserve old source bytes and revisions. A new active evidence
version invalidates the old proposal/cache/recording; applied history is not replaced.
Use a registered input variant, never arbitrary public financial uploads. Do not
invent a reset operation when resuming is sufficient.

## Canonical review response

Existing v1 fields remain valid: `payment.amount`, `cash[].amount`,
`credits[].amount` are integer centavos. Snake case is canonical. Frontend DTOs
should mirror actual API responses; no component guesses between aliases/units.

Session response adds `active_engine` and `capabilities` with booleans `interpret`,
`correct`, `apply`, `reverse`. Detail response returns state-aware capabilities;
the server still validates every operation. Provider disabled/uninvited means no
enabled live-interpretation control. Previously persisted provenance remains visible.

Detail adds `case` (nullable case ID/version), `decision_trace` and `comparison`
(nullable). Source view uses the existing authenticated source endpoint; add exact
raw text/version metadata where necessary, preserving existing fields.

## Persisted observations

Store bounded input/candidate/source snapshots and stages in existing revision
`model_trace`, at execution time. Trace schema has `schema_version`,
`input_fingerprint`, and `stages`. A stage has `id`, `name`, `status`, `summary`,
`duration_ms` (null if unmeasured), optional `details` and exact `evidence` references.
Top-level `source` identifies the decision execution: `rules`, `live`, `cache`,
`recorded`, or `unavailable`. `rules` means a deterministic execution, not a provider
call. Keep provider routing/availability in its own stage; a disabled provider does
not make completed rules execution unavailable. Legacy traces may omit this field.
Do not add completed stages for operations that did not run. Routing decisions can
explicitly record skipped/disabled inference. Financial validation is an actual
shared-validator invocation, not a label attached to arithmetic.

Reviewer corrections retain earlier revision history. Application/reversal stages
come from actual persisted audit/application records. Label current state separately
from original trace observations. No hidden reasoning or fabricated timings.

## Comparison and evaluation

`POST /api/v1/proposals/{id}/compare` requires CSRF and `expected_revision`.
Run supported methods against the revision's same immutable input/candidate snapshot.
Persist supplemental comparison in an audit event, not by changing financial lines.
Return `input_fingerprint`, `revision`, and `methods`; expose it on later detail
loads. Each method names `method`, `status`, `source`, nullable candidate,
`actionable` (false for shadow), `duration_ms`, nullable usage/cost, and reason.
No winner field. Direct/Hybrid absent authentic exact-input output are Unavailable.
Invalidated inputs cannot silently reuse prior comparison or recordings.

`GET /api/v1/evaluation` returns only a sanitized versioned aggregate artifact
packaged from preserved reports, plus active-engine and v2-not-evaluated labels.
Frontend has no duplicated metric constants. Never ship input datasets or targets.

Reliability experiments are explicit synthetic actions and call real validators /
transactional services. Test payloads are labeled as such, not provider output.
Any experiment that applies/reverses synthetic balances must state that action
before the user triggers it and return actual persisted effect counts.
