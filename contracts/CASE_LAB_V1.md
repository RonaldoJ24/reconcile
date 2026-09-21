# Case comparison and reliability extension v1

Additive companion to `CASE_EXPERIENCE_V1.md`, fixed before implementing these
routes. All actions are session-owned and use existing CSRF/revision guards.
No provider call, new database table, autonomous approval or fresh benchmark is
implied by using this screen.

## Compare a revision

`POST /api/v1/proposals/{id}/compare` accepts `expected_revision`.
Return `revision`, `input_fingerprint` and `methods`. Each method has `method`,
`status` (`proposed`, `deferred`, `unavailable`, `failed`), `source`, nullable
`candidate` (complete cash/credit allocation), `actionable: false`, `duration_ms`,
nullable `usage`, nullable `cost_usd`, and `reason`. Comparison is an observation;
only the normal persisted proposal and explicit reviewer action can apply money.
Method identifiers are `rules`, `bounded_correction`, `shadow_ranker`, `direct`, and
`hybrid`. Source `local` identifies local model execution; it must not be labeled
as deterministic rules or live provider output.

The conservative rules and bounded correction baseline may share one actual rules
execution; label that identity rather than inventing two independent methods.
The existing shadow ranker runs on the same persisted candidate context. Measure
feature construction and prediction; disclose artifact loading and database I/O
boundaries. An observed raw score is not calibrated confidence.

Direct and Hybrid use only allowlisted authentic exact-input artifacts that pass
all provenance, digest, schema, citation and financial checks again. With no such
artifact, return Unavailable and a reason. Validator test fixtures are never a
runtime recording registry. Recordings cannot modify proposals or balances.
Persist the comparison with its revision/fingerprint; do not show it as current
when either changes. No winner field or implied unavailable-method success.

## Controlled evidence variants

`POST /api/v1/cases/{case_id}/variant` accepts `expected_revision` and `variant`
from `original`, `ambiguous`, `prompt_like`. Only registered cases are eligible.
The server owns exact bytes. Return the same handles as opening a case plus the
current scenario version. The original variant restores original input text; it
does not restore consumed balances. An unchanged active variant resumes.

Preserve immutable source bytes and earlier proposal revisions. Explicitly
supersede old message evidence and use only active committed evidence in matching
and interpretation. Increment the payment/evidence revision, invalidate pending
proposals, and schedule an ordinary match job. Applied/reversed histories cannot
be rewritten by a variant request. A comparison or recording from another input
fingerprint is ineligible. No free-form public upload is introduced.

## Reliability lab

`POST /api/v1/proposals/{id}/reliability` accepts `expected_revision` and one
`experiment`: `invalid_allocation`, `invalid_citation`, `stale_apply`, or
`duplicate_apply`. These are explicitly synthetic experiment inputs, not outputs
from a provider. Responses name the actual validator/service invoked, expected
behavior, observed result, and persisted effect counts before/after. A rejected
request is evidence only for the named check, never proof of semantic support.

Invalid allocation and citation experiments call the same validation functions
used by the application. Stale apply uses an intentionally obsolete revision with
the real transactional service and must leave balances/history unchanged.
Duplicate apply is available only after an explicit reviewer application, replays
that already approved logical operation's identical payload/key, and must return
the same application ID with no new effects. It does not create a first application
or reverse anything. State these conditions before the user triggers an experiment.

Return `experiment`, `synthetic: true`, `validator`, `expected`, `observed`,
`passed`, nullable `application_id`, and `effects_before` / `effects_after`.
Each effect object contains `application_groups`, `cash_applications`,
`credit_applications`, `cash_centavos`, and `credit_centavos`, read from persisted
financial rows for this proposal. Audit records of the experiment itself are not
financial effects. The server computes `passed` from the observed rejection or
idempotent identity and the unchanged effects, rather than from the experiment
name. The UI labels each injected input as a synthetic check.

## Historical evaluation

`GET /api/v1/evaluation` returns the packaged `evaluation_summary.json` with
current `active_engine` and explicit v2-not-evaluated status. The aggregate artifact
contains source-report digest/date, four historical method/split rows, raw proposal
and correctness counts, precision/coverage, and limitations. Its correctness
label is the historical v1 label definition, not newly independent semantic review.
Neither sealed inputs nor targets are runtime artifacts. The UI must not maintain
its own numeric copies of the summary.
