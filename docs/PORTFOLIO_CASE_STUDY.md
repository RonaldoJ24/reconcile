# Which invoices does this payment settle?

An incoming payment is an observation, not an allocation instruction. Several
invoices may have the same total; credits reduce an invoice balance without adding
cash; a message may contradict an old bank reference. Reconcile makes those
exceptions reviewable and records an explicitly approved internal allocation.
It does not initiate transfers or replace an accounting system.

## Constraints shape the implementation

The initial data is synthetic. Processing is real: bounded CSV/TXT parsing,
immutable evidence, PostgreSQL persistence, proposal revisions, reviewer correction,
idempotent application, history export and audited reversal. Monetary values cross
the HTTP/domain boundary as integer centavos; editable amounts are decimal MXN text.

The application is a Python modular monolith (FastAPI/SQLAlchemy/PostgreSQL), a
React/TypeScript interface, an optional scikit-learn shadow ranker and a bounded
DeepSeek interpreter. There is no agent framework, retrieval service or vector
database. The deployment architecture is one same-origin Render Free service and
Neon; the free service may sleep. This branch is not a deployment.

## Baselines before model claims

Amount equality alone cannot establish customer intent. Deterministic rules must
find bounded, unambiguous evidence or defer. The continuation corrects the original
mention-based rules, which could treat a negated invoice reference as positive.
Historical `rules-v1` measurements must not be attributed to the corrected engine.

The learned model ranks feasible candidates using decision-time features. Its raw
score is not calibrated confidence, and it cannot change an allocation. On the
historical sealed synthetic final set, it proposed in 500/500 cases at 50% precision.
Rules proposed in 300/500 at 83.3% precision. The model abstained on none of the 250
underdetermined cases and stayed in shadow mode. More coverage did not justify
promotion. See the immutable [release report](../reports/release-v1/README.md).

Those operating points differ. The old evaluator's timing also surrounded score
bookkeeping, and its recall measured supplied candidates, not end-to-end retrieval.
The [implementation audit](ENHANCEMENT_AUDIT.md) records these limitations rather
than rewriting the historical report.

## A falsifiable job for interpretation

The hypothesis is that semantic interpretation can resolve sufficiently informative
remittance exceptions beyond a credible deterministic baseline without increasing
unsupported recommendations. A deterministic correction baseline is allowed to win.
An insufficient message is a reason to defer, not an invitation to guess.

Direct and Hybrid receive bounded observations and candidates. Structured output
must pass schema, candidate identity, exact citation-location and financial checks.
An existing quote plus valid arithmetic does **not** prove semantic support; that
requires separate evaluation and human review. The historical development smoke
observed real calls but was not a benchmark of interpretation quality.

Live calls are disabled during this continuation. Exact-input recordings require
source, scenario, model, prompt, schema/parser and artifact provenance; missing
provenance means Unavailable. Fixtures used to test validators are labeled test
data and cannot masquerade as provider execution. Unknown usage/cost stays unknown.

## Recommendation, validation and approval

These are distinct steps. A recommendation identifies a possible allocation.
Validation checks specified constraints. Human approval authorizes an internal
recording against a persisted revision. PostgreSQL then checks workspace ownership,
versions, balances and idempotency under locks in one transaction. Provider calls
never occur inside that transaction. Reversal restores only the application's
consumption and keeps its history.

Visible edits must be saved or discarded before applying; confirmation shows the
persisted revision and its cash, credit and projected balance effects. A changed
source invalidates pending decisions. Interpretation never approves itself.

## What v2 is designed to measure

The [frozen v2 protocol](../contracts/PORTFOLIO_V2.md) separates related variants by
group, fixes baselines/selection before final evaluation and reports risk against
coverage with numerators and denominators. Supported proposals, unsupported
proposals, correct deferrals, unnecessary deferrals and provider failures are
different outcomes. A timeout is not credited as safe abstention.

The harness is useful without running a paid or final benchmark. No new model
quality, real-world accuracy, accounting time saved or business impact is claimed.

## Evidence still missing

Labels are synthetic and agent-generated. An independent accounts-receivable or
bookkeeping reviewer must assess decision-time evidence, plausible answers and
ambiguity, with expected labels withheld. Disagreements need documented adjudication
before changing an evaluation target. A future permissioned user study would need
real review time, correction/error rates and representative cases; none is implied
by passing engineering tests.

The historical remote-database list/detail p95 exceeded the proposed 500 ms budget;
matching-only timing excluded persistence. Neither is a new end-to-end latency
claim. No public deployment, provider enablement or external domain review is part
of this continuation's implementation evidence.
