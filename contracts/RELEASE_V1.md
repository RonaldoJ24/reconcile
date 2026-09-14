# Reconcile release contract v1

Frozen for the Phase 5 release candidate on 2026-09-14. This contract does not
authorize deployment, provider benchmarking, spending, publication, autonomous
application, or use of real customer data.

## Frozen behavior

- Input/domain/API contract: `contracts/V1.md`; parser and deterministic allocator:
  `v1` / `rules-v1`.
- Feature schema: `ml-v1`; model: `ranker-ml-v1-logistic` version `1`, model SHA-256
  `bb9774c99870bfa0d20759c6ceb37aafd6fbf65619b28c71947bdb993c16135e`.
- The learned model remains shadow-only. Its raw classifier score has no operational
  confidence threshold and cannot select or apply money. Runtime authority remains
  `rules-v1`, whose evaluator selection threshold is `0.5`.
- Interpretation prompt/schema: `reconcile-interpretation-prompt-v1` /
  `reconcile-interpretation-schema-v1`. Direct and Hybrid remain explicit,
  invite/budget-gated review aids. They never apply or reverse money.
- Dataset: `ml-v1-2026-09-14`, generator `ml-v1-data-1`, seed `20260914`.
  The committed manifests and their recorded file digests are immutable inputs to
  release evaluation.

The exact release commit is recorded by the generated reports. A code change after
that commit creates a new candidate and requires rerunning non-sealed gates. It does
not authorize another sealed-label access.

## Release gates

Hard safety checks require zero invariant violations across at least 1,000 generated
stateful sequences, 25 contested-balance races, deterministic idempotency and
reversal, source-staleness rejection, expired-lease recovery, cross-workspace denial,
provider failure/injection failure-closed behavior, frontend E2E, and clean artifact
and tracked-secret scans.

The workload is 1,000 payments and 10,000 invoices. Proposed local budgets are warm
review-list/detail p95 below 500 ms and non-provider matching below 30 seconds.
Environment, sample counts, cold work, database location, and any miss are recorded;
performance misses are never removed from the report.

## Sealed evaluation

`make evaluate-release` is disabled unless `ALLOW_SEALED_EVAL=1` is explicitly set.
It verifies the four frozen sealed file digests before parsing labels, writes a
durable access record, and refuses a second run when that record exists. It evaluates
the verified shadow model and rules path on the 500 final groups and 20 sealed
challenge groups. No live provider calls occur.

The report states denominators, failures, provenance, thresholds, artifact identity,
commit, and every accessed sealed path/hash. Results are synthetic and
agent-generated, not human reviewed or real-world validated. Human/owner review of
challenge semantics remains a separate, unperformed activity.
