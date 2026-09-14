# Status

## Phase 0 — in progress

- Verified local project path and clean pre-Git package state.
- Verified `gh` 2.96.0 as `RonaldoJ24`, Render 2.28.0 owner identity, and Neon
  4.17.3 owner organization on the free plan; no secrets printed.
- Created private `RonaldoJ24/reconcile`; no cloud database or service created yet.
- Froze v1 inputs, snapshot semantics, financial invariants, session/CSRF boundary,
  HTTP shapes, job lease contract, and rules-only model boundary in `contracts/V1.md`.
- Completed the read-only reuse inventory in `docs/REUSE.md`; both source commits
  remain unchanged.
- Local PostgreSQL and Docker are unavailable. PostgreSQL integration will use one
  isolated Reconcile-owned Neon Free project only after code is ready.

Checks and initial commit are pending. Phases 1 and 2 have not started. Next: add
tooling, migration, and contract tests; run the Phase 0 checks and commit.
