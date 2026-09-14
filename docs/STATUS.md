# Status

## Phase 0 — complete

- Verified the package path and Git state; authenticated `gh` 2.96.0 as
  `RonaldoJ24`, Render 2.28.0 as the owning user, and Neon 4.17.3 in the owner
  organization on plan `free`. No credentials were printed or tracked.
- Created private `RonaldoJ24/reconcile`, froze the v1 input/domain/API/job/model
  contracts, and recorded the bounded read-only reuse inventory. `incident-lens`
  and `supplierops-lab` were not modified.
- Added locked Python tooling, an explicit Alembic migration, guarded test and
  development commands, and a read-only `make cost-preflight`.
- Created the Reconcile-owned Neon project `polished-block-65726106` and expiring
  branch `br-broad-haze-aelm777j` for PostgreSQL verification. Existing projects
  were not repurposed. A requested custom suspend interval was unsupported on the
  Free account, so the branch retains plan-managed scale-to-zero behavior.

Checks: `make cost-preflight` passed; Alembic offline SQL generation and the real
upgrade passed; `git diff --check` passed. Contract commit: `01bb303`.

## Phase 1 — complete

Implemented bounded CSV/TXT validation with row reports, immutable source bytes
and locators, conflict-safe commits/opening snapshots, exact centavo arithmetic,
deterministic `rules-v1` proposals, evidence and alternatives, revisions,
transactional/idempotent application and reversal, CSV-safe export, and leased
PostgreSQL jobs. Workspace scoping, stale version tokens, shared-balance locking,
CSRF/origin checks, and synthetic-only preview admission are enforced.

Checks: `make check` passed (`ruff`, `mypy`, 13 unit/contract tests; 6 PostgreSQL
tests deselected); the guarded Neon run passed 6/6 PostgreSQL tests in 36.77 s.
Commits: `b35d12a`, `73db264`, `0c69c2b`, `f94397e`. PR: #2.

## Phase 2 — complete

Implemented the Imports, Review Queue, and Allocation Detail screens against the
real API, including row-level validation feedback, job execution, evidence/source
links, alternative and balance display, reviewer corrections, explicit apply and
reverse actions, reload persistence, and application-history export. The UI shows
`rules-v1` with the server-issued local/preview mode. Its same-origin development
proxy preserves the backend origin check. Mobile detail grids are constrained to
the viewport while wide data tables remain locally scrollable.

Checks: integrated `make check` passed (Ruff, mypy, 13 backend tests, TypeScript,
2 Vitest tests, and Vite production build). Playwright against live FastAPI and
the dedicated PostgreSQL branch passed desktop and mobile: 2/2 in 59.9 s. Both
paths asserted no page-level horizontal overflow while exercising fresh-file
import, review/correction, application, reload, CSV export, and reversal.
Commits: `eb386e7`, `e05ea2d`, `a4d18da`, `d30b2d3`. PR: #1.

No ML training, provider benchmark/call, cloud deployment, or human validation
has occurred. Next bounded phase: Phase 3 synthetic dataset generation, sealed
splits, rules comparison, local candidate training, evaluation, and artifact
verification. Do not start it without a new authorization.
