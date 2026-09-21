# Portfolio continuation deployment — 2026-09-21

The owner authorized merge and deployment after the draft review chain was ready.
PRs #24–#31 were merged in order using merge commits; each next PR was retargeted
to `main`. The final merge `2c1aacebffe769148c248619ff0afd3c5b4c9d82` has an
identical tree to the accepted `c5a33c2` integration. Published history and the
original worktree edits were preserved.

Render deployment `dep-daonnfg0cd8s73eggacg` reached `live` at
2026-09-21 18:48:21 UTC on the existing `reconcile-preview` service
(`srv-dakm0t8ae00c73btfpi0`). The deployed commit is the final merge above.
The service remains one Free Docker web instance in Ohio, with automatic deploys
off. No resource, billing setting, credential, migration or dependency was changed.
The Docker build includes the complete API tree, packaged historical aggregate
and compiled frontend. Existing Neon-backed state is used by the application.

Public URL: https://reconcile-preview.onrender.com

Root and health returned HTTP 200. A fresh session reported preview mode,
`rules-v2-conservative`, no provider access and interpretation disabled.
No provider call or benchmark was executed.

The bounded hosted Playwright run passed **8/8 in 2.9 minutes**, without skips or
mock transport, across desktop and mobile. It covered opening/resuming all five
cases, immutable source inspection, persisted variants after reload, stale
comparison invalidation, four synthetic validator/transaction checks, duplicate
application identity with unchanged financial effects, persisted comparisons,
packaged historical evaluation, and import/correction/explicit approval/reload/
CSV export/reversal. Page-overflow assertions passed. The raw local log is ignored
under `output/quality-demo/hosted-deployment-20260921.log`.

Current Render service plan and topology were verified through the authenticated
CLI. Account-wide remaining allowances and overage state are not exposed by that
CLI; the owner's 2026-09-15 no-payment-method confirmation remains the available
billing evidence. No current allowance or guaranteed zero bill is claimed.
GitHub Actions was not added or run. The repository remains private.

Native browser 200% zoom, hosted load/cold-start distributions, independent domain
review and new v2 benchmark results are not established by this deployment.
The separate documentation commit records this deployment; it does not change
runtime files or trigger another deployment.
