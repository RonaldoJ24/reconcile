# Portfolio continuation engineering evidence

Development started 2026-09-16; final checks resumed 2026-09-21.
Status: **implementation and draft review sequence complete**, with verification
limits below. This report records development checks,
not benchmark or production results. Existing v1 reports and the model artifact
are preserved. No provider inference, reserved-final evaluation, deployment,
training or promotion was executed for this continuation.

## Environment and boundaries

- Private repository `RonaldoJ24/reconcile`, integration branch
  `feat/reconcile-case-study`, initial base `dd18e1c`.
- Existing Neon branch `test-phases-0-2`; no new infrastructure. PostgreSQL tests
  use their guarded isolated schema. The local browser API uses this test database.
- Original package and partial frontend worktrees retain their pre-existing edits.
- Provider inference is explicitly disabled. Frontend transport fixtures cannot
  establish real provider provenance or database persistence.

## Accepted checks

| Scope | Evidence | Result |
| --- | --- | --- |
| Initial backend baseline | `make lint typecheck test` | Ruff/mypy; 1,047 offline tests passed; 45 PostgreSQL tests deselected |
| Initial PostgreSQL baseline | `make test-integration` | 45 passed in 230.91 s |
| Financial UI | TypeScript, production build, Vitest | 16 tests passed |
| Financial browser flow | Desktop/mobile real API plus transport regressions | 12 Playwright tests passed |
| Conservative rules and source/transaction integrity | `make lint typecheck test` after `c668a7f` | Ruff/mypy; 1,065 offline tests passed; 49 PostgreSQL tests deselected |
| Targeted PostgreSQL integrity | Changed-message lifecycle, context rejection, 25 contested races, unrelated-row lock | 30 passed, 14 deselected, 258.57 s |
| Case-first frontend | TypeScript/build and Vitest | 22 tests passed |
| Case and financial transport regressions | Mock Playwright desktop/mobile | Initial 12 passed; not real case-endpoint acceptance |
| Registered cases, immutable trace and source bindings | Ruff, mypy, targeted offline tests | 42 passed |
| Persisted case lifecycle and trace | Seven PostgreSQL API/lifecycle tests, plus separately rerun concurrent import/apply test | 7 + 1 passed; concurrent test 113.83 s |
| Case UI follow-up | App unit tests; TypeScript/build; case transport tests | 20 App tests; 4 mocked browser tests passed, 2 real tests intentionally skipped in mock run |
| Real case browser lifecycle | `E2E_REAL_CASES=1` Playwright case test, desktop/mobile | 2 passed (1.3 / 1.4 min): five cases each, resume, persisted sources and trace |
| Real financial browser regression after case integration | Playwright `reconcile.spec.ts`, desktop/mobile | 2 passed (36.8 / 37.0 s): correction, confirmation, apply, reload, export and reversal |
| Responsive/keyboard review | Real page at 390, 768, 1280 and 1440 px; skip link | No page overflow; skip link focuses main content |

The real financial browser test enters `100` character by character and also fills
`100.00`, observes 10,000 centavos in the submitted and persisted allocation, then
checks explicit confirmation, apply, reload, CSV export, reversal and balances.
Transport regressions cover dirty drafts, invalid edits, explicit discard, delayed
validation generations, disabled controls during save and uncertain apply retries
with the same idempotency key.

Targeted PostgreSQL acceptance uses the direct endpoint with a bounded connection
timeout. An earlier pooled attempt was interrupted during connection establishment
before a test completed. Read-only database activity showed no blocked transaction;
this is not a measured connection-latency distribution or service-level result.

## Local visual artifacts

Accepted financial workflow captures are in `output/quality-demo/`, including
`confirmation-desktop.png`, `confirmation-mobile.png`, `applied-desktop.png` and
`applied-mobile.png`. Primary review inspected mobile confirmation and desktop
applied state. Case layout captures `mock-case-desktop.png` and
`mock-case-mobile.png` use explicitly mocked API responses. Primary review inspected
both. Their recorded-provider labels are fixture data, **not** evidence of a
provider execution or eligible runtime recording.

Real backend captures are `real-case-list-{desktop,mobile}.png`,
`real-bundle-trace-{desktop,mobile}.png` and
`real-bundle-source-{desktop,mobile}.png` in the same directory. Primary review
inspected the desktop trace, mobile case list and mobile source capture.
Screenshots and generated browser outputs are ignored working artifacts.
Browser-native 200% zoom remains unverified: Chromium headless did not respond
to the zoom shortcut; device scaling is not counted as browser zoom.

## Accepted comparison backend checks

Rules and the unchanged local ranker execute over the verified saved snapshot.
The production provider recording registry and trusted digest sets remain empty.
Ruff and mypy passed; 40 focused offline checks passed before the final small
empty-citation deferral correction. The five comparison module tests then passed,
including Direct/Hybrid proposal and deferral verification with synthetic trust
fixtures. PostgreSQL: seven focused tests passed, followed by two endpoint tests
on the final backend implementation. No financial effects or provider calls occur
when comparing. See `docs/COMPARISON_AND_REPLAY.md` for scope and timing boundaries.

## Accepted comparison and evaluation interface

