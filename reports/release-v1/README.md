# Reconcile synthetic release-v1 report

Candidate commit: `2325734dd55d9e6a2f028a46437823ff05ac012e`

Evaluation scope: synthetic, agent-generated, not independently domain validated

## Safety and workflow gates

- 1,000/1,000 deterministic stateful financial sequences preserved payment,
  invoice, credit, application, idempotency, and reversal invariants.
- 25/25 synchronized PostgreSQL contested-balance races produced exactly one
  winner and no overconsumption.
- The complete PostgreSQL suite passed 42/42, including source staleness,
  cross-workspace denial, idempotency/reversal, and expired-lease recovery.
- Offline checks passed 1,046/1,046 backend tests, Ruff, strict mypy, 6/6
  frontend tests, and the production build. Provider failures, invalid output,
  injection-like source content, cancellation, cache invalidation, and budgets
  fail closed within these tests.
- Playwright passed 2/2 desktop/mobile flows through import, review/correction,
  application, reload, export, reversal, and overflow assertions.
- The tracked-secret/artifact scan passed: 115 text files, zero findings, the
  model digest verified, and no forbidden path in the runtime allowlist.

## One-time sealed synthetic evaluation

| Method / split | Groups | Proposals | Precision | Coverage | Underdetermined abstained |
| --- | ---: | ---: | ---: | ---: | ---: |
| Rules / final | 500 | 300 | 83.3% | 60.0% | 200 / 250 |
| Shadow ranker / final | 500 | 500 | 50.0% | 100.0% | 0 / 250 |
| Rules / sealed challenge | 20 | 10 | 90.0% | 50.0% | 9 / 10 |
| Shadow ranker / sealed challenge | 20 | 20 | 50.0% | 100.0% | 0 / 10 |

Candidate retrieval recall was 100% on the 250 answer-bearing final groups and
10 answer-bearing challenge groups for both methods. On those answer-bearing
groups, exact-allocation accuracy was 250/250 for rules and ranker on final, and
9/10 for rules versus 10/10 for the ranker on the challenge. That restricted
metric does not make the ranker safe: it proposed on every underdetermined case.

Rules proposed MXN 2,697,352.00 incorrectly out of MXN 36,563,734.40 final value
(7.38%); the ranker's corresponding value was MXN 19,975,727.50 (54.63%). On the
20-case challenge, rules proposed MXN 19,959.00 incorrectly (1.41% of value) and
the ranker MXN 830,375.90 (58.61%). The ranker therefore remains shadow-only;
rules remain the default, and every financial application still requires explicit
human approval.

The one-time command made zero provider calls, had zero cache hits, and incurred
zero provider cost. `sealed-access.json` records the four verified input/target
paths and hashes. No human semantic review occurred.

## Performance

On an Apple M5/arm64 with Python 3.14.6, with the application local and the test
database on the isolated Neon Free PostgreSQL 17 branch, deterministic rules
matching for 1,000 payments and 10,000 invoices took 0.961 seconds. Database seed
and proposal persistence were excluded from that matching timer; provider calls
were zero.

Warm list p95 was 599.6 ms over 20 samples and warm detail p95 was 593.7 ms over
100 samples, with zero HTTP failures. Both miss the proposed 500 ms target against
the remote test database. Hosted latency, free-service cold starts, provider
latency, and local-PostgreSQL latency remain unmeasured. An initial mis-scoped
per-payment remote-persistence run was stopped at 238/1,000 after about 249 seconds
and is not used as the matching result.

## Release limitations

This evidence does not establish real-world accuracy, accounting correctness under
unseen business policies, human validation, production readiness, hosted behavior,
or autonomous safety. No deployment or publication occurred. The two previously
exposed credentials remain scheduled for end-of-work rotation in
`docs/ROTATION_CHECKLIST.md`; no secret value is stored in this repository.
