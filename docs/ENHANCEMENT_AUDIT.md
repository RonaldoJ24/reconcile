# Continuation audit — 2026-09-16

Baseline: `dd18e1c`, on top of `c19034d`. This continues the quality correction;
historical phases are not being restarted. Original partial frontend edits remain
in `phase1-frontend`; a copy is being completed in `feat/reconcile-case-study`.

| Requirement | Observed baseline | Action |
| --- | --- | --- |
| File → proposal → correction → apply/export/reverse | Implemented in API and PostgreSQL service; historical E2E evidence exists | Preserve and verify in this run |
| Exact editable MXN, unsaved guard, stale validation | Partial uncommitted frontend changes; not accepted yet | Finish interaction and regression coverage first |
| Conservative references | `domain/matching.py` concatenates bank/message mentions, without polarity | Version rules, preserve source context, defer unresolved semantics |
| Financial transactions | Existing revision, ownership, idempotency and balance checks; broad locks | Preserve; narrow locks only after real PostgreSQL evidence |
| One-click cases, capability agreement, readable trace | Missing; import is default and trace is raw JSON | Add bounded cases through existing import/jobs; additive contracts |
| Provider | Explicit Direct/Hybrid workflow, cache, quotas, failure states | No calls; show genuine provenance and unavailable when unmatched |
| Same-input comparison / replay | No portable exact-input recorded-run mechanism | Reuse candidate/validator contracts, freeze fingerprints, reject mismatch |
| Evaluation presentation and v2 | v1 artifacts exist; no reviewer-facing evaluation surface | Sanitize aggregates, freeze v2 before evaluation |
| Independent domain validation | No evidence it occurred | Prepare blinded packet and adjudication process; leave review pending |

## Evaluation implementation findings

- `ml/evaluate.py:evaluate_scores` defaults to no proposal threshold. The release
  ranker proposed every ranked group. Rules use 0.5 on binary scores. These are
  different coverage operating points, not a matched-risk comparison.
- `ml/train.py` evaluates a separate calibration split but fits no calibrator and
  reports no selected-proposal reliability analysis. A raw score is not confidence.
- `_evaluate_rows` times `_scores_for_group` after feature computation/inference.
  Its latency field is score bookkeeping, not model or end-to-end latency.
  `ml/runtime.py:rank_candidates` does time features/prediction/sorting, excluding
  artifact loading, retrieval, persistence and network.
- Candidate recall checks supplied candidate IDs. It does not measure file-to-
  candidate retrieval. Exact allocation can count a top choice even when a
  threshold defers it; v2 must separate ranking from proposed decisions.
- v1 reports record 500 final and 20 sealed challenge groups, agent-generated
  labels and one original sealed access. This run reads aggregate reports/code,
  not sealed labels, and does not rerun that benchmark. Historical tuning exposure
  beyond the recorded ledger cannot be independently ruled out.
- The negative ranker result remains: final precision 50% at 100% coverage versus
  historical rules 83.3% at 60%. Neither is a measurement of new rules behavior.
- `interpretation/schemas.py:validate_result` checks source-slice existence and
  candidate/financial constraints. It does not establish semantic entailment.
  An arithmetically feasible candidate plus a valid quote does not prove intent.
- Provider workflow represents failures explicitly and reserves/reconciles usage.
  A timeout must stay a provider failure in v2 denominators, not become successful
  safety abstention. Historical smoke is not a model-quality benchmark.

## Conflicts resolved

Reconcile records internal allocations; it does not initiate bank transfers.
Research examples, timings, costs and hiring claims are not measured project
evidence. No inferred authorization to deploy, enable inference, access credentials,
publish, change licensing or create infrastructure. The existing workflow is plain
Python, not LangGraph. No authentic matching provider recording is assumed.

## Test environment

No PostgreSQL executable/service configuration or `TEST_DATABASE_URL` was available
at intake. The owner directed this task to the historical setup. Neon's existing
`test-phases-0-2` branch (`br-broad-haze-aelm777j`) was verified ready, separate from
main and expiring 2026-09-22. Tests use that previously authorized dedicated branch;
no new infrastructure or credential changes are needed. Connection values are
captured directly into process environments and never displayed or committed.
