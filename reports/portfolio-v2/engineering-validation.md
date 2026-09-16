# Portfolio continuation engineering evidence

Date: 2026-09-16. Status: **in progress**. This report records development checks,
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
| Case and financial transport regressions | Mock Playwright desktop/mobile | 12 passed; not real case-endpoint acceptance |

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

Screenshots and generated browser outputs are ignored working artifacts. This
report does not present mocked cases as persisted real case runs.

## Research evidence and limitations

The sanitized historical aggregate retains the source report's SHA-256 and exact
method/split counts. It does not re-evaluate sealed labels or recast historical
`rules-v1` results as measurements of `rules-v2-conservative`.

The 24-case independent-review packet is synthetic, author-labeled and not yet
reviewed by an external domain specialist. Its existence does not validate labels.
V2 harness tests, when completed, test evaluation logic; they are not v2 benchmark
results. Raw ranker scores are not calibrated confidence. Financial and exact
citation-location checks do not establish semantic entailment.

## Remaining acceptance

Real case lifecycle/trace integration; same-input comparison and artifact rejection;
controlled evidence variants and reliability experiments; historical evaluation UI;
v2 harness review; final integrated backend, PostgreSQL and browser verification;
final release-boundary scan and private PR. Replace this section with observed
results as those checks complete.
