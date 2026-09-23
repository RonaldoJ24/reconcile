# Evaluation v2 run: pre-registration

Registered on 2026-09-23, before any evaluation case existed. The protocol in
[`contracts/PORTFOLIO_V2.md`](../../contracts/PORTFOLIO_V2.md) still applies; this
file fixes the choices that protocol leaves to the run.

## System under test

The system under test is frozen at the commit that adds this file. The runner
records that commit in `run.json` and refuses to run if `HEAD` differs or
`backend/src` has uncommitted changes.

- Rules: `rules-v2-conservative`.
- Retrieval (#39): case-scoped. It takes exact identifiers, then the exact
  payment amount, then folio fragments, and keeps at most ten invoices.
- Candidates: single invoices, two- or three-invoice combinations whose balances
  add up to the payment, and at most one linked credit note that the evidence
  names.
- Interpreter (#43): Direct mode, prompt `reconcile-interpretation-prompt-v3`,
  schema `reconcile-interpretation-schema-v1`, `deepseek-flash`, temperature 0,
  JSON output and production validation.
- Shadow ranker: `ranker-ml-v1-logistic` version 1, SHA-256 `bb9774c9…c16135e`.
- Hybrid mode is not evaluated.

## Cases

- About 180 cases, written by two separate Claude subagents from the domain-only
  brief in [`AUTHORING_BRIEF.md`](AUTHORING_BRIEF.md). The authors are told not
  to read this repository, and they are given nothing about the prompt, rules,
  retrieval, candidate generation or its limits. They label by accounting truth,
  including allocations this system cannot represent.
- Cases and labels stay outside the repository until the run is complete. The
  operator does not read labels before scoring, and only aggregate counts are
  printed before the live pass. Cases, labels, observations and recordings are
  published with the results.
- Cases with authoring defects (schema errors or parser rejections) are excluded
  and listed by identifier. No case is edited after the live pass starts.
- The main limitation: these are AI-written synthetic cases, and the authors
  belong to the same model family that helped build the system. They are not
  independent human labels.

## Methods

1. **Rules alone.**
2. **Shadow ranker.** The operating point is chosen on the validation split only,
   from thresholds 0, .25, .5, .75, .9 and 1. The rule picks maximum coverage
   with zero unsupported proposals; ties go to the higher threshold, and if no
   threshold qualifies, the ranker defers everything. The ranker is reported on
   the other splits.
3. **Rules then Direct**, which is production behavior. Rules decide when they
   propose; otherwise Direct interprets once. When there is no candidate or no
   note, production makes no provider call, and the case counts as a deferral.

## Run rules

- There is one live pass. Each rules-deferred case is interpreted at most once.
  Provider failures, timeouts and budget stops are recorded as unavailable and
  are not retried. An interrupted run resumes without calling recorded cases
  again.
- Budget: an evaluation-only policy is passed explicitly to the workflow. Its
  execution, daily and monthly caps are US$2.00 each, with at most 1,000
  attempts and two attempts per case session. Visitor limits in the product do
  not change.
- Direct observations reach the frozen evaluator only through recordings that
  the evaluator verifies itself. It checks the digest, the frozen identities, the
  request against the fingerprint the workflow persisted, the candidate set, and
  production validation of the result.
- The reserved-final split is opened once, through the one-time ledger, after the
  live pass.
- After cases exist, nothing changes in the rules, retrieval, candidates,
  prompt, model or validation. Any later change needs a new case set.

## Metrics and headline

- Splits come from `stable_split("eval-v2-" + case_id)`: about 60% development,
  20% validation and 20% reserved-final.
- **Headline:** reserved-final, end to end, reported as counts.
- **Secondary:** all cases for rules and for rules then Direct, because neither
  is tuned on any split. The ranker excludes validation.
- Each method reports:
  - proposals, correct proposals and unsupported (wrong) proposals
  - correct and unnecessary deferrals
  - unavailable outcomes
  - precision (correct divided by proposals)
  - answerable resolved (correct divided by every case a person can answer)
  - misallocated value: the payment amount of every unsupported proposal
- **Answerable but unreachable** means a person can allocate the payment, but no
  system candidate matches the correct allocation. These cases are reported as
  their own row and stay in the answerable denominator. A proposal on one of
  them counts as unsupported; a deferral counts as a missed answerable case. The
  frozen evaluator scores the reachable subset, and the end-to-end numbers add
  the unreachable cases on top.
- Provider: attempts, tokens, cost estimated at the peak list price (US$0.30 per
  million input tokens, US$1.20 per million output tokens), and p50/p95 latency.

## Commands

```sh
uv run python -m reconcile.ml.run_v2 prepare --cases CASES --labels LABELS --out RUN --commit SHA
uv run python -m reconcile.ml.run_v2 interpret --out RUN --budget-usd 2.00 --run-id 20260923
uv run python -m reconcile.ml.run_v2 score --out RUN
uv run python -m reconcile.ml.run_v2 score --out RUN --final
```

`TEST_DATABASE_URL` must point at the isolated test branch, and
`ALLOW_DESTRUCTIVE_TEST_DB=1` must be set. The run uses its own
`reconcile_eval_v2` schema and never touches the preview database.
