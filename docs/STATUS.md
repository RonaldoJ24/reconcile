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
`a7476ae`, `8654f63`, `600d9e7`, `b194865`. PRs: #5, #6, #7. Phase 3
merged to main as `7758263`.

No provider call, paid benchmark, cloud deployment, sealed evaluation, or human
validation occurred. Next bounded phase: Phase 4 provider adapter and bounded
DeepSeek interpretation, only after explicit authorization and budget/credential
verification.

## Phase 4 — complete

Froze the bounded interpretation contract, verified authenticated access to the
`deepseek-flash` catalog entry and positive existing credit, and recorded the
official 2026-09-14 peak rate card. Implemented strict Direct and Hybrid requests,
exact source-slice citations, whole-candidate selection, deterministic allocation
revalidation, explicit failure states, one bounded retry, cancellation checks,
transactional PostgreSQL quota reservations, usage reconciliation, validated
cache replay/invalidation, and persisted proposal revisions. Interpretation never
applies money. The UI calls it only from explicit Direct/Hybrid actions and labels
live, cached, and unavailable results.

The synthetic development smoke observed eight attempts under one USD 0.05
execution cap. Three initial responses failed citation validation and produced no
revision or application; their complete usage was reconciled as billed failures.
Five later responses were valid. On the final identical-input comparison, rules,
Direct, and Hybrid safely abstained on the original ambiguity; changing the source
to “alphabetically last” produced a validated Direct selection of `INV-B`; removing
evidence made no provider call. Total observed estimated cost was USD 0.002765,
with zero retained unknown-billing reservation and zero applications. These are
synthetic observations, not model-quality or human-validation claims.

Checks: final `make check` passed Ruff, strict mypy, 44 non-PostgreSQL tests,
TypeScript, 6 Vitest tests, and the Vite production build. The isolated Reconcile
Neon branch passed 15/15 PostgreSQL tests in 107.38 s. `make cost-preflight` passed;
unguarded `make smoke-live` and Phase 5 `make perf` remained closed (exit 2).
Live evidence: `reports/llm-v1/live-smoke.json`. Integration commit: `7502876`.
PRs: #9–#12 plus the Phase 4 integration PR.

No deployment, sealed evaluation, autonomous application, paid benchmark, credit
top-up, plan upgrade, or real-world validation occurred. Next bounded phase:
Phase 5 reliability, security, and local performance gates.

## Phase 5 — complete

Frozen release contract, parser/feature/prompt/schema/model identities, routing,
dataset hashes, and candidate commit `2325734`. Added a one-shot sealed-access
ledger, artifact/tracked-secret scan, guarded 1,000-payment/10,000-invoice workload,
bounded database retrieval, consolidated review queries, and deferred nonsecret
credential-rotation checklist.

Hard gates passed: `make check` ran Ruff, strict mypy, 1,046/1,046 offline backend
tests, 6/6 frontend tests, and the production build. The isolated Reconcile Neon
branch passed 42/42 PostgreSQL tests in 254.34 s, including 25/25 synchronized
contested-balance races. Playwright passed desktop/mobile 2/2 in 42.1 s through
fresh import, correction, application, reload, export, and reversal. The release
scan checked 115 tracked text files with zero findings and verified the model digest
and runtime boundary.

The authorized one-time sealed evaluation verified and accessed exactly four frozen
files: 500 final groups and 20 challenge groups. Rules achieved 83.3% precision at
60% coverage on final and 90% at 50% on challenge. The shadow ranker achieved 50%
precision at 100% coverage on both and abstained on none of the underdetermined
cases, so it remains unpromoted. No provider call or cost occurred.

Deterministic matching completed in 0.961 s on Apple M5/arm64. Against remote Neon,
warm list p95 was 599.6 ms/20 samples and detail p95 593.7 ms/100 samples, missing
the proposed 500 ms budgets; hosted, provider, cold-start, and local-PostgreSQL
latency remain unmeasured. Reports: `reports/release-v1/`.

