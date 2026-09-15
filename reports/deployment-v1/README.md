# Phase 6 deployment evidence

The owner confirmed that the Render workspace has no payment method, and the
GitHub App installation was restricted to the private `RonaldoJ24/reconcile`
repository. The deployment uses one Render Free Docker web service, explicit
deploys only, no worker, disk, cron, custom domain, or paid resource. Its only
secrets are separate pooled runtime and direct migration database connections.
Live provider access is off; the exposed DeepSeek key was never installed.

`reconcile-preview` (`srv-dakm0t8ae00c73btfpi0`) runs in Ohio at
`https://reconcile-preview.onrender.com`. Automatic deploys are off. Deploy
`dep-dakmdmjl550s73fnjbbg` built the container and reached `live` from main commit
`3ef812aaa7eea4367956b9c49c9d788d98038aca`. Health and root returned HTTP 200.
The dedicated Neon Free main branch reached Alembic revision
`0004_preview_lifecycle`; after smoke its database measured 9,035,776 bytes,
well below the conservative 300 MiB application stop.

Hosted Playwright passed desktop and mobile, 2/2 in 38.4 seconds. Each path used
a fresh preview session and exercised validation, commit, deterministic matching,
reviewer correction, explicit application, reload persistence, CSV export, and
reversal. The desktop path took 30.9 seconds and mobile 6.8 seconds. A separate
fresh-session API smoke retrieved all four immutable sources and their hashes,
confirmed repeated apply returned the same application ID, exported CSV, and
reversed the application. Rows created before and after the final deploy remained
in Neon, demonstrating deployment-independent state.

The public preview accepted only the bundled synthetic packet. A changed packet
was rejected with HTTP 403 as designed, so accepted changed-source behavior was
not exercised externally. A Direct interpretation attempt also returned HTTP 403
at the invite gate; no provider request, token usage, or inference cost occurred.
Hosted memory, cold-start, and latency distributions remain unmeasured, and no
load test was sent to the shared Free service.

The first deploy failed because the configured uv image tag was unpublished; PR
#16 selected a published image. The next startup exposed an omitted runtime
configuration module; PR #17 included it. A hosted desktop smoke then exposed a
race between the lifecycle consumer and synchronous run-once endpoint; PR #18
made the active workspace job observable. A 22.5-second valid job then exceeded
the original UI polling bound; PR #19 extended the bound and made exhaustion an
explicit error. The final deploy and hosted smoke passed after these fixes.

GitHub Actions remains unused because its no-overage boundary was not exposed by
the authenticated API. Local, isolated-Neon, and hosted checks are recorded
instead. Deferred credential work remains in `docs/ROTATION_CHECKLIST.md`.
