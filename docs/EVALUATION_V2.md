# Evaluation protocol v2

`reconcile.ml.evaluate_v2` is a small, offline evaluator for the v2 portfolio
continuation. It accepts frozen case inputs, expected labels, and recorded
method observations. It does not execute the rules engine, ranker, provider, or
database workflow, so this phase reports no benchmark result and makes no model
or provider claim. Wiring those executors belongs to the case and service phase.

## Dataset and split rules

Every case has a unique `case_id`, an immutable `group_id`, and explicit
`lineage` metadata. Variants, paraphrases, shared entities, and shared history
must use the same group. `stable_split(group_id)` hashes
`reconcile-eval-v2:<group_id>` with SHA-256 and takes modulo 10:

- buckets 0–5: `development`
- buckets 6–7: `validation`
- buckets 8–9: `reserved-final`

The evaluator rejects duplicate or missing case/result/label joins, declared
split mismatches, cross-group lineage, public data outside development, and
input/target rows whose hashes no longer match a frozen manifest. Money is an
integer count of MXN centavos. A credit allocation line must include
`credit_note_id`, linked `invoice_id`, and positive integer `amount`; allocation
comparison includes both IDs and amounts.

## Observations and metrics

An observation records the exact input, candidate, evidence, and validator
fingerprints, its outcome, and a non-negative duration or `null`. Ranker
observations also declare `timing_scope: "features+predict"`; this is a claimed
scope supplied by the executor, not a fact this observation-only evaluator can
verify. A missing duration stays unknown. A proposal must identify a candidate
and complete allocation.

Reports keep citation location and semantic support as separate counts. They
include supported, unsupported, and unknown proposals; answerable resolution;
proposal precision; all-case coverage; correct and unnecessary deferrals;
errors and unavailable outcomes; and risk-versus-coverage counts. Unknown
semantic support is never treated as supported.

## Ranker selection

The same recorded ranking trace is evaluated at raw thresholds `0`, `.25`, `.5`,
`.75`, `.9`, and `1`. Selection is validation-only: choose maximum all-case
coverage with zero observed unsupported or unknown proposals, breaking ties in
favor of the higher threshold. If no point qualifies, the artifact records
`defer_all`. The selected artifact includes the validation manifest hash and a
content hash; it must be verified before a later run.

Paired methods can be checked with `validate_observation_parity`; they must use
identical input, candidate, evidence, and validator fingerprints. This supports
comparisons between the active conservative and bounded correction baselines
without allowing one method a different candidate set. The harness executes no
method and has no authentic provider artifact loader. Direct/Hybrid observations
therefore accept only an explicit `UNAVAILABLE` row; supplied provider
proposals, failures, and claimed authenticity are rejected until an authenticated
observation adapter is added to this harness. The runtime comparison verifier is
a separate boundary and is not wired into this evaluator.

## Reserved final access

`read_split` checks explicit authorization, the exact frozen manifest hash, and
an atomic one-time ledger reservation before opening either final input or label
file. A missing authorization, wrong hash, or reused ledger fails first. No
reserved-final file was opened in this phase and no final execution is implied.

The toy fixtures in `backend/tests/test_ml_evaluate_v2.py` are synthetic unit
fixtures only. Run them with:

```text
uv run pytest backend/tests/test_ml_evaluate_v2.py
```