No deployment, publication, human validation, autonomous application, or real-world
accuracy claim occurred. PR: #14. Next bounded phase: Phase 6 zero-new-spend
deployment preflight and owner-authorized Render/Neon deployment.

## Phase 6 — deployed, rules preview verified

Added a single-process hosted profile: same-origin FastAPI/React serving, a
concurrency-one lifecycle consumer that sleeps without database polling, guarded
direct-connection migrations under a PostgreSQL advisory lock, separate pooled
runtime connections, 24-hour lazy preview cleanup, a 300 MiB database admission
stop, and invite-gated provider sessions. Live provider access is disabled. The
minimal multi-stage image excludes tests, datasets, evaluation labels, reports,
local environment files, and credentials. The validated Render Blueprint requests
one Free web service with explicit deploys and no worker, disk, cron, or domain.

Checks: `make check` passed Ruff, strict mypy, 1,047 offline backend tests, six
frontend tests, and the production build. The existing expiring Reconcile Neon
test branch accepted the committed startup migration and passed 44/44 tests through
the pooled endpoint in 262.20 s, including 25 contested-balance races; a final
invite/preview check passed separately. Same-origin desktop/mobile Playwright
passed 2/2 in 1.1 minutes against the production frontend and pooled test endpoint.
The release scan inspected 130 tracked text files with zero findings, and Render
validated the one-action Blueprint. Docker/Podman was unavailable, so the image
itself was not built locally.

The owner verified that the Render workspace has no payment method. The private
GitHub App installation is limited to `RonaldoJ24/reconcile`. The resulting
`reconcile-preview` service (`srv-dakm0t8ae00c73btfpi0`) is one Free Docker web
service in Ohio with automatic deploys off. It serves the same-origin UI and API
at `https://reconcile-preview.onrender.com`. The live deploy
`dep-dakmrjvqj5pc73d45d50` is pinned to main commit `7d371a6`. The Neon main
database is at revision `0004_preview_lifecycle`; its measured size after smoke
was 9,035,776 bytes, below the 300 MiB admission stop.

External health and root checks returned HTTP 200. Hosted Playwright passed
desktop/mobile 2/2 in 38.4 s through fresh import, matching, correction,
application, reload persistence, export, and reversal. A separate isolated API
smoke retrieved all four immutable sources and hashes, confirmed repeated apply
returns the same application ID, exported CSV, reversed the application, and
confirmed both changed-input rejection and provider invite enforcement. Durable
job rows created before and after the final deploy remained in Neon.

The first hosted desktop run exposed a lifecycle-consumer race and the next run
showed that a valid free-tier job could outlast the UI's original polling bound.
Both were fixed and regression-tested in PRs #18 and #19. Image publication fixes
were reviewed in PRs #16 and #17. Provider inference remains disabled, so no
hosted provider call or cost occurred. The public preview deliberately rejects
noncanonical source updates; accepted changed-source behavior remains covered by
local/PostgreSQL tests rather than the public smoke. Hosted memory, cold-start,
and latency distributions were not measured, and no load test was run against the
shared Free service. Evidence: `reports/deployment-v1/`.

A post-deployment clarity pass now states the payment-allocation purpose above
the fold, shows the four-step reviewer workflow, distinguishes public-demo from
private inputs, prefills the synthetic context, and explains incomplete demo
packets before upload. The same actionable guidance is returned by the API.
Frontend unit/type/build checks passed, the targeted PostgreSQL rejection test
passed, and local same-origin desktop/mobile Playwright passed 2/2 in 1.1 minutes.
Hosted desktop/mobile Playwright then passed 2/2 in 36.8 seconds.

Next bounded phase: operate the rules-only preview under Free-tier constraints,
rotate the deferred credentials, and authorize a separate invite-gated provider
preview only if a new key and explicit nonzero inference budget are supplied.
