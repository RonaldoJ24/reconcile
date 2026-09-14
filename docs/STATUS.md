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
CSRF/origin checks, and synthetic-only preview admission are enforced. Generated
properties exercise exact-centavo conservation and isolation between unrelated
payments; PostgreSQL tests cover both contested and independent balances.

Checks: `make check` passed (`ruff`, `mypy`, 15 unit/property/contract tests; 7
PostgreSQL tests deselected); the guarded Neon run passed 7/7 PostgreSQL tests in
48.38 s. Commits: `b35d12a`, `73db264`, `0c69c2b`, `f94397e`, `1bf8189`.
PRs: #2, #3.

## Phase 2 — complete

Implemented the Imports, Review Queue, and Allocation Detail screens against the
real API, including row-level validation feedback, job execution, evidence/source
links, alternative and balance display, reviewer corrections, explicit apply and
reverse actions, reload persistence, and application-history export. The UI shows
`rules-v1` with the server-issued local/preview mode. Its same-origin development
proxy preserves the backend origin check. Mobile detail grids are constrained to
the viewport while wide data tables remain locally scrollable.

Checks: integrated `make check` passed (Ruff, mypy, 15 backend tests, TypeScript,
2 Vitest tests, and Vite production build). Playwright against live FastAPI and
the dedicated PostgreSQL branch passed desktop and mobile: 2/2 in 59.9 s. Both
paths asserted no page-level horizontal overflow while exercising fresh-file
import, review/correction, application, reload, CSV export, and reversal.
Commits: `eb386e7`, `e05ea2d`, `a4d18da`, `d30b2d3`. PR: #1.

## Phase 3 — complete

Generated and froze 5,000 deterministic synthetic payment groups plus a 30-case
development and 20-case sealed challenge suite, with separate input/target files,
split-disjoint entities/templates, manifests, and SHA-256 digests. Final and sealed
challenge targets were not evaluated. Labels remain agent-generated and not
independently domain-validated.

Trained exactly two bounded scikit-learn candidates and selected the simpler
logistic model after a validation tie. The verified `ranker-ml-v1-logistic` v1
artifact has model digest `bb9774c99870bfa0d20759c6ceb37aafd6fbf65619b28c71947bdb993c16135e`.
On 1,000 validation groups, the learned ranker proposed 1,000 at 0.500 precision;
rules v1 proposed 500 at 0.800 precision. The model is not promoted. Opt-in shadow
mode records an observed model ID/version, raw score, latency, truncation, and
candidate ranking in PostgreSQL without changing the rules proposal or any
financial operation. The existing UI exposes this trace.

Checks: `make data-dev` reproduced all frozen files without a diff; `make train`
reproduced the model digest; `make evaluate-dev` passed; `make check` passed Ruff,
mypy, 31 non-PostgreSQL tests, TypeScript, 2 Vitest tests, and the Vite build. The
Alembic 0002 upgrade and 8/8 PostgreSQL tests passed on the isolated Reconcile test
branch. `make evaluate-release` remained guarded (exit 2). Commits: `eac20a7`,
`a7476ae`, `8654f63`, `600d9e7`, `b194865`. PRs: #5, #6; final integration PR
pending.

No provider call, paid benchmark, cloud deployment, sealed evaluation, or human
validation occurred. Next bounded phase: Phase 4 provider adapter and bounded
DeepSeek interpretation, only after explicit authorization and budget/credential
verification.