Frontend unit tests: 34 passed; TypeScript and production build passed. Mocked
Playwright checks: six passed across desktop/mobile, including a delayed comparison
after editing and evaluation reached before session initialization. Real PostgreSQL
API browser checks: two passed across desktop/mobile, observing five methods, saved
comparison after reload, and four packaged historical rows with provenance.
Primary review inspected the real desktop comparison and mobile evaluation captures.
An observed mobile navigation overflow was corrected with a two-column layout.
The final real browser check also asserts no page overflow (2 passed, 39.2 s).

## Accepted controlled-variant and reliability backend

Ruff, mypy and 15 focused offline tests passed. Eight PostgreSQL case/lab tests
passed in 271.63 s after the final backend correction: source reactivation and
version changes, immediate stale-comparison rejection, session/CSRF isolation,
applied/reversed history guards, actual validator failures, same-key application
identity, unchanged financial rows, and injected rollback after import processing.
An initial connection attempt failed during fixture setup before product assertions;
a read-only direct connection succeeded before the focused retry.

## Accepted v2 harness checks

`backend/tests/test_ml_evaluate_v2.py`: 15 tests passed. Ruff and mypy passed for
the changed module. These checks cover complete joins, allocation support, grouped
lineage, threshold selection and manifest binding, missing measurements, provider
unavailability and the guarded one-time final-access mechanism using temporary toy
files. No reserved project data or new benchmark was executed. The harness consumes
observations; it does not execute or authenticate provider methods.

## Research evidence and limitations

The sanitized historical aggregate retains the source report's SHA-256 and exact
method/split counts. It does not re-evaluate sealed labels or recast historical
`rules-v1` results as measurements of `rules-v2-conservative`.

The 24-case independent-review packet is synthetic, author-labeled and not yet
reviewed by an external domain specialist. Its existence does not validate labels.
V2 harness tests test evaluation logic; they are not v2 benchmark
results. Raw ranker scores are not calibrated confidence. Financial and exact
citation-location checks do not establish semantic entailment.

## Integrated checks — 2026-09-21

`make lint typecheck test` passed: Ruff, mypy and 1,089 offline backend tests.
The separate complete PostgreSQL suite passed 62/62 tests in 831.86 s,
including the unchanged-variant resume regression at `138038f`. Historical reports, sealed data and the ranker artifact have no diff
against `origin/main` (`c19034d`). Existing original-worktree edits are preserved.

## Controlled-variant and reliability interface — 2026-09-21

TypeScript, production build and all 42 frontend unit tests passed. The integrated
same-origin Playwright run used the production frontend and real PostgreSQL API
in preview mode, with both real-case flags enabled and provider inference disabled.
It passed 30 checks in 10.2 minutes. The two import workflows stopped at an
ambiguous revision-text selector because the new lab also shows the revision;
the selector was scoped to the page heading for a focused desktop/mobile rerun.
The first focused rerun passed that assertion and reached reversal but exhausted
the 90-second whole-test budget. Trace requests showed database-backed reads
taking up to 14.5 seconds. The workflow budget was raised to 180 seconds; individual
assertion timeouts and product code were unchanged.

Both real case-lab journeys passed. They record a comparison before changing
variants and verify its invalidation, reload the persisted prompt-like variant,
restore the original message, run invalid allocation/citation/stale checks, obtain
explicit approval, check duplicate identity and exact unchanged financial effects,
and reverse through the ordinary reviewer action. The duplicate result contains
one application group, two cash rows and one credit row, totaling 5,400,000 cash
centavos and 100,000 credit centavos both before and after the check.

Primary visual inspection covered the real desktop trace and desktop/mobile
synthetic duplicate-result panels. Browser checks assert no horizontal page overflow.
Native browser zoom at 200% remains unverified; viewport/device scaling is not
reported as native zoom verification. Captures and raw logs are local ignored
artifacts under `output/quality-demo/`.

## Final acceptance and review handoff

The final focused import rerun passed 2/2 desktop/mobile checks in 4.4 minutes
(about 2.2 minutes per workflow), including persisted corrections, explicit
confirmation, application, reload, CSV export and reversal. Together with the
30 accepted checks from the integrated run, all 32 distinct browser scenarios
have passed: 24 mocked transport checks and eight real PostgreSQL/API journeys.
This is aggregate coverage across the full run and focused rerun, not a claim
that the initial 32-test invocation was entirely green. No product source changed
after that integrated run; only the two import selectors and whole-test budget did.

Implementation and test boundary: `914bbe9`; interface implementation: `085130a`.
The separate `scan.json` records the implementation/test boundary and scans the
tracked working files. It passed with zero secret findings, 21 allowed runtime
entries and the unchanged verified model SHA-256
`bb9774c99870bfa0d20759c6ceb37aafd6fbf65619b28c71947bdb993c16135e`.
The following documentation commit carries this report, scan and final PR sequence.

Eight dependent draft PRs (#24–#31) form the review sequence in the private
repository; see `docs/PR_SEQUENCE.md`. Checks above were run locally; no GitHub CI
result is claimed. No merge or deployment occurred. Native 200% zoom, independent
domain validation and a new v2 benchmark remain unverified or outside this run's
authorization. Provider inference, training and model promotion remain disabled.
