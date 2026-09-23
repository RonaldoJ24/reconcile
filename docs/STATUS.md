# Status

## Release 1: presentable demo — 2026-09-23

Merged and deployed #39–#43; #38 was closed and superseded by #42.
#39 shares one case-scoped retrieval between matching and interpretation, and
adds folio-fragment recall. Interpretation used to load every open invoice in
the workspace. #40 adds five synthetic Mexican receivables cases and keeps the
original fixtures as the regression group. #41 caches and warms the verified
shadow ranker. #42 makes the UI reviewer-first: case pages show inputs only,
explanations come from reason codes, the detail API returns remittance notes,
there is one AI action, and a remembered engineering view holds the rest.
#43 gives the interpreter explicit decision rules at temperature 0 (prompt v3,
byte bound 9,000).

Checks: ruff, strict mypy, and `uv run pytest -m 'not postgres'` (1,110 passed at
#42).
The PostgreSQL suite ran on the new isolated Neon branch `test-release1`; the
preview's `main` branch was untouched. All 68 passed at #42, in 15m38s, and the
7 interpretation workflow tests passed at #43. Frontend typecheck, 58 tests and
the build passed. Playwright ran against a local preview-mode server on the
test branch: 26 passed and 6 skipped, the skips needing real-backend flags. The
real comparison spec passed. The real five-case walkthrough timed out locally
at about 20 s per case open over the laptop-to-Neon link. Against the hosted
preview (`2c208da`) with the real-backend flags, the full suite then passed 32 of
32 on desktop and mobile, after the two selector fixes in #44.

At `main` `1a632ed` (after #43 and #44), ruff and strict mypy passed, with 1,111
offline tests and 68 PostgreSQL tests passing on `test-release1`.

Deploys: `dep-daq3jslg1s2s73du2f2g` (df2f857) and `dep-daq3nel9fdbs7380b1fg`
(2c208da), which is live. On the hosted preview, the first case's shadow ranker
stage took 200 ms after deploy, against 19,383 ms before (single observations).
Live DeepSeek on 2026-09-23 made five calls, about 6k input and 0.6k output
tokens; the billed cost was not observed. The first call, with prompt v2,
abstained on the abbreviated case. With v3, one call per case:
- abbreviated reference: F-1432 and F-1433, with NC-88 credited to F-1433
- partial payment: F-2207
- ambiguous payment: abstained
- hidden instruction: F-5520

These are development observations, not an evaluation.

Limits: there is no held-out evaluation yet, and the interpreter cannot cite the
bank reference. Next: the owner writes the held-out realistic cases, then the
v2 protocol runs once within the approved US$2 cap. The repository was made
public after a clean secret scan: the release scan found nothing in 175 tracked
text files, and a pattern scan found nothing in 155 revisions. The landing page
is live at https://ronaldoj24.github.io/reconcile/.

## Interpretation review clarity — 2026-09-22

The allocation-detail sidebar now starts level with the decision summary on desktop
without pushing down the payment, and retains the sequential mobile order. During
interpretation, the page remains visible and shows the latest observed workflow
stage; after the stream, it waits for the saved proposal refresh before showing
the result. The lasting result summarizes the server status/revision, allocation,
actual citations or saved proposal evidence, reason, and next reviewer action.
Refresh failures ask the reviewer to verify the saved proposal and do not claim
stale allocation lines as the new result. Fixed reason codes have plain-language
explanations. No backend, financial behavior, provider budget, or hosting setting
changed.

Commits `1fe3116` and `0ce1f7f` on `fix/interpretation-clarity`. Frontend
typecheck, 51 tests, production build, and diff checks passed. Local browser
verification at 1280px measured decision and sidebar both at 294px, with payment
still at 587px; at 390px the page had no horizontal overflow. Live deployment
and a provider call were not part of this local acceptance.

## Portfolio continuation — active, 2026-09-16

Worktree `case-study`, branch `feat/reconcile-case-study`, based on `dd18e1c`.
Original package/partial frontend worktrees are preserved. Reconciled scope and
evaluation audit: `docs/ENHANCEMENT_AUDIT.md`; frozen continuation/v2 protocol:
`contracts/PORTFOLIO_V2.md`, commit `7bd57ed`. Existing historical phases below are
historical records, not checks rerun for this continuation.

Current sequence: financial UI prerequisites → conservative rules/source validity →
real versioned cases, capabilities and persisted trace → exact-input comparison,
recording validation/reliability → v2 harness and independent-review packet → final
browser/integration acceptance and PR. No provider calls, sealed evaluation,
retraining, promotion, merge or deployment.

This run: `make lint typecheck test` passed, 1,047 offline backend tests and 45
PostgreSQL tests deselected (16.21 s). Direct reproduction confirms negated English
and Spanish references incorrectly propose, while `101.` is missed. The existing
Neon `test-phases-0-2` branch is available, so no new infrastructure is necessary;
`make test-integration` passed 45/45 PostgreSQL tests (230.91 s), including
contested-balance races. Financial frontend corrections were accepted in
`56b6fb1`: typecheck/build, 16 frontend tests, and 12 desktop/mobile Playwright
checks passed. The real PostgreSQL browser flow verifies that typed `100` and
filled `100.00` both persist as 10,000 centavos, then confirms apply, reload,
export, reversal and balances. Transport mocks separately exercise stale validation,
unsaved edits, uncertain retries and save locking. Screenshots are local under
`output/quality-demo/`; case-first UI work is still in progress.

The 24-case blinded synthetic domain-review packet is committed in `65bec35`.
Blank responses, provisional author labels and adjudication instructions are
separate. No independent review has occurred.

Backend integrity was accepted in `c668a7f`: strict JSON centavo integers,
conservative reference handling, committed-source filtering, changed-message
invalidation and rerun scheduling, immutable applied/reversed history, and narrower
invoice/credit locks. Unsupported follow-up clauses and ambiguous `No invoice 101`
references defer. Primary acceptance ran `make lint typecheck test`: Ruff and strict
mypy passed, with 1,065 offline tests passing and 49 PostgreSQL checks deselected.

Targeted PostgreSQL acceptance passed 30/30 in 258.57 s on the existing test branch:
new evidence lifecycle, changed-context rejection, 25 contested-balance races, and
an independent allocation completing while an unrelated invoice row remained locked.
The run used the direct endpoint with a bounded connection timeout. An earlier
pooled attempt was interrupted during connection establishment before a test
completed; read-only activity checks found no blocked SQL transaction.
No application-provider inference was called.

Canonical frontend DTOs and the case/trace interface are accepted locally (`b56313a`);
real case endpoint acceptance remains in progress. Draft PRs #24–#27 form the
verified dependency chain described in `docs/PR_SEQUENCE.md`. No PR was merged.

## Quality/demo correction — Phase 0 baseline

Reference and clean correction branch are fixed at
`c19034dd9e3644c4151805cfa257a3085f37cd97`. The original worktree's generated
`reports/release-v1/scan.json` change is preserved outside this branch. The
requested correction prompt was supplied externally because it is not present in
`docs/` at the reference commit.

Baseline `make check` passed: Ruff, strict mypy, 1,047 non-PostgreSQL tests, eight
frontend tests, TypeScript, and the production build. Inspection confirms the
reported gaps remain: editable strings are reinterpreted as centavos, apply ignores
unsaved drafts, validation lacks input-generation ownership, mentions are merged
without negation/provenance, correction/application lock whole-workspace rows, the
preview has no one-click real demo, API types are permissive, and no root README or
evaluation view exists.

Frozen order: (1) money/review/import-snapshot integrity; (2) conservative
`rules-v2-conservative`, canonical API contracts, and targeted PostgreSQL locks;
(3) backend-owned sample workspaces plus decision-oriented routed UI and
capabilities; (4) sanitized evaluation view, root README, real-backend Playwright,
captures, full checks, and PR. DeepSeek stays disabled; no deployment, paid test,
credential change, model promotion, retraining, or reserved-evaluation access.

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

## Case lifecycle acceptance — 2026-09-16

Registered cases and immutable traces integrated at `b37a879`. Backend targeted
checks: Ruff/mypy, 42 offline tests, seven PostgreSQL case/lifecycle tests plus one
concurrent import/apply regression. Real Playwright: two case journeys and two
financial workflows passed across desktop/mobile; four viewport widths and keyboard
skip navigation checked. Evidence: `reports/portfolio-v2/engineering-validation.md`.
Comparison/evaluation and controlled variants/reliability remain in progress.

## Comparison and evaluation acceptance — 2026-09-16

The comparison API executes rules and the unchanged local ranker over the saved
input fingerprint and persists review-only observations. Provider registries remain
empty, so Direct/Hybrid are unavailable. The frontend discards stale comparisons
and serves the packaged historical evaluation after session initialization.
Focused backend, PostgreSQL, frontend and real desktop/mobile checks passed; exact
scope and counts are in `reports/portfolio-v2/engineering-validation.md`.
Controlled variants and reliability remain in progress. No deployment or benchmark
was executed.


## Controlled variants and final review sequence — 2026-09-21

Controlled message variants preserve source/decision history and invalidate stale
comparisons. The synthetic lab invokes actual validators and transaction guards;
duplicate checks reuse an explicitly approved operation without new financial rows.
The interface, immutable-history controls and review sequence are implemented.

Final checks: Ruff/mypy, 1,089 offline backend tests, 62 PostgreSQL tests, 42 frontend
unit tests, TypeScript and production build passed. All 32 distinct desktop/mobile
browser scenarios passed across the integrated run (30) and focused import rerun
(2); the initial import selectors and total timeout were corrected in the tests.
Native browser 200% zoom remains unverified. Exact evidence and limits are in
`reports/portfolio-v2/engineering-validation.md`.

The eight private draft PRs #24–#31 follow `docs/PR_SEQUENCE.md`. No merge,
deployment, provider inference, fresh benchmark or model promotion occurred.
Independent domain review remains pending; prepared synthetic packets are not
external validation.


## Authorized merge and hosted acceptance — 2026-09-21

The owner subsequently authorized merge and deployment. PRs #24–#31 were merged
in order into `main`; final code merge `2c1aace` exactly matches the tested tree.
Render deployment `dep-daonnfg0cd8s73eggacg` is live at
https://reconcile-preview.onrender.com on the existing single Free web service.
Root/health returned 200; the active engine is `rules-v2-conservative` with provider
access disabled. Eight real hosted desktop/mobile journeys passed in 2.9 minutes,
covering cases, variants, comparison, integrity checks and the complete financial
review workflow. No new resource, billing change, provider call or benchmark ran.
See `reports/deployment-v2/` for deploy identity, checks and cost-observability limits.


## Public DeepSeek access acceptance — 2026-09-21

The owner authorized public Direct/Hybrid use with shared spending ceilings of
USD 0.50 per UTC day and USD 5 per UTC month. Public access is a reversible
preview flag evaluated on every request; existing sessions gain access without
persisting an invitation grant. The DeepSeek credential remains server side.
Public execution reservations use a stable prefix plus UTC day, preserving the
global day/month counters and existing session, attempt, token and concurrency
limits. Interpretation still requires manual approval before any allocation applies.

Validation: 1,102 offline tests, five focused PostgreSQL public-access and budget
reservation tests, Ruff and mypy (44 source files) passed. Checks ran locally
against the dedicated test database, with provider responses stubbed for the
PostgreSQL route tests. No new benchmark or model promotion occurred. Hosted
provider verification follows the code deployment. Operational configuration is
documented in `PUBLIC_PROVIDER_ACCESS.md`.

## Visual hierarchy acceptance — 2026-09-22

Based on `a3c2779`, allocation detail now groups payment, cash, credit and evidence,
keeps current balances distinct, and places mobile reviewer controls before the
diagnostic sections. Proposal identifiers are secondary; source content precedes
expandable provenance. Proposed/review/applied states use blue/amber/green with
explicit labels. Evaluation separates the current engine from historical splits
and presents labeled metric cards on mobile. Existing branding, financial handlers,
provider limits, historical metrics and caveats are preserved.

Validation: `pnpm test -- --run` (6 files, 42 tests), `pnpm typecheck`, `pnpm build`
and `git diff --check` passed. Actual browser checks covered 1440px desktop, 390px
mobile and 320px overflow checks, including below-fold evidence, reviewer controls,
source provenance, Evaluation, Cases, Imports and queue states. Opening/canceling
an unsaved correction preserved the existing disabled-action guards. Screenshots
were retained in the owner's visual-review output directory. This was a visual
acceptance pass, not a new financial end-to-end suite or live-provider benchmark.
The owner authorized deployment to the existing Render Free preview; deployment
identity and hosted verification are recorded on the associated pull request.

## Flow audit and interpretation progress — 2026-09-22

Based on `2370497`, Direct/Hybrid now stream observed workflow stages and elapsed
time, clear obsolete results on a new attempt, and abort/discard obsolete work
when leaving a proposal. New revisions retain live/cache provenance and completed
stages in the existing decision history. DeepSeek eligibility, case opening and
import completion have explicit status text. A structured CSRF rejection refreshes
the session and retries once, resolving the reproduced cross-tab failure.

Prompt v2 omits redundant model-visible bookkeeping while preserving candidates,
amounts and citations; the full request still controls validation and cache identity.
The nine-invoice regression now measures 6,102 bytes Direct / 6,847 Hybrid under
the unchanged 8,000-byte ceiling. Spending caps and explicit financial approval
remain unchanged. Cancellation preserves usage accounting and cannot guarantee
an already-running provider HTTP request stops.

Validation: 1,104 offline backend tests, nine prompt/provider tests, six workflow
PostgreSQL tests, two route/auth PostgreSQL tests, 47 frontend tests, Ruff, mypy,
TypeScript and production build. Actual desktop/mobile inspection covered streamed
stub progress, larger-session Direct/Hybrid, cache replay, persisted traces,
navigation cancellation, imports, correction, approval/reversal and cross-tab
recovery. The final trace normalization passed all six workflow PostgreSQL tests
in 121.13 seconds, plus Ruff/mypy/diff checks. Deployment follows this acceptance;
exact evidence and the subsequent deployment are recorded in
`reports/portfolio-v2/flow-audit-2026-09-22.md` and the associated pull request.

## Demo story and reviewer view — 2026-09-22

The Cases entry point now walks through the registered bundle as a payment
decision: the tempting exact-amount invoice, the message-supported split, the
linked credit, and the required human approval. The allocation detail repeats
that story only when the persisted case, amounts, lines, and source evidence
match the registered original; changed variants and corrections use the actual
server result. Unresolved cases explain why no allocation was proposed and
omit empty cash, credit, evidence, and alternative panels. Desktop actions begin
beside the decision; mobile actions follow the core evidence. A resolved proposal
does not show an inactive DeepSeek panel. The preview is labeled synthetic.

After a DeepSeek request, the saved result stays in the top right panel instead
of moving below the reviewer controls. Resolved results show the saved decision,
allocation, evidence, and next action in place of inactive mode buttons. The
observed workflow steps remain available in an expandable record.

Validation: 59 frontend tests, TypeScript typecheck, production build, and diff
whitespace checks passed. Actual browser inspection covered the bundle and
insufficient cases at desktop and mobile sizes. This changes presentation only;
financial rules, provider limits, and application guards are unchanged. It does
not claim production matching accuracy or customer savings. The branch is ready
for review and has not been deployed to the hosted preview.
